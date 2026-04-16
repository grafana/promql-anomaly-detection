package fixture

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/go-kit/log"
	"github.com/go-kit/log/level"
	prometheus_api "github.com/prometheus/client_golang/api"
	prometheus_v1 "github.com/prometheus/client_golang/api/prometheus/v1"
	"github.com/prometheus/common/model"
	"github.com/prometheus/prometheus/model/labels"
	"github.com/prometheus/prometheus/storage"
	"github.com/prometheus/prometheus/tsdb"
)

const (
	// DefaultStep is the resolution used when querying Prometheus.
	DefaultStep = 30 * time.Second
	// maxSamplesPerQuery keeps us under Prometheus's default 11000-sample limit.
	maxSamplesPerQuery = 10000
)

// ExportOptions configures a fixture export.
type ExportOptions struct {
	URL    string
	Query  string
	Start  time.Time
	End    time.Time
	Step   time.Duration
	OutDir string
	// RemapStart, if non-zero, shifts all exported timestamps so that the
	// first sample lands at RemapStart instead of Start. Useful for placing
	// fixture data at a stable historic date (e.g. 2025-01-01) so it never
	// overlaps with a live Prometheus instance.
	RemapStart time.Time
}

// Export queries a Prometheus server and writes the results as TSDB blocks
// in OutDir, ready to be used as a fixture for backfill.
func Export(ctx context.Context, opts ExportOptions) error {
	if err := os.MkdirAll(opts.OutDir, 0o755); err != nil {
		return fmt.Errorf("creating output dir: %w", err)
	}

	client, err := prometheus_api.NewClient(prometheus_api.Config{Address: opts.URL})
	if err != nil {
		return fmt.Errorf("creating prometheus client: %w", err)
	}
	api := prometheus_v1.NewAPI(client)

	logger := log.NewLogfmtLogger(log.NewSyncWriter(os.Stderr))

	var tsOffset int64
	if !opts.RemapStart.IsZero() {
		tsOffset = opts.RemapStart.UnixMilli() - opts.Start.UnixMilli()
	}

	// BlockWriter groups samples into 2h TSDB blocks automatically.
	writer, err := tsdb.NewBlockWriter(logger, opts.OutDir, tsdb.DefaultBlockDuration)
	if err != nil {
		return fmt.Errorf("creating block writer: %w", err)
	}
	defer writer.Close()

	// Chunk the time range to stay under the per-query sample limit.
	chunkDuration := time.Duration(maxSamplesPerQuery) * opts.Step
	total := 0

	for chunkStart := opts.Start; chunkStart.Before(opts.End); chunkStart = chunkStart.Add(chunkDuration) {
		chunkEnd := chunkStart.Add(chunkDuration)
		if chunkEnd.After(opts.End) {
			chunkEnd = opts.End
		}

		result, warnings, err := api.QueryRange(ctx, opts.Query, prometheus_v1.Range{
			Start: chunkStart,
			End:   chunkEnd,
			Step:  opts.Step,
		})
		if err != nil {
			return fmt.Errorf("querying %s [%s, %s]: %w", opts.Query, chunkStart, chunkEnd, err)
		}
		for _, w := range warnings {
			level.Warn(logger).Log("msg", "query warning", "warning", w)
		}

		matrix, ok := result.(model.Matrix)
		if !ok {
			return fmt.Errorf("expected matrix result, got %T", result)
		}

		app := writer.Appender(ctx)
		for _, series := range matrix {
			lbls := modelMetricToLabels(series.Metric)
			var ref storage.SeriesRef
			for _, sample := range series.Values {
				ts := sample.Timestamp.Time().UnixMilli() + tsOffset
				val := float64(sample.Value)
				var err error
				ref, err = app.Append(ref, lbls, ts, val)
				if err != nil {
					return fmt.Errorf("appending sample: %w", err)
				}
				total++
			}
		}
		if err := app.Commit(); err != nil {
			return fmt.Errorf("committing samples: %w", err)
		}
		level.Info(logger).Log("msg", "exported chunk", "start", chunkStart, "end", chunkEnd, "total_samples", total)
	}

	if _, err := writer.Flush(ctx); err != nil {
		return fmt.Errorf("flushing blocks: %w", err)
	}

	level.Info(logger).Log("msg", "export complete", "dir", opts.OutDir, "total_samples", total)
	return nil
}

func modelMetricToLabels(m model.Metric) labels.Labels {
	b := labels.NewBuilder(labels.EmptyLabels())
	for k, v := range m {
		b.Set(string(k), string(v))
	}
	return b.Labels()
}
