# Peer Random Forest (Module 5 vs peers)



Goal: anomaly bands for **Module 5 current** using **Random Forest** where inputs are **only** modules 1–4 and 6–8 (same machine, same time) — not Module 5’s own past.



## Compared to existing panels



| Panel | Mechanism |

|-------|-----------|

| vs. Peer Band (Flux) | Instant mean ± 2σ across peer `_field`s |

| RandomForest ML (Influx) | Multivariate RF on same machine (`ml_predictions` without `model` tag) |

| **RandomForest vs Peers (Influx)** | RF with `model=peer_rf` in `ml_predictions` |



## Implemented



| Piece | Location |

|-------|----------|

| Config | [`exporter/peer_rf_config.json`](../exporter/peer_rf_config.json) |

| Logic | [`exporter/bridge_peer_rf.py`](../exporter/bridge_peer_rf.py) + hooks in `bridge.py` |

| Historical bands | Peer backfill writes a **time series** per chunk (not one point per chunk) |

| Delete peer data | [`exporter/delete_peer_rf_predictions.sh`](../exporter/delete_peer_rf_predictions.sh) |

| Deploy | [`scripts/sync-exporter-electramet.sh`](../scripts/sync-exporter-electramet.sh) |



### Environment



- `ENABLE_PEER_RF=1` (default) — live training after each multivariate cycle

- `ENABLE_PEER_RF_BACKFILL=1` (default) — 30-day series backfill on container start

- `INFLUX_TOKEN`, `INFLUX_HOST`, `INFLUX_ORG`, `INFLUX_BUCKET`



### Grafana + Graft



- Fixture: `vikshana-graft-app/scripts/fixtures/panel-module5-randomforest-peer-influx.json`

- Graft: `runProgrammaticAddPeerRfPanel`



Example prompt:



```text

On dashboard uid 6gawrgawrgragg, add panel "Module 5 Current — RandomForest vs Peers (Influx)" next to the Module 5 peer-band panel.

```



## Evaluation



Keep **vs. Peer Band** and add **RandomForest vs Peers**. Compare on labeled incidents using the dashboard time picker: lead time, false positives when all modules ramp, misses when only M5 drifts.


