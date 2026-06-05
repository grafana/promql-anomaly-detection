# PowerTech ML exporter (`bridge.py`)

RandomForest exporter that reads **InfluxDB** (`powertechdata`), publishes **Prometheus** metrics on port **8000**, and writes historical bands to **`ml_predictions`** for Grafana Flux panels.

This is **Track 1** in the [hybrid guide](../docs/hybrid-powertech.md). It is **not** the PromQL recording-rule framework in `rules/` (Track 2).

## Deployed location (ElectraMet)

On EC2 the live copy is mounted into the `data_bridge` container:

```text
~/ptw_data/Cloud/Docker/prometheus/bridge.py
~/ptw_data/Cloud/Docker/prometheus/bridge_peer_rf.py
~/ptw_data/Cloud/Docker/prometheus/peer_rf_config.json
~/ptw_data/Cloud/Docker/prometheus/.influx_token   # not in git — chmod 600
~/ptw_data/Cloud/Docker/prometheus/prometheus.yml
```

`docker-compose.yml` should mount:

```yaml
volumes:
  - .../bridge.py:/app/bridge.py
  - .../bridge_peer_rf.py:/app/bridge_peer_rf.py
  - .../peer_rf_config.json:/app/peer_rf_config.json
  - .../.influx_token:/secrets/influx_token:ro
environment:
  - INFLUX_TOKEN_FILE=/secrets/influx_token
```

**Source of truth should be this Git repo**, not only the Docker folder.

## What the script does today

1. Per **machine**, fetch a wide table: one row per timestamp, one column per Influx `_field`.
2. **`classify_fields()`** — auto-labels columns as **targets** (predicted) vs **features** (inputs), plus `hour` / `minute`.
3. **`RandomForestRegressor`** — multi-output: predicts all **target** columns from **feature** columns at the **same timestamp** (not lagged self-history).
4. **Bands** — residual std on the training window × `STD_MULTIPLIER` (default 2σ).
5. **Outputs**
   - Prometheus: `machine_metrics`, `machine_metric_{expected,upper,lower}_bound`
   - Influx: `ml_predictions` measurement (`upper`, `lower`, `expected` fields; tags `machine`, `field`)

6. **Backfill** — `ML_WINDOW_DAYS` (30) in chunks; skipped if old `ml_predictions` already exist.

## Peer-based anomaly (Module 5 vs modules 1–4,6–8)

The exporter already supports explicit roles via **`FIELD_ROLES`** at the top of `bridge.py`:

```python
FIELD_ROLES = {
    "Module5_Current_A": "target",
    "Module1_Current_A": "feature",
    "Module2_Current_A": "feature",
    # ... modules 3,4,6,7,8 ...
}
```

That trains RF to predict **only** Module 5 from **peer currents** (and time columns), not from a separate “own history” series.

**Recommended extensions** (not implemented yet):

| Change | Why |
|--------|-----|
| Write `model=peer` tag (or `field=Module5_Current_A_peer`) on `ml_predictions` | So peer-RF backfill does not overwrite multivariate bands |
| `train_peer_mode` flag per machine | Run peer targets without predicting every high-CV field |
| New Grafana panel fixture | Flux queries filter `model=peer` for historical evaluation |

Until then, auto-`classify_fields()` may treat many module currents as **targets**, so Module 5 bands may be driven mostly by **time features**, not peers — verify with `FIELD_ROLES` for your evaluation.

## Configuration

```bash
cp exporter/.env.example exporter/.env   # on server only
export $(grep -v '^#' exporter/.env | xargs)
python exporter/bridge.py
```

Prometheus scrape config: see [`deploy/powertech-prometheus.yml`](../deploy/powertech-prometheus.yml).

## Operations

- **Force backfill:** run [`delete_ml_predictions.sh`](delete_ml_predictions.sh) (uses `INFLUX_TOKEN`), then restart `data_bridge`.
- **Do not** tag `machine_metrics` with `anomaly_name` for Track 2 — see hybrid doc. The deployed `machine_mapping.yml` on some hosts may still do this; that double-bands ML metrics.

## Security

Production copies on EC2 historically embedded Influx tokens in `bridge.py` and shell scripts. This repo version expects **`INFLUX_TOKEN` in the environment**. Rotate any token that was ever committed or pasted in chat.
