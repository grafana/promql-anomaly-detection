package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/grafana/promql-anomaly-detection/harness/internal/backfill"
	"github.com/grafana/promql-anomaly-detection/harness/internal/fixture"
	"github.com/grafana/promql-anomaly-detection/harness/internal/rules"
	"github.com/grafana/promql-anomaly-detection/harness/internal/test"
	"github.com/spf13/cobra"
)

func main() {
	root := &cobra.Command{
		Use:   "harness",
		Short: "PromQL anomaly detection testing harness",
	}

	root.AddCommand(depsCmd())
	root.AddCommand(fixtureCmd())
	root.AddCommand(backfillCmd())
	root.AddCommand(testCmd())

	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

type depsRuleOutput struct {
	Record   string   `yaml:"record"`
	Interval string   `yaml:"interval,omitempty"`
	Deps     []string `yaml:"deps,omitempty"`
}

type depsWaveOutput struct {
	Wave  int                `yaml:"wave"`
	Rules []depsRuleOutput   `yaml:"rules"`
}

func depsCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "deps <rules-file> [rules-file...]",
		Short: "Print the dependency graph and evaluation waves as YAML",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			rs, err := rules.ParseFiles(args)
			if err != nil {
				return err
			}
			waves, err := rules.Waves(rs)
			if err != nil {
				return err
			}

			// Build a set of all recorded metric names for dep lookup.
			recorded := make(map[string]struct{}, len(rs))
			for _, r := range rs {
				recorded[r.Record] = struct{}{}
			}

			var out []depsWaveOutput
			for i, wave := range waves {
				wo := depsWaveOutput{Wave: i + 1}
				for _, r := range wave {
					ro := depsRuleOutput{Record: r.Record}
					if r.Interval > 0 {
						ro.Interval = r.Interval.String()
						// Trim trailing zeros: 5m0s → 5m
						if len(ro.Interval) > 2 && ro.Interval[len(ro.Interval)-2:] == "0s" {
							ro.Interval = ro.Interval[:len(ro.Interval)-2]
						}
					}
					seen := make(map[string]struct{})
					for _, ref := range rules.MetricRefs(r.Node) {
						if _, isRecorded := recorded[ref]; isRecorded && ref != r.Record {
							if _, already := seen[ref]; !already {
								ro.Deps = append(ro.Deps, ref)
								seen[ref] = struct{}{}
							}
						}
					}
					wo.Rules = append(wo.Rules, ro)
				}
				out = append(out, wo)
			}

			enc := yaml.NewEncoder(os.Stdout)
			enc.SetIndent(2)
			return enc.Encode(out)
		},
	}
}

func fixtureCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "fixture",
		Short: "Manage test fixtures",
	}
	cmd.AddCommand(fixtureExportCmd())
	cmd.AddCommand(fixtureLoadCmd())
	return cmd
}

func testCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "test <test-file> [test-file...]",
		Short: "Run acceptance tests against a fixture",
		Example: `  harness test tests/adaptive.yml`,
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			passed, failed := 0, 0
			for _, path := range args {
				results, err := test.RunFile(cmd.Context(), path)
				if err != nil {
					return fmt.Errorf("%s: %w", path, err)
				}
				for _, r := range results {
					if r.Passed {
						fmt.Printf("  PASS  %s\n", r.Case.Description)
						passed++
					} else {
						fmt.Printf("  FAIL  %s: %s\n", r.Case.Description, r.Failure)
						failed++
					}
				}
			}
			fmt.Printf("\n%d passed, %d failed\n", passed, failed)
			if failed > 0 {
				os.Exit(1)
			}
			return nil
		},
	}
}

func fixtureLoadCmd() *cobra.Command {
	var (
		fixtureDir     string
		ruleFiles      []string
		start          string
		end            string
		prometheusData string
	)

	cmd := &cobra.Command{
		Use:   "load",
		Short: "Backfill a fixture and inject the results into the demo Prometheus",
		Example: `  harness fixture load \
    --fixture  fixtures/smoke \
    --rules    rules/adaptive.yml \
    --start    2025-01-01T00:00:00Z \
    --end      2025-01-01T00:30:00Z \
    --prom-data demo/prometheus-data`,
		RunE: func(cmd *cobra.Command, args []string) error {
			startT, err := time.Parse(time.RFC3339, start)
			if err != nil {
				return fmt.Errorf("invalid --start: %w", err)
			}
			endT, err := time.Parse(time.RFC3339, end)
			if err != nil {
				return fmt.Errorf("invalid --end: %w", err)
			}
			// Create the work dir on the same filesystem as prometheusData so that
			// copyDirWritable can write blocks directly without crossing a device boundary.
			workDir, err := os.MkdirTemp(filepath.Dir(prometheusData), "harness-backfill-*")
			if err != nil {
				return fmt.Errorf("creating work dir: %w", err)
			}
			defer os.RemoveAll(workDir)

			return backfill.Run(cmd.Context(), backfill.Options{
				FixtureDir:  fixtureDir,
				WorkDir:     workDir,
				RuleFiles:   ruleFiles,
				Start:       startT,
				End:         endT,
				SnapshotDir: prometheusData,
			})
		},
	}

	cmd.Flags().StringVar(&fixtureDir, "fixture", "", "Source fixture directory")
	cmd.Flags().StringArrayVar(&ruleFiles, "rules", nil, "Rule file(s) to evaluate (repeatable)")
	cmd.Flags().StringVar(&start, "start", "", "Start time in RFC3339 format")
	cmd.Flags().StringVar(&end, "end", "", "End time in RFC3339 format")
	cmd.Flags().StringVar(&prometheusData, "prom-data", "../demo/prometheus-data", "Prometheus data directory to inject blocks into")

	cmd.MarkFlagRequired("fixture")
	cmd.MarkFlagRequired("rules")
	cmd.MarkFlagRequired("start")
	cmd.MarkFlagRequired("end")

	return cmd
}

