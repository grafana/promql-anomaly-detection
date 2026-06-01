# PowerTech hybrid anomaly — operator cheat sheet

One-page reference for **Track 1 (ML exporter)** and **Track 2 (promql-anomaly-detection)**.

## Which track?

| You care about… | Track | Prometheus metrics |
|-----------------|-------|-------------------|
| Module current, pressure, PLC/process fields | **1 — ML** | `machine_metrics`, `machine_metric_expected`, `machine_metric_upper_bound`, `machine_metric_lower_bound` |
| Server CPU/RAM, generic infra | **2 — Framework** | `anomaly:level`, `anomaly:upper_band`, `anomaly:lower_band` (needs `anomaly_name` on inputs) |

**Never** put `anomaly_name` on ML exporter metrics.

## Grafana panel queries (Track 1)

| Line | Query |
|------|--------|
| Actual | `machine_metrics{machine="YOUR_MACHINE", field="YOUR_FIELD"}` |
| Expected | `last_over_time(machine_metric_expected{machine="YOUR_MACHINE", field="YOUR_FIELD"}[7m])` |
| Upper | `last_over_time(machine_metric_upper_bound{machine="YOUR_MACHINE", field="YOUR_FIELD"}[7m])` |
| Lower | `last_over_time(machine_metric_lower_bound{machine="YOUR_MACHINE", field="YOUR_FIELD"}[7m])` |

- **No lookback** on `machine_metrics` (updates ~15s).
- **Use `[6m]`–`[7m]`** on bound metrics (ML refresh ~5m).

## Useful PromQL checks

```promql
# ML exporter alive?
up{job="influxdb-bridge"}  # or your scrape job name for :8000

# Any ML bounds?
count(machine_metric_upper_bound)

# Actual above upper band (same logic as alert)
machine_metrics > on(machine, field) group_left() last_over_time(machine_metric_upper_bound[7m])

# Framework bands (Track 2)
anomaly:upper_band{anomaly_name!=""}

# ML bounds stale >15m
powertech:ml_bounds_stale
```

## Alerts (after `powertech_hybrid.yml` loaded)

| Alert | Meaning |
|-------|---------|
| `PowerTechMLAboveUpperBound` | Actual above ML upper band 5m+ |
| `PowerTechMLBelowLowerBound` | Actual below ML lower band 5m+ |
| `PowerTechMLBoundsStale` | No fresh ML bound 15m+ — check ML exporter |
| `AnomalyDetected` | Track 2 — framework band breach |

## ML exporter health

| Metric | Healthy signal |
|--------|----------------|
| `ml_last_run_timestamp` | Updates ~every 300s |
| `live_last_run_timestamp` | Updates ~every 15s |
| `ml_process_memory_mb` | Below warning threshold (~3GB) |
| `ml_errors_total` | Not increasing steadily |

## Asking Graft (build 12+)

- “Update **Track 1** module 5 panel bounds with `[7m]` lookback”
- “List panels for dashboard uid …” (use panel index table)
- “Add **Track 2** node CPU anomaly panel”

Confirm Graft reports **dashboard version** after save before refreshing.

## Files on Prometheus server

```
/etc/prometheus/rules/adaptive.yml
/etc/prometheus/rules/robust.yml
/etc/prometheus/rules/powertech_hybrid.yml    # must be in rules/ root (not only examples/)
/etc/prometheus/rules/examples/*.yml         # requires rule_files line in prometheus.yml
```

Reload: `curl -X POST http://localhost:9090/-/reload` (inside Prometheus network) or restart `prometheus` container.

## More detail

See [hybrid-powertech.md](./hybrid-powertech.md).
