# =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 1: Imports, Configuration, Registry & Utilities
# =============================================================================
import os
import time
import requests
import pandas as pd
from io import StringIO
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from prometheus_client import start_http_server, Gauge, Counter, CollectorRegistry
import urllib3
import threading
import gc
from datetime import datetime, timedelta
import traceback
import resource
import ctypes



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# =============================================================================
# CONFIGURATION
# =============================================================================
def _load_influx_token() -> str:
    token = os.environ.get("INFLUX_TOKEN", "").strip()
    if token:
        return token
    candidates = [
        os.environ.get("INFLUX_TOKEN_FILE", "").strip(),
        "/secrets/influx_token",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".influx_token"),
    ]
    for token_file in candidates:
        if token_file and os.path.isfile(token_file):
            with open(token_file, encoding="utf-8") as f:
                found = f.read().strip()
            if found:
                return found
    return ""


INFLUX_HOST   = os.environ.get("INFLUX_HOST", "https://52.35.251.91:8086")
INFLUX_ORG    = os.environ.get("INFLUX_ORG", "powertech")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "powertechdata")
TOKEN         = _load_influx_token()
INFLUX_URL    = f"{INFLUX_HOST.rstrip('/')}/api/v2/query?org={INFLUX_ORG}"
MACHINE_COLUMN = "machine"
TIME_COLUMN    = "_time"
VALUE_COLUMN   = "_value"
FIELD_COLUMN   = "_field"
KEEP_COLUMNS   = [TIME_COLUMN, VALUE_COLUMN, FIELD_COLUMN, MACHINE_COLUMN]
FIELD_ROLES: dict[str, str] = {}
ENABLE_PEER_RF = os.environ.get("ENABLE_PEER_RF", "1").strip().lower() in ("1", "true", "yes")
ENABLE_PEER_RF_BACKFILL = os.environ.get("ENABLE_PEER_RF_BACKFILL", "1").strip().lower() in ("1", "true", "yes")
ML_UPDATE_INTERVAL   = 300
LIVE_UPDATE_INTERVAL = 15
ML_WINDOW_DAYS       = 30
BACKFILL_CHUNK_HOURS = 3
ML_MODEL_CONFIG = {
    "live":     {"n_estimators": 5, "max_depth": 3},
    "backfill": {"n_estimators": 3, "max_depth": 2},
}
STD_MULTIPLIER        = 2.0
MIN_STD_DEV           = 1.0
MIN_ROWS_FOR_TRAINING = 20
SAMPLE_RESIDUAL_SIZE  = 500
MAX_LABEL_LENGTH        = 50
MAX_FIELD_LENGTH        = 30
MAX_METRICS_PER_MACHINE = 255
LABEL_TIMEOUT           = 600
MAX_ROWS_PER_MACHINE    = 10_000
# --- Memory Thresholds (MB) ---
MEMORY_CRITICAL_MB = 5000
MEMORY_WARNING_MB  = 3000
# --- Timing ---
INTER_MACHINE_SLEEP     = 0.1
HEALTH_LOG_INTERVAL     = 60
FAILED_MACHINE_COOLDOWN = 3600
# =============================================================================
# FIX 1 — LIVE QUERY ROW CAP
# Prevents the live loop from accumulating unbounded response data when
# InfluxDB returns more rows than expected (e.g. during schema changes).
# =============================================================================
LIVE_QUERY_ROW_CAP = 5_000
# =============================================================================
# PROMETHEUS METRIC DEFINITIONS
# =============================================================================
METRIC_DEFINITIONS: dict[str, tuple[str, list[str]]] = {
    "machine_metric_expected":    ("ML predicted value",       ["machine", "field"]),
    "machine_metric_upper_bound": ("ML predicted upper bound", ["machine", "field"]),
    "machine_metric_lower_bound": ("ML predicted lower bound", ["machine", "field"]),
    "machine_metrics":            ("Actual current value",     ["machine", "field"]),
}
COUNTER_DEFINITIONS: dict[str, tuple[str, list[str]]] = {
    "ml_training_cycles_total":         ("Total ML training cycles",  []),
    "live_data_cycles_total":           ("Total live data cycles",    []),
    "ml_errors_total":                  ("Total ML errors",           ["error_type"]),
    "live_data_errors_total":           ("Total live data errors",    []),
    "prometheus_skipped_metrics_total": ("Skipped metrics",           ["reason"]),
}
GAUGE_DEFINITIONS: dict[str, tuple[str, list[str]]] = {
    "ml_process_memory_mb":      ("Process memory usage MB",      []),
    "ml_last_run_timestamp":     ("Unix timestamp last ML run",   []),
    "live_last_run_timestamp":   ("Unix timestamp last live run", []),
    "ml_machines_trained_total": ("Machines trained last cycle",  []),
}


# =============================================================================
# INFLUXDB WRITE HELPER (using requests, not influxdb-client)
# Batches all predictions for a machine into a single HTTP POST
# =============================================================================
def write_predictions_to_influx(
    machine_str: str,
    field_name: str,
    expected: float,
    upper: float,
    lower: float,
    timestamp_ms: int,
) -> bool:
    """Single prediction write - kept for compatibility."""
    return write_predictions_batch_to_influx([{
        "machine": machine_str,
        "field":   field_name,
        "expected": expected,
        "upper":    upper,
        "lower":    lower,
    }], timestamp_ms)
