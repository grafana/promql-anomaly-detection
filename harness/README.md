# Testing harness

The harness evaluates recording rule chains against captured TSDB fixtures and checks the results. It is the primary tool for verifying changes to files under `rules/`.

## How it works

**The challenge.** Recording rules depend on each other. `anomaly:upper_band` depends on `anomaly:robust:mad_lt`, which depends on `anomaly:robust:abs_dev_lt`, which depends on `anomaly:robust:select`, which depends on raw metrics from the OTel demo. You can't evaluate them in any order — you have to get the order right.

**Step 1: the fixture.** Instead of scraping live metrics, we snapshot a small slice of real data from Prometheus into TSDB block files and commit them to the repo. Fixtures live under `fixtures/` — each subdirectory is an independent snapshot. These are the raw ingredients — the input metrics the rules will be evaluated against.

**Step 2: wave ordering.** Before evaluating anything, the harness reads all the rule files and builds a dependency graph — rule A references metric B, so A must come after B. It then sorts them into waves using Kahn's topological sort. The first wave has no dependencies (scalar constants like `0.5`). The next wave depends only on the fixture data. Each subsequent wave depends on the previous ones.

**Step 3: backfill.** The harness opens the fixture blocks in an in-process TSDB, then walks through time minute by minute and evaluates each wave in order. For each rule at each timestamp, it runs the PromQL expression against the DB and writes the results back into the same DB. By the time the last wave runs, all the intermediate metrics from earlier waves are already in the DB and queryable.

This is exactly what Prometheus does at runtime — the harness just does it offline, in a temp directory, at whatever time range you want.

**Step 4: assertions.** Once the backfill is done, the test runner queries the DB at a specific timestamp and checks the results — are there at least N series? Is this constant exactly 0.5? Is the lower band below the upper band? If anything is wrong, it fails with a clear message.

## Prerequisites

- Go 1.22+

## Quick start

```bash
cd harness
make build   # produces bin/harness
make test    # run acceptance tests
```

## Workflow: changing a recording rule

1. Edit the rule under `rules/`.
2. Run `make test` to run acceptance tests against the committed fixtures.
3. If a test fails because the change is intentional, update the test case.
4. Optionally load the fixture into the demo to inspect results visually (see below).

## Fixtures

Fixtures are small TSDB block snapshots that serve as reproducible inputs for tests. They are committed to the repo under `fixtures/`.

### Capturing a new fixture

Export the source metric from any Prometheus instance (the demo or a production one) with timestamps remapped to a stable historic date so the fixture never overlaps with a live instance:

```bash
cd demo && make start
# ... let the demo run for at least a few hours ...

cd ../harness
make fixture-export \
  QUERY='anomaly:request:rate5m' \
  START='<actual-start-in-RFC3339>' \
  END='<actual-end-in-RFC3339>' \
  OUT='fixtures/smoke' \
  REMAP_START='2025-01-01T00:00:00Z'
```

Commit the resulting blocks. The `START`/`END` used for backfill and tests should match the remapped range (starting at `REMAP_START`).

### Loading a fixture into the demo

To visually inspect rule output in Grafana before committing:

```bash
cd demo && make start

cd ../harness
make fixture-load \
  FIXTURE='fixtures/smoke' \
  START='2025-01-01T00:00:00Z' \
  END='2025-01-01T00:30:00Z'
```

Restart Prometheus to pick up the injected blocks, then open Grafana and set the time range to match the fixture window.

## Acceptance tests

Test files live in `tests/`. Each file specifies a fixture, the rule files to evaluate, a backfill time range, and a list of assertions:

```yaml
fixture: fixtures/smoke
rules:
  - ../rules/adaptive.yml

time_range:
  start: 2025-01-01T00:00:00Z
  end:   2025-01-01T00:30:00Z

cases:
  - description: upper band is computed
    query: 'anomaly:upper_band{anomaly_strategy="adaptive"}'
    at: 2025-01-01T00:15:00Z
    expect:
      - min_series: 1

  - description: threshold constant is 0.5
    query: 'anomaly:adaptive:threshold_by_covar'
    at: 2025-01-01T00:15:00Z
    expect:
      - value: 0.5
        tolerance: 0.01
```

Each test runs a full in-process backfill into a temporary directory — no running Prometheus required. Assertions can check series presence (`min_series`) or exact values with optional tolerance (`value`, `tolerance`).
