"""
Peer Random Forest — Module N current predicted from peer module currents only.
Writes ml_predictions with tag model=peer_rf (separate from multivariate exporter bands).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from bridge import (
        BACKFILL_CHUNK_HOURS,
        FailedMachineTracker,
        INTER_MACHINE_SLEEP,
        ML_ERRORS,
        ML_MODEL_CONFIG,
        ML_WINDOW_DAYS,
        TIME_COLUMN,
        build_matrices,
        calculate_bounds,
        clean_influx_csv,
        fetch_influx_data,
        fetch_machine_list,
        fetch_single_machine_data,
        fit_model,
        get_memory_usage_mb,
        has_sufficient_data,
        prepare_wide_df,
        publish_predictions,
        release_memory,
        validate_labels,
        write_predictions_batch_to_influx,
    )

PEER_RF_INFLUX_MODEL = os.environ.get("PEER_RF_INFLUX_MODEL", "peer_rf")
def _default_peer_rf_config_path() -> str:
    for candidate in (
        os.environ.get("PEER_RF_CONFIG_PATH", "").strip(),
        "/app/peer_rf_config.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer_rf_config.json"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer_rf_config.json")


PEER_RF_CONFIG_PATH = _default_peer_rf_config_path()
INFLUX_BATCH_SIZE = int(os.environ.get("PEER_RF_INFLUX_BATCH_SIZE", "400"))


@dataclass(frozen=True)
class PeerRfMachineConfig:
    target: str
    peer_features: list[str]


@dataclass(frozen=True)
class TrainingPlan:
    targets: list[str]
    features: list[str]
    excluded: list[str]
    influx_model: str | None
    publish_prometheus: bool


_PEER_RF_CACHE: dict[str, PeerRfMachineConfig] | None = None


def load_peer_rf_machines() -> dict[str, PeerRfMachineConfig]:
    global _PEER_RF_CACHE
    if _PEER_RF_CACHE is not None:
        return _PEER_RF_CACHE
    path = PEER_RF_CONFIG_PATH
    if not os.path.isfile(path):
        _PEER_RF_CACHE = {}
        return _PEER_RF_CACHE
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        machines = raw.get("machines") or {}
        out: dict[str, PeerRfMachineConfig] = {}
        for machine_id, cfg in machines.items():
            if not isinstance(cfg, dict):
                continue
            target = str(cfg.get("target", "")).strip()
            peers = cfg.get("peer_features") or []
            if not target or not isinstance(peers, list):
                continue
            out[str(machine_id).strip()] = PeerRfMachineConfig(
                target=target,
                peer_features=[str(p).strip() for p in peers if str(p).strip()],
            )
        _PEER_RF_CACHE = out
        print(f"[PEER-RF] Loaded config for {len(out)} machine(s) from {path}")
        return _PEER_RF_CACHE
    except Exception as e:
        print(f"[PEER-RF] Failed to load {path}: {e}")
        _PEER_RF_CACHE = {}
        return _PEER_RF_CACHE


def merge_field_roles_from_peer_config() -> dict[str, str]:
    roles: dict[str, str] = {}
    for cfg in load_peer_rf_machines().values():
        roles[cfg.target] = "target"
        for feat in cfg.peer_features:
            roles[feat] = "feature"
    return roles


def get_peer_rf_plan(machine_str: str, wide_df: pd.DataFrame) -> TrainingPlan | None:
    cfg = load_peer_rf_machines().get(machine_str)
    if not cfg:
        return None
    time_cols = {"hour", "minute"}
    available = set(wide_df.columns)
    if cfg.target not in available:
        print(f"[PEER-RF] {machine_str}: target {cfg.target} not in wide_df")
        return None
    features = [f for f in cfg.peer_features if f in available and f != cfg.target]
    for tc in time_cols:
        if tc in available and tc not in features:
            features.append(tc)
    if not features:
        print(f"[PEER-RF] {machine_str}: no peer feature columns available")
        return None
    excluded = [c for c in wide_df.columns if c not in features and c not in {cfg.target} and c not in time_cols]
    return TrainingPlan(
        targets=[cfg.target],
        features=features,
        excluded=excluded,
        influx_model=PEER_RF_INFLUX_MODEL,
        publish_prometheus=False,
    )


def calculate_bounds_series(
    model: Any,
    X: np.ndarray,
    Y: np.ndarray,
    std_multiplier: float,
    min_std_dev: float,
    sample_residual_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    preds = model.predict(X)
    if preds.ndim == 1:
        preds = preds.reshape(-1, 1)
    sample_size = min(sample_residual_size, len(X))
    sample_preds = preds[-sample_size:]
    residuals = Y[-sample_size:] - sample_preds
    std_deviations = np.std(residuals, axis=0)
    std_deviations = np.maximum(std_deviations, min_std_dev)
    lower_bounds = preds - (std_multiplier * std_deviations)
    upper_bounds = preds + (std_multiplier * std_deviations)
    return preds, lower_bounds, upper_bounds


def publish_backfill_series(
    machine_str: str,
    target_fields: list[str],
    timestamps_ms: list[int],
    predictions: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    influx_model: str,
    write_batch_to_influx: Any,
) -> int:
    if len(timestamps_ms) == 0:
        return 0
    n = len(timestamps_ms)
    points: list[dict] = []
    written = 0
    for i in range(n):
        for idx, target in enumerate(target_fields):
            try:
                if predictions.shape[1] > 1:
                    y_pred = float(predictions[i, idx])
                    lower_val = float(lower_bounds[i, idx])
                    upper_val = float(upper_bounds[i, idx])
                else:
                    y_pred = float(predictions[i, 0])
                    lower_val = float(lower_bounds[i, 0])
                    upper_val = float(upper_bounds[i, 0])
            except (IndexError, ValueError):
                continue
            if np.isnan(y_pred) or np.isinf(y_pred):
                continue
            points.append({
                "machine": machine_str,
                "field": target,
                "model": influx_model,
                "expected": y_pred,
                "upper": upper_val,
                "lower": lower_val,
                "timestamp_ms": timestamps_ms[i],
            })
    for batch_start in range(0, len(points), INFLUX_BATCH_SIZE):
        batch = points[batch_start : batch_start + INFLUX_BATCH_SIZE]
        if write_batch_to_influx(batch, batch[0]["timestamp_ms"]):
            written += len(batch)
    return written


def train_peer_rf_machine(
    machine_str: str,
    df: pd.DataFrame,
    n_estimators: int,
    max_depth: int,
    timestamp_ms: int | None,
    *,
    publish_fn: Any,
    write_batch_fn: Any,
    build_matrices_fn: Any,
    fit_model_fn: Any,
    calculate_bounds_fn: Any,
    prepare_wide_fn: Any,
    std_multiplier: float,
    min_std_dev: float,
    sample_residual_size: int,
    write_series: bool = False,
) -> int:
    wide_df = prepare_wide_fn(df)
    if wide_df is None:
        return 0
    plan = get_peer_rf_plan(machine_str, wide_df)
    if plan is None:
        return 0
    X, Y = build_matrices_fn(wide_df, plan.targets, plan.features)
    if X is None or Y is None:
        return 0
    model = fit_model_fn(X, Y, n_estimators, max_depth)
    if model is None:
        return 0
    if write_series and len(wide_df) > 1:
        preds, lowers, uppers = calculate_bounds_series(
            model, X, Y, std_multiplier, min_std_dev, sample_residual_size
        )
        times_ms = [int(ts.timestamp() * 1000) for ts in wide_df.index.to_pydatetime()]
        return publish_backfill_series(
            machine_str,
            plan.targets,
            times_ms,
            preds,
            lowers,
            uppers,
            plan.influx_model or PEER_RF_INFLUX_MODEL,
            write_batch_fn,
        )
    predictions, lower_bounds, upper_bounds = calculate_bounds_fn(model, X, Y)
    return publish_fn(
        machine_str,
        plan.targets,
        predictions,
        lower_bounds,
        upper_bounds,
        timestamp=timestamp_ms,
        influx_model=plan.influx_model,
        publish_prometheus=plan.publish_prometheus,
    )


def check_peer_rf_backfill_done(influx_bucket: str, machine: str, target_field: str, days_ago: int = 25) -> bool:
    from bridge import clean_influx_csv, fetch_influx_data

    check_start = (datetime.now() - timedelta(days=days_ago + 3)).isoformat() + "Z"
    check_end = (datetime.now() - timedelta(days=days_ago)).isoformat() + "Z"
    query = f'''
from(bucket: "{influx_bucket}")
  |> range(start: {check_start}, stop: {check_end})
  |> filter(fn: (r) => r._measurement == "ml_predictions")
  |> filter(fn: (r) => r["machine"] == "{machine}")
  |> filter(fn: (r) => r["field"] == "{target_field}")
  |> filter(fn: (r) => r["model"] == "{PEER_RF_INFLUX_MODEL}")
  |> limit(n: 1)
'''
    raw = fetch_influx_data(query, timeout_secs=30)
    clean = clean_influx_csv(raw)
    if not clean:
        return False
    try:
        df = pd.read_csv(StringIO(clean), low_memory=False, dtype=str)
        return len(df) > 0
    except Exception:
        return False


def backfill_peer_rf_historical_data() -> None:
    from bridge import (
        BACKFILL_CHUNK_HOURS,
        FailedMachineTracker,
        INTER_MACHINE_SLEEP,
        INFLUX_BUCKET,
        ML_ERRORS,
        ML_MODEL_CONFIG,
        ML_WINDOW_DAYS,
        MIN_STD_DEV,
        SAMPLE_RESIDUAL_SIZE,
        STD_MULTIPLIER,
        build_matrices,
        calculate_bounds,
        fetch_machine_list,
        fetch_single_machine_data,
        fit_model,
        get_memory_usage_mb,
        has_sufficient_data,
        prepare_wide_df,
        publish_predictions,
        release_memory,
        validate_labels,
        write_predictions_batch_to_influx,
    )

    machines = load_peer_rf_machines()
    if not machines:
        print("[PEER-RF BACKFILL] No peer_rf_config machines — skip")
        return

    for machine_str, cfg in machines.items():
        if check_peer_rf_backfill_done(INFLUX_BUCKET, machine_str, cfg.target):
            print(f"[PEER-RF BACKFILL] ⏭️  {machine_str}/{cfg.target} already has peer_rf data")
            continue

        print(f"\n[PEER-RF BACKFILL] Starting {machine_str} target={cfg.target} "
              f"({ML_WINDOW_DAYS} days, {BACKFILL_CHUNK_HOURS}h chunks)")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=ML_WINDOW_DAYS)
        current_start = start_date
        chunk_num = 1
        total_chunks = int((ML_WINDOW_DAYS * 24 + BACKFILL_CHUNK_HOURS - 1) / BACKFILL_CHUNK_HOURS)
        tracker = FailedMachineTracker()
        total_written = 0

        while current_start < end_date:
            current_end = min(current_start + timedelta(hours=BACKFILL_CHUNK_HOURS), end_date)
            start_str = current_start.isoformat() + "Z"
            end_str = current_end.isoformat() + "Z"
            print(f"[PEER-RF BACKFILL] Chunk {chunk_num}/{total_chunks} {start_str} → {end_str}")

            if tracker.is_cooling_down(machine_str):
                current_start = current_end
                chunk_num += 1
                continue

            is_valid, _ = validate_labels(machine_str, cfg.target)
            if not is_valid:
                current_start = current_end
                chunk_num += 1
                continue

            try:
                df = fetch_single_machine_data(machine_str, start_str, end_str, timeout=120)
                if not has_sufficient_data(df, machine_str):
                    if df is not None:
                        del df
                    release_memory()
                    current_start = current_end
                    chunk_num += 1
                    continue

                written = train_peer_rf_machine(
                    machine_str,
                    df,
                    **ML_MODEL_CONFIG["backfill"],
                    timestamp_ms=None,
                    publish_fn=publish_predictions,
                    write_batch_fn=write_predictions_batch_to_influx,
                    build_matrices_fn=build_matrices,
                    fit_model_fn=fit_model,
                    calculate_bounds_fn=calculate_bounds,
                    prepare_wide_fn=prepare_wide_df,
                    std_multiplier=STD_MULTIPLIER,
                    min_std_dev=MIN_STD_DEV,
                    sample_residual_size=SAMPLE_RESIDUAL_SIZE,
                    write_series=True,
                )
                del df
                release_memory()
                total_written += written
                if written > 0:
                    print(f"[PEER-RF BACKFILL] ✓ chunk {chunk_num}: {written} points")
            except Exception as e:
                print(f"[PEER-RF BACKFILL] Error {machine_str} chunk {chunk_num}: {e}")
                tracker.mark_failed(machine_str)
                ML_ERRORS.labels(error_type="peer_rf_backfill").inc()
                release_memory()

            current_start = current_end
            chunk_num += 1
            time.sleep(2)

        print(f"[PEER-RF BACKFILL] ✓ {machine_str} complete ({total_written} points written)")