def _escape_line_protocol_tag(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def write_predictions_batch_to_influx(
    predictions: list[dict],
    timestamp_ms: int,
) -> bool:
    """
    Write multiple ML predictions to InfluxDB in a single HTTP request.
    Uses InfluxDB line protocol with newline-separated points.

    Each dict: machine, field, expected, upper, lower;
    optional model (Influx tag), timestamp_ms (per-point override).
    """
    if not predictions:
        return True

    lines = []
    for p in predictions:
        ts_ms = int(p.get("timestamp_ms", timestamp_ms))
        timestamp_ns = int(ts_ms * 1_000_000)
        machine_tag = _escape_line_protocol_tag(p["machine"])
        field_tag = _escape_line_protocol_tag(p["field"])
        tag_part = f"machine={machine_tag},field={field_tag}"
        model = p.get("model")
        if model:
            tag_part += f",model={_escape_line_protocol_tag(model)}"
        line = (
            f"ml_predictions,{tag_part} "
            f"expected={p['expected']},"
            f"upper={p['upper']},"
            f"lower={p['lower']} "
            f"{timestamp_ns}"
        )
        lines.append(line)
    
    body = "\n".join(lines)
    
    headers = {
        "Authorization": f"Token {TOKEN}",
        "Content-Type": "text/plain; charset=utf-8",
    }
    
    write_url = (
        f"{INFLUX_HOST.rstrip('/')}/api/v2/write"
        f"?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}"
    )
    
    try:
        res = requests.post(
            write_url,
            data=body,
            headers=headers,
            verify=False,
            timeout=30,  # Higher timeout for larger batches
        )
        if res.status_code == 204:
            return True
        print(f"[INFLUX WRITE] Non-204 response: {res.status_code} - {res.text}")
        return False
    except Exception as e:
        print(f"[INFLUX WRITE] Error: {e}")
        return False
    
# =============================================================================
# REGISTRY
# =============================================================================
class BoundedRegistry(CollectorRegistry):
    """CollectorRegistry that logs registration errors instead of crashing."""
    def __init__(self, max_metrics: int = 100_000):
        super().__init__()
        self.max_metrics = max_metrics
    def register(self, collector):
        try:
            super().register(collector)
        except Exception as e:
            print(f"[REGISTRY] Registration error: {e}")
REGISTRY = BoundedRegistry(max_metrics=100_000)
ML_GAUGES: dict[str, Gauge] = {
    name: Gauge(name, help_text, labels, registry=REGISTRY)
    for name, (help_text, labels) in METRIC_DEFINITIONS.items()
}
SCALAR_GAUGES: dict[str, Gauge] = {
    name: Gauge(name, help_text, labels, registry=REGISTRY)
    for name, (help_text, labels) in GAUGE_DEFINITIONS.items()
}
COUNTERS: dict[str, Counter] = {
    name: Counter(name, help_text, labels, registry=REGISTRY)
    for name, (help_text, labels) in COUNTER_DEFINITIONS.items()
}
EXPECTED_VAL     = ML_GAUGES["machine_metric_expected"]
UPPER_BOUND      = ML_GAUGES["machine_metric_upper_bound"]
LOWER_BOUND      = ML_GAUGES["machine_metric_lower_bound"]
ACTUAL_VAL       = ML_GAUGES["machine_metrics"]
ML_CYCLES        = COUNTERS["ml_training_cycles_total"]
LIVE_CYCLES      = COUNTERS["live_data_cycles_total"]
ML_ERRORS        = COUNTERS["ml_errors_total"]
LIVE_ERRORS      = COUNTERS["live_data_errors_total"]
SKIPPED_METRICS  = COUNTERS["prometheus_skipped_metrics_total"]
MEMORY_USAGE     = SCALAR_GAUGES["ml_process_memory_mb"]
LAST_ML_RUN      = SCALAR_GAUGES["ml_last_run_timestamp"]
LAST_LIVE_RUN    = SCALAR_GAUGES["live_last_run_timestamp"]
MACHINES_TRAINED = SCALAR_GAUGES["ml_machines_trained_total"]
ACTIVE_METRIC_LABELS: dict[tuple[str, str], float] = {}
# =============================================================================
# FIX 2 — GLOBAL PROMETHEUS LOCK
# WHY THIS IS NEEDED:
# prometheus_client's internal _metrics dict (which caches child label
# objects) is NOT thread-safe. When the live thread and ML thread both
# call gauge.labels(machine=x, field=y) at the same moment:
#   - Both threads do the dict lookup and find the key absent
#   - Both allocate a new child Gauge object
#   - Both insert into _metrics — the loser's object is orphaned
#   - The orphan is never freed because prometheus_client holds a
#     reference in its internal structure that the Python GC cannot see
# After thousands of cycles across 28 machines this is the PRIMARY cause
# of the unbounded memory growth seen in the logs (165MB → 7000MB+).
# Serialising all .labels() and .set() calls with one lock costs ~0.1ms
# per call — completely negligible at 15-second live intervals.
# =============================================================================
_PROM_LOCK = threading.Lock()
# =============================================================================
# MEMORY UTILITIES
# =============================================================================
def get_memory_usage_mb() -> float:
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    try:
        with open('/proc/self/statm', 'r') as f:
            return int(f.read().split()[1]) * 4 / 1024
    except Exception:
        pass
    try:
        with open('/proc/self/smaps_rollup', 'r') as f:
            for line in f:
                if line.startswith('Rss:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        pass
    return 0.0
def release_memory():
    """Run all GC generations and ask libc to return freed pages to OS."""
    gc.collect(0)
    gc.collect(1)
    gc.collect(2)
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
# =============================================================================
# LABEL VALIDATION & TRACKING
# =============================================================================
def validate_labels(machine: str, field: str) -> tuple[bool, str]:
    machine = str(machine).strip() if machine else ""
    field   = str(field).strip()   if field   else ""
    if not machine or not field:
        return False, "empty_label"
    if len(machine) > MAX_LABEL_LENGTH:
        return False, "machine_too_long"
    if len(field) > MAX_FIELD_LENGTH:
        return False, "field_too_long"
    for ch in ['\x00', '\n', '\r', '\t']:
        if ch in machine or ch in field:
            return False, "invalid_chars"
    return True, "valid"
def track_metric_label(machine: str, field: str) -> bool:
    key = (machine, field)
    if key in ACTIVE_METRIC_LABELS:
        ACTIVE_METRIC_LABELS[key] = time.time()
        return True
    machine_count = sum(1 for (m, _) in ACTIVE_METRIC_LABELS if m == machine)
    if machine_count >= MAX_METRICS_PER_MACHINE:
        if not hasattr(track_metric_label, '_warned'):
            track_metric_label._warned = set()
        if machine not in track_metric_label._warned:
            print(f"[METRICS] ⚠️ Machine '{machine}' reached field limit "
                  f"({MAX_METRICS_PER_MACHINE})")
            if len(track_metric_label._warned) < 1000:
                track_metric_label._warned.add(machine)
        SKIPPED_METRICS.labels(reason="machine_field_limit").inc()
        return False
    ACTIVE_METRIC_LABELS[key] = time.time()
    return True
def prune_stale_labels():
    """
    Remove stale Prometheus label sets and release memory held by the
    prometheus_client internal label cache.
    FIX 3 — After calling gauge.remove() we also force a full GC + malloc_trim
    so that the RSS reported by /proc/self/status actually drops.
    Previously remove() detached the child object from _metrics but Python's
    allocator kept the pages mapped; malloc_trim returns them to the OS.
    """
    now   = time.time()
    stale = [k for k, v in ACTIVE_METRIC_LABELS.items()
             if now - v > LABEL_TIMEOUT]
    if not stale:
        return
    print(f"[PRUNE] Removing {len(stale)} stale label sets...")
    with _PROM_LOCK:
        for machine, field in stale:
            for name, (_, label_names) in METRIC_DEFINITIONS.items():
                if label_names == ["machine", "field"]:
                    try:
                        ML_GAUGES[name].remove(machine, field)
                    except Exception:
                        pass
            ACTIVE_METRIC_LABELS.pop((machine, field), None)
    release_memory()

    # =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 2A: FailedMachineTracker, Validation & Field Classification
# =============================================================================
# =============================================================================
# FAILED MACHINE TRACKER
# =============================================================================
class FailedMachineTracker:
    def __init__(self, cooldown_secs: int = FAILED_MACHINE_COOLDOWN):
        self._failed:   dict[str, float] = {}
        self._cooldown: int              = cooldown_secs
    def is_cooling_down(self, machine: str) -> bool:
        if machine not in self._failed:
            return False
        if time.time() - self._failed[machine] >= self._cooldown:
            del self._failed[machine]
            return False
        return True
    def mark_failed(self, machine: str):
        self._failed[machine] = time.time()
    def clear(self, machine: str):
        self._failed.pop(machine, None)
# =============================================================================
# DATA VALIDATION HELPERS
# =============================================================================
def has_sufficient_data(df, label: str = "") -> bool:
    if df is None:
        if label:
            print(f"[DATA] Skipping '{label}': no data returned")
        return False
    if len(df) < MIN_ROWS_FOR_TRAINING:
        if label:
            print(f"[DATA] Skipping '{label}': only {len(df)} rows "
                  f"(need {MIN_ROWS_FOR_TRAINING})")
        return False
    return True
def ensure_2d(arr) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 0:
        return arr.reshape(1, 1)
    if arr.ndim == 1:
        return arr.reshape(-1, 1)
    return arr
# =============================================================================
# FIELD CLASSIFICATION
# =============================================================================
def classify_fields(
    wide_df: pd.DataFrame,
) -> tuple[list[str], list[str], list[str]]:
    targets:  list[str] = []
    features: list[str] = []
    excluded: list[str] = []
    time_cols = {'hour', 'minute'}
    for col in wide_df.columns:
        if col in time_cols:
            continue
        override = FIELD_ROLES.get(col)
        if override == 'exclude':
            excluded.append(col)
            continue
        if override == 'target':
            targets.append(col)
            continue
        if override == 'feature':
            features.append(col)
            continue
        try:
            nunique = wide_df[col].nunique()
            std     = float(wide_df[col].std())
            mean    = float(wide_df[col].mean())
            cv      = abs(std / mean) if mean != 0 else 0.0
            if std == 0:
                features.append(col)
            elif nunique <= 2:
                if col not in targets:
                    targets.append(col)
                if col not in features:
                    features.append(col)
            elif nunique <= 5:
                features.append(col)
            elif cv > 0.001:
                targets.append(col)
            else:
                features.append(col)
        except Exception:
            features.append(col)
    if not features:
        features = [
            c for c in wide_df.columns
            if c not in targets
            and c not in excluded
            and c not in time_cols
        ]
    if len(targets) > MAX_METRICS_PER_MACHINE:
        targets = targets[:MAX_METRICS_PER_MACHINE]
    return targets, features, excluded

# =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 2B: InfluxDB Connection & CSV Cleaning
# =============================================================================
# =============================================================================
# INFLUXDB CONNECTION
# =============================================================================
def _build_keep_columns_flux() -> str:
    return ", ".join(f'"{c}"' for c in KEEP_COLUMNS)
def fetch_influx_data(query: str, timeout_secs: int) -> str | None:
    """
    POST a Flux query to InfluxDB and return raw CSV text.
    Returns None on any error or non-200 status.
    """
    headers = {
        "Authorization": f"Token {TOKEN}",
        "Content-Type":  "application/json",
        "Accept":        "application/csv",
    }
    try:
        res = requests.post(
            INFLUX_URL,
            json={"query": query},
            headers=headers,
            verify=False,
            timeout=timeout_secs,
        )
        if res.status_code == 200:
            return res.text
        print(f"[INFLUX] Non-200 response: {res.status_code}")
        return None
    except Exception as e:
        print(f"[ERROR] InfluxDB fetch failed: {e}")
        return None
# =============================================================================
# CSV CLEANING
# =============================================================================
def clean_influx_csv(csv_data: str | None) -> str | None:
    """
    Strip InfluxDB annotation rows (lines starting with '#') and blank lines.
    Returns None if nothing usable remains.
    """
    if not csv_data:
        return None
    lines = [
        l for l in csv_data.splitlines()
        if l.strip() and not l.startswith('#')
    ]
    return "\n".join(lines) if lines else None
def parse_and_clean_df(
    csv_text: str,
    required_cols: set[str],
) -> pd.DataFrame | None:
    """
    Parse cleaned InfluxDB CSV into a DataFrame.
    FIX 4 — dtype=str on read_csv prevents pandas from allocating
    large float64 arrays for every column up front.  We cast only
    VALUE_COLUMN to float32 explicitly after filtering, so peak
    memory during parsing is roughly halved for wide result sets.
    """
    try:
        df = pd.read_csv(StringIO(csv_text), low_memory=False, dtype=str)
    except Exception as e:
        print(f"[PARSE] CSV parse failed: {e}")
        return None
    for col in required_cols:
        if col not in df.columns:
            print(f"[PARSE] Missing column: {col}")
            return None
    df = df[list(required_cols)].dropna(how='all')
    if TIME_COLUMN in required_cols:
        df = df[df[TIME_COLUMN] != TIME_COLUMN]
    if VALUE_COLUMN in required_cols:
        df[VALUE_COLUMN] = (
            df[VALUE_COLUMN]
            .str.strip()
            .replace({'': np.nan, ' ': np.nan, 'nan': np.nan, 'None': np.nan})
        )
        df[VALUE_COLUMN] = pd.to_numeric(df[VALUE_COLUMN], errors='coerce')
        df = df.dropna(subset=[VALUE_COLUMN])
        df[VALUE_COLUMN] = df[VALUE_COLUMN].astype('float32')
    return df if len(df) > 0 else None
# =============================================================================
# MACHINE LIST FETCH
# =============================================================================
def fetch_machine_list(
    start_str: str,
    end_str: str,
    timeout: int = 60,
) -> list[str]:
    query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start_str}, stop: {end_str})
  |> keep(columns: ["{MACHINE_COLUMN}"])
  |> distinct(column: "{MACHINE_COLUMN}")
