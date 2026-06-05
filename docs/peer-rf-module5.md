# Peer Random Forest (Module 5 vs peers)

Goal: anomaly bands for **Module 5 current** using **Random Forest** where inputs are **only** modules 1–4 and 6–8 (same machine, same time) — not Module 5’s own past.

## Compared to existing panels

| Panel | Mechanism |
|-------|-----------|
| vs. Peer Band (Flux) | Instant mean ± 2σ across peer `_field`s |
| History Comparison / RF Influx (today) | `bridge.py` → `ml_predictions` for each **target** field; default auto-classification may not isolate peers |
| **Peer RF (proposed)** | `bridge.py` + `FIELD_ROLES` + separate `ml_predictions` tag + new Grafana panel |

## Implementation checklist (`exporter/bridge.py`)

1. **Set roles** for machine `2406-176021` (or load from YAML):

   ```python
   FIELD_ROLES = {
       "Module5_Current_A": "target",
       "Module1_Current_A": "feature",
       "Module2_Current_A": "feature",
       "Module3_Current_A": "feature",
       "Module4_Current_A": "feature",
       "Module6_Current_A": "feature",
       "Module7_Current_A": "feature",
       "Module8_Current_A": "feature",
   }
   ```

2. **Restrict targets** — train/publish only `Module5_Current_A` in peer mode (avoid predicting every pressure/current on the machine in the same model).

3. **Influx write** — add tag `model=peer_rf` (or field tag `Module5_Current_A_peer`) in `write_predictions_batch_to_influx`.

4. **Backfill** — run 30-day (or incident window) backfill into the new tag; use dashboard time picker on the new panel.

5. **Grafana** — clone `vikshana-graft-app/scripts/fixtures/panel-module5-randomforest-ml-influx.json`; point B–D at `model=peer_rf` (or new field tag).

## Evaluation

Keep the existing **vs. Peer Band** panel. Add **Module 5 — RandomForest vs Peers (Influx)**. Compare both on May 11–12 and other labeled incidents: lead time, false positives when all modules ramp together, misses when only M5 drifts.
