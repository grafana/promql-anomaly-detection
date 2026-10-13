package backfill

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/go-kit/log"
	"github.com/go-kit/log/level"
	prometheus_client "github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/prometheus/model/labels"
	"github.com/prometheus/prometheus/promql"
	"github.com/prometheus/prometheus/tsdb"

	"github.com/grafana/promql-anomaly-detection/harness/internal/rules"
)

const defaultInterval = time.Minute

// Options configures a backfill run.
type Options struct {
	// FixtureDir contains the source TSDB blocks (read-only).
	FixtureDir string
	// WorkDir is where backfill writes results. Fixture blocks are copied here first.
	WorkDir string
	// RuleFiles are the recording rule YAML files to evaluate.
	RuleFiles []string
	// Start and End define the backfill time range.
	Start time.Time
	End   time.Time
}

// Run copies the fixture blocks into WorkDir and evaluates all recording rule
// waves in-process.
func Run(ctx context.Context, opts Options) error {
	return RunWithDB(ctx, opts, nil)
}

// RunWithDB is like Run but calls dbFn with the open TSDB and PromQL engine
// after all waves have been evaluated — useful for running queries against
// the backfilled data before the DB is closed (e.g. for testing).
func RunWithDB(ctx context.Context, opts Options, dbFn func(*tsdb.DB, *promql.Engine) error) error {
	logger := log.NewLogfmtLogger(log.NewSyncWriter(os.Stderr))

	if err := copyBlocks(opts.FixtureDir, opts.WorkDir); err != nil {
		return fmt.Errorf("copying fixture blocks: %w", err)
	}

	maxBlockTime, err := maxBlockTime(opts.WorkDir)
	if err != nil {
		return fmt.Errorf("reading fixture block metadata: %w", err)
	}

	tsdbOpts := tsdb.DefaultOptions()
	// The head accepts samples back to its max time minus this window. The head's
	// max time starts at the newest block's max time, so the window has to reach
	// from there back to the start of the backfill range.
	tsdbOpts.OutOfOrderTimeWindow = max(maxBlockTime, opts.End.UnixMilli()) - opts.Start.UnixMilli() + time.Hour.Milliseconds()

	db, err := tsdb.Open(opts.WorkDir, logger, prometheus_client.NewRegistry(), tsdbOpts, nil)
	if err != nil {
		return fmt.Errorf("opening tsdb: %w", err)
	}
	defer db.Close()

	engine := promql.NewEngine(promql.EngineOpts{
		Logger:               logger,
		MaxSamples:           50_000_000,
		Timeout:              5 * time.Minute,
		EnableAtModifier:     true,
		EnableNegativeOffset: true,
		NoStepSubqueryIntervalFn: func(rangeMillis int64) int64 {
			return int64(defaultInterval / time.Millisecond)
		},
	})

	rs, err := rules.ParseFiles(opts.RuleFiles)
	if err != nil {
		return err
	}
	waves, err := rules.Waves(rs)
	if err != nil {
		return err
	}

	for i, wave := range waves {
		level.Info(logger).Log("msg", "evaluating wave", "wave", i+1, "rules", len(wave))
		if err := evaluateWave(ctx, engine, db, wave, opts.Start, opts.End, logger); err != nil {
			return fmt.Errorf("wave %d: %w", i+1, err)
		}
	}

	if dbFn != nil {
		return dbFn(db, engine)
	}
	return nil
}

func evaluateWave(ctx context.Context, engine *promql.Engine, db *tsdb.DB, wave rules.Wave, start, end time.Time, logger log.Logger) error {
	for _, rule := range wave {
		interval := rule.Interval
		if interval == 0 {
			interval = defaultInterval
		}

		level.Info(logger).Log("msg", "evaluating rule", "record", rule.Record, "interval", interval)

		app := db.Appender(ctx)
		total := 0

		for ts := start; !ts.After(end); ts = ts.Add(interval) {
			q, err := engine.NewInstantQuery(ctx, db, nil, rule.Expr, ts)
			if err != nil {
				_ = app.Rollback()
				return fmt.Errorf("creating query for %s at %s: %w", rule.Record, ts, err)
			}
			res := q.Exec(ctx)
			q.Close()
			if res.Err != nil {
				// Non-fatal: rule may reference data that doesn't exist yet at this timestamp.
				level.Debug(logger).Log("msg", "query returned error", "record", rule.Record, "ts", ts, "err", res.Err)
				continue
			}

			var vec promql.Vector
			switch v := res.Value.(type) {
			case promql.Vector:
				vec = v
			case promql.Scalar:
				// Scalar recording rules (e.g. `expr: 0.5`) produce a single
				// sample with no labels, matching Prometheus's own behaviour.
				vec = promql.Vector{promql.Sample{Metric: labels.EmptyLabels(), F: v.V, T: v.T}}
			default:
				continue
			}

			for _, sample := range vec {
				lbls := withRuleLabels(sample.Metric, rule.Record, rule.Labels)
				if _, err := app.Append(0, lbls, ts.UnixMilli(), sample.F); err != nil {
					_ = app.Rollback()
					return fmt.Errorf("appending sample for %s: %w", rule.Record, err)
				}
				total++
			}
		}

		if err := app.Commit(); err != nil {
			return fmt.Errorf("committing %s: %w", rule.Record, err)
		}
		level.Info(logger).Log("msg", "rule done", "record", rule.Record, "samples", total)
	}
	return nil
}

// withRuleLabels returns lbls with __name__ set to name and any extra rule labels applied.
func withRuleLabels(lbls labels.Labels, name string, extra map[string]string) labels.Labels {
	b := labels.NewBuilder(lbls)
	b.Set(labels.MetricName, name)
	for k, v := range extra {
		b.Set(k, v)
	}
	return b.Labels()
}

// maxBlockTime returns the largest maxTime across the block directories in dir.
func maxBlockTime(dir string) (int64, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, err
	}
	var maxTime int64
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, e.Name(), "meta.json"))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return 0, err
		}
		var meta tsdb.BlockMeta
		if err := json.Unmarshal(data, &meta); err != nil {
			return 0, fmt.Errorf("parsing %s: %w", e.Name(), err)
		}
		maxTime = max(maxTime, meta.MaxTime)
	}
	return maxTime, nil
}

// copyBlocks copies TSDB block directories from src into dst.
// It skips the WAL and any non-block entries so the fixture stays read-only.
func copyBlocks(src, dst string) error {
	if err := os.MkdirAll(dst, 0o777); err != nil {
		return err
	}
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue // skip loose files (lock, etc.)
		}
		name := e.Name()
		if name == "wal" || name == "chunks_head" {
			continue // skip WAL — not needed for querying sealed blocks
		}
		if err := copyDir(filepath.Join(src, name), filepath.Join(dst, name)); err != nil {
			return fmt.Errorf("copying block %s: %w", name, err)
		}
	}
	return nil
}

func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}
		return copyFile(path, target, info.Mode())
	})
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}
