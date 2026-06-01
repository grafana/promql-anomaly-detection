# Hybrid anomaly detection (PowerTech ML + promql-anomaly-detection)

This fork supports a **two-track** model: RandomForest bands from your ML exporter for plant/process signals, and PromQL recording-rule bands from this framework for everything else.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│  InfluxDB (powertechdata)                                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ML exporter (:8000)                                             │
│  • machine_metrics          (actual, ~15s)                       │
│  • machine_metric_expected  (ML, ~5m)                            │
│  • machine_metric_upper/lower_bound                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Prometheus scrape
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Grafana dashboards — Track 1 (module/pressure panels)           │
│  • A: machine_metrics{machine, field}                          │
│  • B/C/D: last_over_time(machine_metric_*[6m–7m])                 │
│  • Alerts: rules/examples/powertech_hybrid.yml (PowerTechML*)    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Other Prometheus metrics (node_exporter, OTel, apps)           │
│  Tagged with anomaly_name + anomaly_strategy via recording rules │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  rules/adaptive.yml + rules/robust.yml                           │
│  • anomaly:upper_band / anomaly:lower_band / anomaly:level       │
│  • Alert: AnomalyDetected                                        │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Grafana dashboards — Track 2 (infra / generic anomaly panels)   │
└─────────────────────────────────────────────────────────────────┘
```

## What to use when

| Signal type | Band source | Grafana queries | Alert rule group |
|-------------|-------------|-----------------|------------------|
| PLC / module current, pressure, process fields | ML exporter | `machine_metrics` + `machine_metric_*` + `[6m]` lookback on bounds | `PowerTechMLAlerts` |
| Host CPU/RAM, generic infra, OTel service metrics | promql-anomaly-detection | `anomaly:level` + `last_over_time(anomaly:*_band[2m])` | `AnomalyDetected` |

## Prometheus configuration

Load core strategies and both example sets:

```yaml
rule_files:
  - /etc/prometheus/rules/adaptive.yml
  - /etc/prometheus/rules/robust.yml
  - /etc/prometheus/rules/examples/powertech_hybrid.yml
  # Optional: keep node_exporter.yml only if not duplicating powertech_hybrid infra section
```

## Grafana panel recipes

### Track 1 — ML panel (module / pressure)

| Target | Query |
|--------|--------|
| Actual | `machine_metrics{machine="$machine", field="$field"}` |
| Expected | `last_over_time(machine_metric_expected{machine="$machine", field="$field"}[7m])` |
| Upper | `last_over_time(machine_metric_upper_bound{machine="$machine", field="$field"}[7m])` |
| Lower | `last_over_time(machine_metric_lower_bound{machine="$machine", field="$field"}[7m])` |

Use dashboard variables `machine` and `field` (or fixed labels per panel).

### Track 2 — Framework panel

See main [README](../README.md). Requires `anomaly_name` on the input series.

## Rules of thumb

1. **Never** put `anomaly_name` on `machine_metrics` or `machine_metric_*` — avoids double banding.
2. ML lookback **`[6m]`–`[7m]`** on bound metrics only; not on `machine_metrics`.
3. Framework bands need **~24h** history before they stabilize; ML bands follow your **30d** training window.
4. One dashboard can mix both panel types; label sections clearly for operators.

## Graft / AI assistant

When using Graft (vikshana-graft-app), ask explicitly:

- “Update **ML track** module 3 panel” → `machine` / `field` queries with lookback on bounds.
- “Add **infra anomaly** panel for node CPU” → `anomaly_name` tagging + `anomaly:*` bands.

## Verification

```promql
# ML track — should return series
machine_metrics{machine!=""}
machine_metric_upper_bound{machine!=""}

# Framework track — after tagging rules run
anomaly:upper_band{anomaly_name!=""}
```

```promql
# ML staleness helper (from powertech_hybrid.yml)
powertech:ml_bounds_stale
```
