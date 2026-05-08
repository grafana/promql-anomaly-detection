package test

import (
	"context"
	"fmt"
	"math"
	"os"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/prometheus/prometheus/promql"
	"github.com/prometheus/prometheus/tsdb"

	"github.com/grafana/promql-anomaly-detection/harness/internal/backfill"
)

// Suite is the top-level structure of a test YAML file.
type Suite struct {
	// FixtureDir is the path to the exported source metric blocks.
	FixtureDir string `yaml:"fixture"`
	// RuleFiles lists the recording rule YAML files to evaluate.
	RuleFiles []string `yaml:"rules"`
	// TimeRange defines the backfill window.
	TimeRange struct {
		Start string `yaml:"start"`
		End   string `yaml:"end"`
	} `yaml:"time_range"`
	// Cases are the individual assertions to run after backfill.
	Cases []Case `yaml:"cases"`
}

// Case is a single test assertion.
type Case struct {
	Description string    `yaml:"description"`
	Query       string    `yaml:"query"`
	At          string    `yaml:"at"`
	Expect      []Expect  `yaml:"expect"`
}

// Expect describes what a single series in the query result should look like.
type Expect struct {
	// MinSeries asserts that at least this many series are returned.
	MinSeries int `yaml:"min_series"`
	// Value is the expected sample value. Only checked if set (non-zero).
	Value float64 `yaml:"value"`
	// Tolerance is the allowed relative deviation from Value (e.g. 0.05 = 5%).
	Tolerance float64 `yaml:"tolerance"`
}

// Result holds the outcome of a single test case.
type Result struct {
	Case    Case
	Passed  bool
	Failure string
}

// RunFile parses a test suite YAML file, runs the backfill, and executes all cases.
func RunFile(ctx context.Context, path string) ([]Result, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading test file: %w", err)
	}
	var suite Suite
	if err := yaml.Unmarshal(data, &suite); err != nil {
		return nil, fmt.Errorf("parsing test file: %w", err)
	}

	start, err := time.Parse(time.RFC3339, suite.TimeRange.Start)
	if err != nil {
		return nil, fmt.Errorf("invalid time_range.start: %w", err)
	}
	end, err := time.Parse(time.RFC3339, suite.TimeRange.End)
	if err != nil {
		return nil, fmt.Errorf("invalid time_range.end: %w", err)
	}

	workDir, err := os.MkdirTemp("", "harness-test-*")
	if err != nil {
		return nil, fmt.Errorf("creating work dir: %w", err)
	}
	defer os.RemoveAll(workDir)

	var results []Result

	err = backfill.RunWithDB(ctx, backfill.Options{
		FixtureDir: suite.FixtureDir,
		WorkDir:    workDir,
		RuleFiles:  suite.RuleFiles,
		Start:      start,
		End:        end,
	}, func(db *tsdb.DB, engine *promql.Engine) error {
		for _, c := range suite.Cases {
			r, err := runCase(ctx, engine, db, c)
			if err != nil {
				return fmt.Errorf("case %q: %w", c.Description, err)
			}
			results = append(results, r)
		}
		return nil
	})

	return results, err
}

func runCase(ctx context.Context, engine *promql.Engine, db *tsdb.DB, c Case) (Result, error) {
	at, err := time.Parse(time.RFC3339, c.At)
	if err != nil {
		return Result{Case: c}, fmt.Errorf("invalid 'at': %w", err)
	}

	q, err := engine.NewInstantQuery(ctx, db, nil, c.Query, at)
	if err != nil {
		return Result{Case: c}, fmt.Errorf("creating query: %w", err)
	}
	res := q.Exec(ctx)
	q.Close()
	if res.Err != nil {
		return Result{Case: c, Failure: fmt.Sprintf("query error: %s", res.Err)}, nil
	}

	vec, ok := res.Value.(promql.Vector)
	if !ok {
		return Result{Case: c, Failure: fmt.Sprintf("expected vector, got %T", res.Value)}, nil
	}

	for _, exp := range c.Expect {
		if exp.MinSeries > 0 && len(vec) < exp.MinSeries {
			return Result{
				Case:    c,
				Failure: fmt.Sprintf("expected at least %d series, got %d", exp.MinSeries, len(vec)),
			}, nil
		}

		if exp.Value != 0 {
			for _, sample := range vec {
				if math.IsNaN(sample.F) {
					return Result{Case: c, Failure: "sample value is NaN"}, nil
				}
				tol := exp.Tolerance
				if tol == 0 {
					tol = 0.01 // default 1%
				}
				diff := math.Abs(sample.F-exp.Value) / math.Abs(exp.Value)
				if diff > tol {
					return Result{
						Case:    c,
						Failure: fmt.Sprintf("value %.4f outside %.0f%% tolerance of expected %.4f (diff %.2f%%)", sample.F, tol*100, exp.Value, diff*100),
					}, nil
				}
			}
		}
	}

	return Result{Case: c, Passed: true}, nil
}