'''
    raw   = fetch_influx_data(query, timeout)
    clean = clean_influx_csv(raw)
    if not clean:
        return []
    try:
        df = pd.read_csv(StringIO(clean), low_memory=False, dtype=str)
        if MACHINE_COLUMN not in df.columns:
            return []
        machines = df[MACHINE_COLUMN].dropna().unique().tolist()
        del df
        gc.collect(0)
        return [str(m).strip() for m in machines if str(m).strip()]
    except Exception as e:
        print(f"[MACHINE LIST] Failed: {e}")
        return []
# =============================================================================
# SINGLE MACHINE DATA FETCH
# =============================================================================
def fetch_single_machine_data(
    machine_str: str,
    start_str: str,
    end_str: str,
    timeout: int = 120,
) -> pd.DataFrame | None:
    """
    Fetch all fields for ONE machine over a time window.
    FIX 5 — Added |> limit(n: MAX_ROWS_PER_MACHINE) to the Flux query.
    Previously the query fetched the full 30-day window with no server-side
    row limit. For a machine like 2103-176030 with 66 fields sampled every
    minute that is up to 66 * 30 * 1440 = 2,851,200 rows transferred over
    the network, held in memory as a CSV string, then parsed into a DataFrame.
    The server-side limit drops this to at most MAX_ROWS_PER_MACHINE rows
    per field (Flux limit applies per table/field) which is the same cap
    we applied client-side in build_matrices — so no training quality is lost.
    """
    keep_cols = _build_keep_columns_flux()
    query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r["{MACHINE_COLUMN}"] == "{machine_str}")
  |> keep(columns: [{keep_cols}])
  |> tail(n: {MAX_ROWS_PER_MACHINE})
'''
    raw   = fetch_influx_data(query, timeout)
    clean = clean_influx_csv(raw)
    if not clean:
        return None
    required = set(KEEP_COLUMNS)
    df = parse_and_clean_df(clean, required)
    del raw, clean
    gc.collect(0)
    return df
