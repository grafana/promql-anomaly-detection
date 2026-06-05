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
class PeerRfTargetConfig:
    machine_id: str
    target: str
    peer_features: list[str]


@dataclass(frozen=True)
class TrainingPlan:
    targets: list[str]
    features: list[str]
    excluded: list[str]
    influx_model: str | None
    publish_prometheus: bool


_PEER_RF_CACHE: list[PeerRfTargetConfig] | None = None


def _parse_peer_rf_entries(machine_id: str, cfg: dict) -> list[PeerRfTargetConfig]:
    machine_str = str(machine_id).strip()
    entries: list[PeerRfTargetConfig] = []

    def add_entry(target: str, peers: list) -> None:
        target = str(target).strip()
        if not target or not isinstance(peers, list):
            return
        peer_features = [str(p).strip() for p in peers if str(p).strip() and str(p).strip() != target]
        if not peer_features:
            return
        entries.append(
            PeerRfTargetConfig(
                machine_id=machine_str,
                target=target,
                peer_features=peer_features,
            )
        )

    targets = cfg.get("targets")
    if isinstance(targets, list):
        for item in targets:
            if isinstance(item, dict):
                add_entry(item.get("target", ""), item.get("peer_features") or [])

    # Legacy single-target shape (one entry per machine).
    legacy_target = str(cfg.get("target", "")).strip()
    if legacy_target:
        add_entry(legacy_target, cfg.get("peer_features") or [])

    return entries


def load_peer_rf_targets() -> list[PeerRfTargetConfig]:
    global _PEER_RF_CACHE
    if _PEER_RF_CACHE is not None:
        return _PEER_RF_CACHE
    path = PEER_RF_CONFIG_PATH
    if not os.path.isfile(path):
        _PEER_RF_CACHE = []
        return _PEER_RF_CACHE
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        machines = raw.get("machines") or {}
        out: list[PeerRfTargetConfig] = []
        for machine_id, cfg in machines.items():
            if not isinstance(cfg, dict):
                continue
            out.extend(_parse_peer_rf_entries(machine_id, cfg))
        _PEER_RF_CACHE = out
        machines_count = len({e.machine_id for e in out})
        print(
            f"[PEER-RF] Loaded {len(out)} target(s) across {machines_count} machine(s) from {path}"
        )
        return _PEER_RF_CACHE
    except Exception as e:
        print(f"[PEER-RF] Failed to load {path}: {e}")
        _PEER_RF_CACHE = []
        return _PEER_RF_CACHE


def load_peer_rf_machines() -> dict[str, list[PeerRfTargetConfig]]:
    grouped: dict[str, list[PeerRfTargetConfig]] = {}
    for entry in load_peer_rf_targets():
        grouped.setdefault(entry.machine_id, []).append(entry)
    return grouped


def merge_field_roles_from_peer_config() -> dict[str, str]:
    roles: dict[str, str] = {}
    for cfg in load_peer_rf_targets():
        roles[cfg.target] = "target"
        for feat in cfg.peer_features:
            roles[feat] = "feature"
    return roles


def get_peer_rf_plan(
    machine_str: str,
    wide_df: pd.DataFrame,
    target_field: str,
) -> TrainingPlan | None:
    cfg = next(
        (e for e in load_peer_rf_targets() if e.machine_id == machine_str and e.target == target_field),
        None,
    )
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
        print(f"[PEER-RF] {machine_str}/{cfg.target}: no peer feature columns available")
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


def _train_peer_rf_target(
    machine_str: str,
    wide_df: pd.DataFrame,
    target_field: str,
    n_estimators: int,
    max_depth: int,
    timestamp_ms: int | None,
    *,
    publish_fn: Any,
    write_batch_fn: Any,
    build_matrices_fn: Any,
    fit_model_fn: Any,
    calculate_bounds_fn: Any,
    std_multiplier: float,
    min_std_dev: float,
    sample_residual_size: int,
    write_series: bool,
) -> int:
    plan = get_peer_rf_plan(machine_str, wide_df, target_field)
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
    target_field: str | None = None,
) -> int:
    wide_df = prepare_wide_fn(df)
    if wide_df is None:
        return 0

    if target_field:
        targets = [target_field]
    else:
        targets = [e.target for e in load_peer_rf_targets() if e.machine_id == machine_str]
        if not targets:
            return 0

    total = 0
    for target in targets:
        total += _train_peer_rf_target(
            machine_str,
            wide_df,
            target,
            n_estimators,
            max_depth,
            timestamp_ms,
            publish_fn=publish_fn,
            write_batch_fn=write_batch_fn,
            build_matrices_fn=build_matrices_fn,
            fit_model_fn=fit_model_fn,
            calculate_bounds_fn=calculate_bounds_fn,
            std_multiplier=std_multiplier,
            min_std_dev=min_std_dev,
            sample_residual_size=sample_residual_size,
            write_series=write_series,
        )
    return total


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

    targets = load_peer_rf_targets()
    if not targets:
        print("[PEER-RF BACKFILL] No peer_rf_config targets — skip")
        return

    for entry in targets:
        machine_str = entry.machine_id
        target_field = entry.target
        if check_peer_rf_backfill_done(INFLUX_BUCKET, machine_str, target_field):
            print(f"[PEER-RF BACKFILL] ⏭️  {machine_str}/{target_field} already has peer_rf data")
            continue

        print(
            f"\n[PEER-RF BACKFILL] Starting {machine_str} target={target_field} "
            f"({ML_WINDOW_DAYS} days, {BACKFILL_CHUNK_HOURS}h chunks)"
        )

        end_date = datetime.now()
        start_date = end_date - timedelta(days=ML_WINDOW_DAYS)
        current_start = start_date
        chunk_num = 1
        total_chunks = int((ML_WINDOW_DAYS * 24 + BACKFILL_CHUNK_HOURS - 1) / BACKFILL_CHUNK_HOURS)
        tracker = FailedMachineTracker()
        total_written = 0
        tracker_key = f"{machine_str}:{target_field}"

        while current_start < end_date:
            current_end = min(current_start + timedelta(hours=BACKFILL_CHUNK_HOURS), end_date)
            start_str = current_start.isoformat() + "Z"
            end_str = current_end.isoformat() + "Z"
            print(f"[PEER-RF BACKFILL] {target_field} chunk {chunk_num}/{total_chunks} {start_str} → {end_str}")

            if tracker.is_cooling_down(tracker_key):
                current_start = current_end
                chunk_num += 1
                continue

            is_valid, _ = validate_labels(machine_str, target_field)
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
                    target_field=target_field,
                )
                del df
                release_memory()
                total_written += written
                if written > 0:
                    print(f"[PEER-RF BACKFILL] ✓ {target_field} chunk {chunk_num}: {written} points")
            except Exception as e:
                print(f"[PEER-RF BACKFILL] Error {machine_str}/{target_field} chunk {chunk_num}: {e}")
                tracker.mark_failed(tracker_key)
                ML_ERRORS.labels(error_type="peer_rf_backfill").inc()
                release_memory()

            current_start = current_end
            chunk_num += 1
            time.sleep(2)

        print(f"[PEER-RF BACKFILL] ✓ {machine_str}/{target_field} complete ({total_written} points written)")