func backfillCmd() *cobra.Command {
	var (
		fixtureDir string
		workDir    string
		ruleFiles  []string
		start      string
		end        string
	)

	cmd := &cobra.Command{
		Use:   "backfill",
		Short: "Evaluate recording rule waves against a fixture and write results into a work directory",
		Example: `  harness backfill \
    --fixture fixtures/weekly-seasonal \
    --work    /tmp/backfill-work \
    --rules   rules/adaptive.yml \
    --start   2025-01-01T00:00:00Z \
    --end     2025-01-08T00:00:00Z`,
		RunE: func(cmd *cobra.Command, args []string) error {
			startT, err := time.Parse(time.RFC3339, start)
			if err != nil {
				return fmt.Errorf("invalid --start: %w", err)
			}
			endT, err := time.Parse(time.RFC3339, end)
			if err != nil {
				return fmt.Errorf("invalid --end: %w", err)
			}
			return backfill.Run(cmd.Context(), backfill.Options{
				FixtureDir: fixtureDir,
				WorkDir:    workDir,
				RuleFiles:  ruleFiles,
				Start:      startT,
				End:        endT,
			})
		},
	}

	cmd.Flags().StringVar(&fixtureDir, "fixture", "", "Source fixture directory (TSDB blocks, read-only)")
	cmd.Flags().StringVar(&workDir, "work", "", "Work directory where results are written")
	cmd.Flags().StringArrayVar(&ruleFiles, "rules", nil, "Rule file(s) to evaluate (repeatable)")
	cmd.Flags().StringVar(&start, "start", "", "Start time in RFC3339 format")
	cmd.Flags().StringVar(&end, "end", "", "End time in RFC3339 format")

	cmd.MarkFlagRequired("fixture")
	cmd.MarkFlagRequired("work")
	cmd.MarkFlagRequired("rules")
	cmd.MarkFlagRequired("start")
	cmd.MarkFlagRequired("end")

	return cmd
}

func fixtureExportCmd() *cobra.Command {
	var (
		url        string
		query      string
		start      string
		end        string
		step       time.Duration
		out        string
		remapStart string
	)

	cmd := &cobra.Command{
		Use:   "export",
		Short: "Export a metric from Prometheus as TSDB blocks for use as a fixture",
		Example: `  harness fixture export \
    --query       'anomaly:request:rate5m' \
    --start       2025-01-01T00:00:00Z \
    --end         2025-01-08T00:00:00Z \
    --out         fixtures/smoke \
    --remap-start 2025-01-01T00:00:00Z`,
		RunE: func(cmd *cobra.Command, args []string) error {
			startT, err := time.Parse(time.RFC3339, start)
			if err != nil {
				return fmt.Errorf("invalid --start: %w", err)
			}
			endT, err := time.Parse(time.RFC3339, end)
			if err != nil {
				return fmt.Errorf("invalid --end: %w", err)
			}
			opts := fixture.ExportOptions{
				URL:    url,
				Query:  query,
				Start:  startT,
				End:    endT,
				Step:   step,
				OutDir: out,
			}
			if remapStart != "" {
				t, err := time.Parse(time.RFC3339, remapStart)
				if err != nil {
					return fmt.Errorf("invalid --remap-start: %w", err)
				}
				opts.RemapStart = t
			}
			return fixture.Export(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&url, "url", "http://localhost:8080/prometheus", "Prometheus base URL (default points to the demo)")
	cmd.Flags().StringVar(&query, "query", "", "PromQL selector or metric name to export (required)")
	cmd.Flags().StringVar(&start, "start", "", "Start time in RFC3339 format (required)")
	cmd.Flags().StringVar(&end, "end", "", "End time in RFC3339 format (required)")
	cmd.Flags().DurationVar(&step, "step", fixture.DefaultStep, "Query resolution")
	cmd.Flags().StringVar(&out, "out", "", "Output directory for TSDB blocks (required)")
	cmd.Flags().StringVar(&remapStart, "remap-start", "", "Shift exported timestamps so the first sample lands at this time (RFC3339)")

	cmd.MarkFlagRequired("query")
	cmd.MarkFlagRequired("start")
	cmd.MarkFlagRequired("end")
	cmd.MarkFlagRequired("out")

	return cmd
}