# =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 3A: Wide Prep, Matrix Builder & Model Fit
# =============================================================================
# =============================================================================
# WIDE FORMAT PREPARATION
# =============================================================================
def prepare_wide_df(df: pd.DataFrame) -> pd.DataFrame | None:
    try:
        wide_df = df.pivot_table(
            index=TIME_COLUMN,
            columns=FIELD_COLUMN,
            values=VALUE_COLUMN,
            aggfunc='first',
        )
        wide_df = wide_df.ffill().bfill().dropna()
        if not has_sufficient_data(wide_df):
            return None
        wide_df.index = pd.to_datetime(wide_df.index, format='ISO8601')  # ← FIXED
        wide_df['hour']   = wide_df.index.hour.astype('int8')
        wide_df['minute'] = wide_df.index.minute.astype('int8')
        for col in wide_df.columns:
            if col not in ['hour', 'minute']:
                wide_df[col] = wide_df[col].astype('float32')
        return wide_df
    except Exception as e:
        print(f"[PREP] Wide format conversion failed: {e}")
        return None
# =============================================================================
# FEATURE / TARGET MATRIX BUILDER
# =============================================================================
def build_matrices(
    wide_df: pd.DataFrame,
    target_fields: list[str],
    feature_cols: list[str],
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    try:
        X = ensure_2d(wide_df[feature_cols].values.astype('float32'))
        Y = ensure_2d(wide_df[target_fields].values.astype('float32'))
        if X.shape[0] > MAX_ROWS_PER_MACHINE:
            X = X[-MAX_ROWS_PER_MACHINE:]
            Y = Y[-MAX_ROWS_PER_MACHINE:]
        return X, Y
    except Exception as e:
        print(f"[MATRIX] Build failed: {e}")
        return None, None
# =============================================================================
# RANDOM FOREST MODEL FIT
# =============================================================================
def fit_model(
    X: np.ndarray,
    Y: np.ndarray,
    n_estimators: int,
    max_depth: int,
) -> RandomForestRegressor | None:
    try:
        max_samples_value = min(len(X), max(len(X) // 2, 10))
        min_leaf_value    = max(min(len(X) // 20, 10), 2)
        model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=42,
            n_jobs=1,
            warm_start=False,
            max_samples=max_samples_value,
            min_samples_leaf=min_leaf_value,
        )
        if Y.shape[1] == 1:
            model.fit(X, Y.ravel())
        else:
            model.fit(X, Y)
        return model
    except Exception as e:
        print(f"[FIT] Model fitting failed: {e}")
        return None
# =============================================================================
# RESIDUAL / BOUNDS CALCULATION
# =============================================================================
def calculate_bounds(
    model: RandomForestRegressor,
    X: np.ndarray,
    Y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X_latest    = ensure_2d(X[[-1]])
    predictions = model.predict(X_latest)
    if predictions.ndim == 1:
        predictions = predictions.reshape(1, -1)
    sample_size  = min(SAMPLE_RESIDUAL_SIZE, len(X))
    sample_preds = model.predict(X[-sample_size:])
    if sample_preds.ndim == 1:
        sample_preds = sample_preds.reshape(-1, 1)
    residuals      = Y[-sample_size:] - sample_preds
    std_deviations = np.std(residuals, axis=0)
    std_deviations = np.maximum(std_deviations, MIN_STD_DEV)
    lower_bounds   = predictions[0] - (STD_MULTIPLIER * std_deviations)
    upper_bounds   = predictions[0] + (STD_MULTIPLIER * std_deviations)
    return predictions, lower_bounds, upper_bounds

# =============================================================================
# PROMETHEUS METRIC PUBLISHER + INFLUXDB PERSISTENT STORAGE
#
# FIX 6 — All gauge.labels().set() calls are wrapped in _PROM_LOCK.
#
# FIX 12 — Write predictions to InfluxDB line protocol using requests.
# No external InfluxDB client library needed - uses same pattern as
# fetch_influx_data() but for writes instead of reads.
# =============================================================================
def publish_predictions(
    machine_str: str,
    target_fields: list[str],
    predictions: np.ndarray,
    lower_bounds: np.ndarray,
    upper_bounds: np.ndarray,
    timestamp: int | None = None,
    influx_model: str | None = None,
    publish_prometheus: bool = True,
) -> int:
    if timestamp is None:
        timestamp = int(time.time() * 1000)
    metrics_set = 0
    influx_batch = []  # Collect all points, write once at end
    for idx, target in enumerate(target_fields):
        is_valid, reason = validate_labels(machine_str, target)
        if not is_valid:
            SKIPPED_METRICS.labels(reason=reason).inc()
            continue
        if not track_metric_label(machine_str, target):
            continue
        try:
            if predictions.shape[1] > 1:
                y_pred    = float(predictions[0, idx])
                lower_val = float(lower_bounds[idx])
                upper_val = float(upper_bounds[idx])
            else:
                y_pred    = float(predictions[0, 0])
                lower_val = float(lower_bounds[0])
                upper_val = float(upper_bounds[0])
        except (IndexError, ValueError) as e:
            print(f"[PUBLISH] Index error for {machine_str}/{target}: {e}")
            SKIPPED_METRICS.labels(reason="index_error").inc()
            continue
        if np.isnan(y_pred) or np.isinf(y_pred):
            SKIPPED_METRICS.labels(reason="invalid_prediction").inc()
            continue
        if publish_prometheus:
            with _PROM_LOCK:
                EXPECTED_VAL.labels(machine=machine_str, field=target).set(y_pred)
                LOWER_BOUND.labels(machine=machine_str, field=target).set(lower_val)
                UPPER_BOUND.labels(machine=machine_str, field=target).set(upper_val)
        influx_point: dict = {
            "machine":  machine_str,
            "field":    target,
            "expected": y_pred,
            "upper":    upper_val,
            "lower":    lower_val,
        }
        if influx_model:
            influx_point["model"] = influx_model
        influx_batch.append(influx_point)
        metrics_set += 1
    if influx_batch:
        tag = f" model={influx_model}" if influx_model else ""
        if write_predictions_batch_to_influx(influx_batch, timestamp):
            print(f"[PUBLISH] ✓ Wrote {len(influx_batch)} predictions to InfluxDB{tag}")
        else:
            ML_ERRORS.labels(error_type="influx_write").inc()
    return metrics_set

# =============================================================================
# MAIN TRAIN FUNCTION
# =============================================================================
def train_single_machine(
    machine_str: str,
    df: pd.DataFrame,
    n_estimators: int = 5,
    max_depth: int = 3,
) -> int:
    """
    Full training pipeline for ONE machine.
    FIX 7 — Explicit del + release_memory() after every major intermediate
    object. Previously del + gc.collect(0) was used but collect(0) only
    sweeps generation 0. RandomForest internal numpy arrays survive into
    generation 1 and 2 because sklearn holds back-references through its
    estimators_ list. A full 3-generation collect + malloc_trim is required
    to actually return RSS to the OS between machines. This is why memory
    climbed by ~170MB per machine in the original logs even though del was
    already being called.
    """
    metrics_set = 0
    try:
        # Step 1: Wide format
        wide_df = prepare_wide_df(df)
        if wide_df is None:
            return 0
        # Step 2: Classify fields
        target_fields, feature_cols, excluded = classify_fields(wide_df)
        if excluded:
            print(f"[TRAIN] {machine_str}: excluded {len(excluded)} fields")
        if not target_fields:
            print(f"[TRAIN] {machine_str}: no target fields found, skipping")
            del wide_df
            release_memory()
            return 0
        if not feature_cols:
            print(f"[TRAIN] {machine_str}: no feature columns found, skipping")
            del wide_df
            release_memory()
            return 0
        # Step 3: Build matrices
        X, Y = build_matrices(wide_df, target_fields, feature_cols)
        # wide_df no longer needed after matrices are built
        del wide_df
        release_memory()
        if X is None or Y is None:
            return 0
        # Step 4: Fit model
        model = fit_model(X, Y, n_estimators, max_depth)
        if model is None:
            del X, Y
            release_memory()
            return 0
        # Step 5: Calculate bounds
        predictions, lower_bounds, upper_bounds = calculate_bounds(model, X, Y)
        # Model and training arrays no longer needed
        del model, X, Y
        release_memory()
        
        # Step 6: Publish to Prometheus + InfluxDB
        current_timestamp = int(time.time() * 1000)  # Current time in ms
        metrics_set = publish_predictions(
            machine_str,
            target_fields,
            predictions,
            lower_bounds,
            upper_bounds,
            timestamp=current_timestamp,
        )

        if ENABLE_PEER_RF:
            try:
                from bridge_peer_rf import train_peer_rf_machine

                peer_set = train_peer_rf_machine(
                    machine_str,
                    df,
                    n_estimators,
                    max_depth,
                    current_timestamp,
                    publish_fn=publish_predictions,
                    write_batch_fn=write_predictions_batch_to_influx,
                    build_matrices_fn=build_matrices,
                    fit_model_fn=fit_model,
                    calculate_bounds_fn=calculate_bounds,
                    prepare_wide_fn=prepare_wide_df,
                    std_multiplier=STD_MULTIPLIER,
                    min_std_dev=MIN_STD_DEV,
                    sample_residual_size=SAMPLE_RESIDUAL_SIZE,
                    write_series=False,
                )
                if peer_set > 0:
                    print(f"[PEER-RF] {machine_str}: {peer_set} peer_rf point(s)")
            except Exception as peer_err:
                print(f"[PEER-RF] {machine_str}: {peer_err}")
                ML_ERRORS.labels(error_type="peer_rf_train").inc()

        # Step 7: Free prediction arrays
        del predictions, lower_bounds, upper_bounds
        release_memory()
    except Exception as e:
        print(f"[TRAIN] Failed for {machine_str}: {e}")
        traceback.print_exc()
        ML_ERRORS.labels(error_type="model_training").inc()
        release_memory()
    return metrics_set

# =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 4A: Live Data Loop
# =============================================================================
def live_data_loop():
    """
    TRACK 1: Real-time actual value stream.
    Runs every LIVE_UPDATE_INTERVAL seconds.
    FIX 8 — Replaced df.iterrows() with direct numpy array iteration.
    iterrows() wraps every row in a new pd.Series object. With 669 metrics
    updated per cycle at 15-second intervals that is 669 Series allocations
    per cycle, ~2,676 per minute, ~160,560 per hour — none of which were
    being released promptly because pandas keeps internal block references.
    Using to_numpy() + zip converts the entire frame to plain numpy arrays
    in one allocation that are freed cleanly after the loop.
    FIX 9 — ACTUAL_VAL.labels().set() is now called inside _PROM_LOCK.
    The live loop runs in its own thread concurrently with the ML thread
    which also calls gauge.labels(). Without the lock both threads can
    corrupt the internal _metrics dict simultaneously (see FIX 2 / FIX 6).
    """
    keep_cols  = ", ".join(f'"{c}"' for c in
                           [VALUE_COLUMN, FIELD_COLUMN, MACHINE_COLUMN])
    live_query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -2m)
  |> last()
  |> keep(columns: [{keep_cols}])
'''
    required_cols = {VALUE_COLUMN, FIELD_COLUMN, MACHINE_COLUMN}
    last_prune    = time.time()
    while True:
        try:
            # --- Memory check ---
            mem = get_memory_usage_mb()
            MEMORY_USAGE.set(mem)
            if mem > MEMORY_CRITICAL_MB:
                print(f"[LIVE] 🚨 CRITICAL MEMORY: {mem:.1f}MB")
                release_memory()
            print(f"[LIVE] Fetching live data (Memory: {mem:.1f}MB)...")
            # --- Fetch & clean ---
            raw_data  = fetch_influx_data(live_query, timeout_secs=10)
            clean_csv = clean_influx_csv(raw_data)
            del raw_data
            if clean_csv:
                df = parse_and_clean_df(clean_csv, required_cols)
                del clean_csv
                if df is not None:
                    # --- Safety row cap ---
                    if len(df) > LIVE_QUERY_ROW_CAP:
                        print(f"[LIVE] ⚠️ Capping {len(df)} rows to "
                              f"{LIVE_QUERY_ROW_CAP}")
                        df = df.iloc[:LIVE_QUERY_ROW_CAP].copy()
                    # FIX 8: convert to numpy once, iterate arrays not Series
                    machines = df[MACHINE_COLUMN].to_numpy(dtype=str)
                    fields   = df[FIELD_COLUMN].to_numpy(dtype=str)
                    values   = df[VALUE_COLUMN].to_numpy(dtype=np.float32)
                    del df
                    metrics_updated = 0
                    metrics_skipped = 0
                    for machine, field, value in zip(machines, fields, values):
                        try:
                            machine = machine.strip()
                            field   = field.strip()
                            is_valid, reason = validate_labels(machine, field)
                            if not is_valid:
                                metrics_skipped += 1
                                SKIPPED_METRICS.labels(reason=reason).inc()
                                continue
                            if not track_metric_label(machine, field):
                                metrics_skipped += 1
                                continue
                            if np.isnan(value) or np.isinf(value):
                                metrics_skipped += 1
                                continue
                            # FIX 9: lock around .labels().set()
                            with _PROM_LOCK:
                                ACTUAL_VAL.labels(
                                    machine=machine,
                                    field=field,
                                ).set(float(value))
                            metrics_updated += 1
                        except Exception:
                            metrics_skipped += 1
                    del machines, fields, values
                    if metrics_skipped > 0:
                        print(f"[LIVE] ✓ Updated {metrics_updated} metrics, "
                              f"skipped {metrics_skipped}")
                    else:
                        print(f"[LIVE] ✓ Updated {metrics_updated} metrics")
            else:
                print("[LIVE] No data received")
            release_memory()
            # --- Periodic label pruning ---
            now = time.time()
            if now - last_prune >= LABEL_TIMEOUT:
                prune_stale_labels()
                last_prune = now
            LIVE_CYCLES.inc()
            LAST_LIVE_RUN.set(time.time())
        except Exception as e:
            print(f"[LIVE ERROR] {e}")
            LIVE_ERRORS.inc()
            traceback.print_exc()
        time.sleep(LIVE_UPDATE_INTERVAL)

        # =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 4B: ML Training Loop
# =============================================================================
def ml_training_loop():
    """
    TRACK 2: Per-machine streaming ML training.
    Runs every ML_UPDATE_INTERVAL seconds.
    FIX 10 — release_memory() (full 3-generation GC + malloc_trim) is
    called after every machine instead of gc.collect(0).
    collect(0) only sweeps generation 0. RandomForest internal numpy
    arrays survive into generations 1 and 2 because sklearn holds
    back-references through its estimators_ list. Without a full sweep
    + malloc_trim the OS-visible RSS never drops between machines, which
    is exactly the staircase pattern seen in the logs:
      165MB -> 340MB -> 594MB -> 575MB (first trim finally kicked in)
    With release_memory() after every machine the RSS stays flat.
    """
    last_prune = time.time()
    tracker    = FailedMachineTracker()
    while True:
        try:
            # --- Memory check ---
            mem = get_memory_usage_mb()
            MEMORY_USAGE.set(mem)
            if mem > MEMORY_CRITICAL_MB:
                print(f"[ML] 🚨 CRITICAL MEMORY: {mem:.1f}MB - EMERGENCY CLEANUP")
                release_memory()
                prune_stale_labels()
                time.sleep(5)
                continue
            if mem > MEMORY_WARNING_MB:
                print(f"[ML] ⚠️ WARNING: Memory at {mem:.1f}MB")
                release_memory()
            # --- Build time window ---
            end_date   = datetime.now()
            start_date = end_date - timedelta(days=ML_WINDOW_DAYS)
            start_str  = start_date.isoformat() + "Z"
            end_str    = end_date.isoformat()   + "Z"
            print(f"\n[ML] Fetching machine list (Memory: {mem:.1f}MB)...")
            # --- Step 1: Fetch machine list only ---
            machines = fetch_machine_list(start_str, end_str)
            if not machines:
                print("[ML] No machines found. Retrying next cycle.")
                time.sleep(ML_UPDATE_INTERVAL)
                continue
            print(f"[ML] Found {len(machines)} machines to train...")
            machines_processed = 0
            machines_skipped   = 0
            # --- Step 2: Process ONE machine at a time ---
            for machine_idx, machine_str in enumerate(machines):
                if tracker.is_cooling_down(machine_str):
                    machines_skipped += 1
                    continue
                is_valid, reason = validate_labels(machine_str, "test")
                if not is_valid:
                    machines_skipped += 1
                    tracker.mark_failed(machine_str)
                    continue
                try:
                    mem = get_memory_usage_mb()
                    print(f"[ML] Training {machine_idx + 1}/{len(machines)}: "
                          f"{machine_str} (Memory: {mem:.1f}MB)")
                    # Step 3: Fetch ONLY this machine's data
                    df = fetch_single_machine_data(
                        machine_str, start_str, end_str
                    )
                    if not has_sufficient_data(df, machine_str):
                        if df is not None:
                            del df
                        release_memory()
                        continue
                    # Step 4: Train and publish
                    metrics_set = train_single_machine(
                        machine_str,
                        df,
                        **ML_MODEL_CONFIG["live"],
                    )
                    del df
                    # FIX 10: full release after every machine
                    release_memory()
                    if metrics_set > 0:
                        machines_processed += 1
                        print(f"[ML] ✓ {machine_str}: {metrics_set} metrics set")
                    else:
                        print(f"[ML] ⚠️ {machine_str}: No metrics set")
                except Exception as e:
                    print(f"[ML] Error processing {machine_str}: {e}")
                    tracker.mark_failed(machine_str)
                    ML_ERRORS.labels(error_type="machine_processing").inc()
                    traceback.print_exc()
                    release_memory()
                time.sleep(INTER_MACHINE_SLEEP)
            # --- Cycle summary ---
            print(f"\n[ML] ✓ Cycle complete: {machines_processed}/"
                  f"{len(machines)} machines trained, "
                  f"{machines_skipped} skipped")
            MACHINES_TRAINED.set(machines_processed)
            # --- Periodic label pruning ---
            now = time.time()
            if now - last_prune >= LABEL_TIMEOUT:
                prune_stale_labels()
                last_prune = now
            ML_CYCLES.inc()
            LAST_ML_RUN.set(time.time())
            release_memory()
        except Exception as e:
            print(f"[ML ERROR] {e}")
            ML_ERRORS.labels(error_type="cycle").inc()
            traceback.print_exc()
            release_memory()
        print(f"[ML] Sleeping for {ML_UPDATE_INTERVAL}s...")
        time.sleep(ML_UPDATE_INTERVAL)


def check_backfill_already_done() -> bool:
    """
    Check if ml_predictions data already exists in InfluxDB.
    If we find predictions from more than 25 days ago, backfill was
    already run and we can skip it.
    """
    check_start = (datetime.now() - timedelta(days=28)).isoformat() + "Z"
    check_end   = (datetime.now() - timedelta(days=25)).isoformat() + "Z"
    
    query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {check_start}, stop: {check_end})
  |> filter(fn: (r) => r._measurement == "ml_predictions")
  |> limit(n: 1)
'''
    raw   = fetch_influx_data(query, timeout_secs=30)
    clean = clean_influx_csv(raw)
    
    if clean:
        try:
            df = pd.read_csv(StringIO(clean), low_memory=False, dtype=str)
            if len(df) > 0:
                print("[BACKFILL] ✓ Existing ml_predictions found "
                      f"({check_start[:10]} to {check_end[:10]})")
                return True
        except Exception:
            pass
    
    print("[BACKFILL] No existing ml_predictions found - full backfill needed")
    return False
# =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 4C-i: Backfill Setup & Chunk Loop Header
# =============================================================================
def backfill_historical_data(
    days_back: int   = ML_WINDOW_DAYS,
    chunk_hours: int = BACKFILL_CHUNK_HOURS,
):
    
    """
    Backfill ML predictions over historical data.
    FIX 11 — release_memory() replaces gc.collect(0) throughout.
    Same reasoning as FIX 10. Backfill processes many machines per chunk
    and without a full 3-generation sweep + malloc_trim the RSS grows
    monotonically across chunks instead of returning to baseline.
    """
    
    print(f"\n[BACKFILL] Starting backfill for {days_back} days "
          f"({chunk_hours}-hour chunks)...")

    # --- Skip if already done ---
    if check_backfill_already_done():
        print("[BACKFILL] ⏭️  Skipping - predictions already exist in InfluxDB")
        print("[BACKFILL] ⏭️  Delete ml_predictions measurement to force re-run")
        return

  
    end_date      = datetime.now()
    start_date    = end_date - timedelta(days=days_back)
    current_start = start_date
    chunk_num     = 1
    total_chunks  = int((days_back * 24 + chunk_hours - 1) / chunk_hours)
    tracker       = FailedMachineTracker()
    while current_start < end_date:
        current_end = min(
            current_start + timedelta(hours=chunk_hours),
            end_date,
        )
        start_str = current_start.isoformat() + "Z"
        end_str   = current_end.isoformat()   + "Z"
        progress  = (chunk_num / total_chunks) * 100
        mem       = get_memory_usage_mb()
        MEMORY_USAGE.set(mem)
        # --- Emergency memory relief ---
        if mem > MEMORY_CRITICAL_MB:
            print(f"\n[BACKFILL] 🚨 CRITICAL MEMORY: {mem:.1f}MB - "
                  f"Aggressive cleanup")
            release_memory()
            time.sleep(10)
            continue
        if mem > MEMORY_WARNING_MB:
            print(f"\n[BACKFILL] ⚠️ HIGH MEMORY: {mem:.1f}MB - Cleanup")
            release_memory()
            time.sleep(5)
        print(f"\n[BACKFILL] Chunk {chunk_num}/{total_chunks} "
              f"({progress:.1f}%) - Memory: {mem:.1f}MB")
        print(f"[BACKFILL] {start_str} to {end_str}")
        # --- Step 1: Fetch machine list only ---
        machines = fetch_machine_list(start_str, end_str, timeout=60)
        if not machines:
            print(f"[BACKFILL] No machines found for chunk {chunk_num}")
            current_start = current_end
            chunk_num += 1
            continue
        print(f"[BACKFILL] Found {len(machines)} machines "
              f"in chunk {chunk_num}")
        machines_processed = 0

        # =============================================================
        # Part 4C-ii: Backfill Machine Loop Body
        # Paste this indented inside the while current_start < end_date
        # block, directly after the machines_processed = 0 line above.
        # =============================================================
        # --- Step 2: Process ONE machine at a time ---
        for machine_idx, machine_str in enumerate(machines):
            # Skip machines still in cooldown
            if tracker.is_cooling_down(machine_str):
                continue
            is_valid, reason = validate_labels(machine_str, "test")
            if not is_valid:
                tracker.mark_failed(machine_str)
                continue
            try:
                if machine_idx % 5 == 0:
                    mem = get_memory_usage_mb()
                    print(f"[BACKFILL] Machine {machine_idx + 1}/"
                          f"{len(machines)} - Memory: {mem:.1f}MB")
                
                # Step 3: Fetch ONLY this machine for this chunk
                df = fetch_single_machine_data(
                    machine_str, start_str, end_str, timeout=120
                )
                if not has_sufficient_data(df, machine_str):
                    if df is not None:
                        del df
                    release_memory()
                    continue
                
                # Use chunk END time as prediction timestamp for backfill
                chunk_end_timestamp = int(current_end.timestamp() * 1000)
                
                # Step 4: Run pipeline once with correct backfill timestamp
                wide_df = prepare_wide_df(df)
                del df
                release_memory()
                
                metrics_set = 0
                if wide_df is not None:
                    target_fields, feature_cols, _ = classify_fields(wide_df)
                    X, Y = build_matrices(wide_df, target_fields, feature_cols)
                    del wide_df
                    release_memory()
                    
                    if X is not None and Y is not None:
                        model = fit_model(X, Y, **ML_MODEL_CONFIG["backfill"])
                        if model is not None:
                            predictions, lower_bounds, upper_bounds = calculate_bounds(model, X, Y)
                            del model, X, Y
                            release_memory()
                            
                            # Publish ONCE with correct historical timestamp
                            metrics_set = publish_predictions(
                                machine_str,
                                target_fields,
                                predictions,
                                lower_bounds,
                                upper_bounds,
                                timestamp=chunk_end_timestamp,
                            )
                            del predictions, lower_bounds, upper_bounds
                            release_memory()
                        else:
                            del X, Y
                            release_memory()
                
                if metrics_set > 0:
                    machines_processed += 1
                    print(f"[BACKFILL] ✓ {machine_str}: {metrics_set} metrics set "
                          f"@ {current_end.isoformat()}Z")
                else:
                    print(f"[BACKFILL] ⚠️ {machine_str}: No metrics set")

            except Exception as e:
                print(f"[BACKFILL] Error processing {machine_str}: {e}")
                tracker.mark_failed(machine_str)
                ML_ERRORS.labels(error_type="backfill_training").inc()
                release_memory()
            time.sleep(INTER_MACHINE_SLEEP)

        # --- Chunk footer ---
        print(f"[BACKFILL] ✓ Chunk {chunk_num}/{total_chunks} complete "
              f"({machines_processed}/{len(machines)} machines)")
        
        # Advance to next chunk
        current_start = current_end
        chunk_num    += 1
        
        # FIX 11: aggressive memory release between chunks
        release_memory()
        print(f"[BACKFILL] Memory after cleanup: "
              f"{get_memory_usage_mb():.1f}MB")
        print(f"[BACKFILL] Memory recovery sleep (5s)...")
        time.sleep(5)

    # --- All chunks complete ---
    print("\n[BACKFILL] ✓✓✓ All chunks completed!")
    # =============================================================================
# PowerTech ML Metrics Exporter — FIXED
# Part 5: Main Function & Entry Point
# =============================================================================
def main():
    print("=" * 60)
    print("  PowerTech ML Metrics Exporter — FIXED")
    print("  Dual-Track: Live Data + ML Predictions + Peer-RF")
    print("=" * 60)
    print(f"[MAIN] Initial memory: {get_memory_usage_mb():.1f}MB")
    if ENABLE_PEER_RF:
        try:
            from bridge_peer_rf import load_peer_rf_machines, merge_field_roles_from_peer_config

            peer_roles = merge_field_roles_from_peer_config()
            FIELD_ROLES.update(peer_roles)
            print(f"[MAIN] Peer-RF enabled for {len(load_peer_rf_machines())} machine(s), "
                  f"{sum(len(v) for v in load_peer_rf_machines().values())} target(s)")
        except Exception as e:
            print(f"[MAIN] Peer-RF config load failed: {e}")
    # --- Start Prometheus HTTP server ---
    PROMETHEUS_PORT = 8000
    try:
        start_http_server(PROMETHEUS_PORT, registry=REGISTRY)
        print(f"[MAIN] ✓ Prometheus server started on port {PROMETHEUS_PORT}")
    except Exception as e:
        print(f"[MAIN] ✗ Failed to start Prometheus server: {e}")
        traceback.print_exc()
        return
    # --- Peer-RF control API (enroll / status) on a separate port ---
    if ENABLE_PEER_RF:
        try:
            from bridge_peer_rf_api import start_peer_rf_control_server

            start_peer_rf_control_server()
        except Exception as e:
            print(f"[MAIN] ⚠️ Peer-RF control API failed to start: {e}")
            traceback.print_exc()
    # --- Optional backfill before live loops start ---
    try:
        print(f"[MAIN] Starting backfill ({ML_WINDOW_DAYS} days, "
              f"{BACKFILL_CHUNK_HOURS}h chunks)...")
        backfill_historical_data()
        print("[MAIN] ✓ Multivariate backfill complete.")
        if ENABLE_PEER_RF and ENABLE_PEER_RF_BACKFILL:
            from bridge_peer_rf import backfill_peer_rf_historical_data

            backfill_peer_rf_historical_data()
            print("[MAIN] ✓ Peer-RF backfill complete.")
    except Exception as e:
        print(f"[MAIN] ⚠️ Backfill failed (non-fatal): {e}")
        traceback.print_exc()
    # --- Start TRACK 1: Live data thread ---
    live_thread = threading.Thread(
        target=live_data_loop,
        name="LiveDataThread",
        daemon=True,
    )
    live_thread.start()
    print("[MAIN] ✓ Live data thread started (Track 1).")
    # --- Start TRACK 2: ML training thread ---
    ml_thread = threading.Thread(
        target=ml_training_loop,
        name="MLTrainingThread",
        daemon=True,
    )
    ml_thread.start()
    print("[MAIN] ✓ ML training thread started (Track 2).")
    print("[MAIN] ✓ All systems running. Press Ctrl+C to stop.\n")
    # --- Keep main thread alive + periodic health logging ---
    last_health_log = time.time()
    while True:
        try:
            time.sleep(5)
            # Watchdog: restart dead threads
            if not live_thread.is_alive():
                print("[MAIN] ⚠️ Live data thread died — restarting...")
                live_thread = threading.Thread(
                    target=live_data_loop,
                    name="LiveDataThread",
                    daemon=True,
                )
                live_thread.start()
            if not ml_thread.is_alive():
                print("[MAIN] ⚠️ ML training thread died — restarting...")
                ml_thread = threading.Thread(
                    target=ml_training_loop,
                    name="MLTrainingThread",
                    daemon=True,
                )
                ml_thread.start()
            # Periodic health log
            now = time.time()
            if now - last_health_log >= HEALTH_LOG_INTERVAL:
                mem           = get_memory_usage_mb()
                active_labels = len(ACTIVE_METRIC_LABELS)
                print(
                    f"[HEALTH] Memory: {mem:.1f}MB | "
                    f"Active labels: {active_labels} | "
                    f"Live thread: {'✓' if live_thread.is_alive() else '✗'} | "
                    f"ML thread: {'✓' if ml_thread.is_alive() else '✗'}"
                )
                MEMORY_USAGE.set(mem)
                last_health_log = now
        except KeyboardInterrupt:
            print("\n[MAIN] 🛑 Shutdown requested. Exiting cleanly...")
            break
        except Exception as e:
            print(f"[MAIN] Unexpected error in main loop: {e}")
            traceback.print_exc()
# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    main()
