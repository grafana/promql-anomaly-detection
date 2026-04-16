package rules

import (
	"fmt"
	"os"
	"time"

	"github.com/prometheus/prometheus/model/rulefmt"
	"github.com/prometheus/prometheus/promql/parser"
)

// Rule is a recording rule with its parsed PromQL expression.
type Rule struct {
	Record   string
	Expr     string
	Node     parser.Expr
	Labels   map[string]string // extra labels to attach to all output series (from the rule's `labels:` section)
	Interval time.Duration     // evaluation interval from the parent rule group; 0 means use default
}

// ParseFiles parses one or more rule YAML files and returns all recording rules.
func ParseFiles(paths []string) ([]Rule, error) {
	var rules []Rule
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("reading %s: %w", path, err)
		}
		rgs, errs := rulefmt.Parse(data)
		if len(errs) > 0 {
			return nil, fmt.Errorf("parsing %s: %w", path, errs[0])
		}
		for _, rg := range rgs.Groups {
			interval := time.Duration(rg.Interval)
			for _, r := range rg.Rules {
				if r.Record.Value == "" {
					continue // skip alerting rules
				}
				node, err := parser.ParseExpr(r.Expr.Value)
				if err != nil {
					return nil, fmt.Errorf("parsing expr for %s: %w", r.Record.Value, err)
				}
				lbls := make(map[string]string, len(r.Labels))
				for k, v := range r.Labels {
					lbls[k] = v
				}
				rules = append(rules, Rule{
					Record:   r.Record.Value,
					Expr:     r.Expr.Value,
					Node:     node,
					Labels:   lbls,
					Interval: interval,
				})
			}
		}
	}
	return rules, nil
}

// MetricRefs returns all metric names referenced in a PromQL expression.
func MetricRefs(node parser.Expr) []string {
	var refs []string
	parser.Inspect(node, func(n parser.Node, _ []parser.Node) error {
		if vs, ok := n.(*parser.VectorSelector); ok && vs.Name != "" {
			refs = append(refs, vs.Name)
		}
		return nil
	})
	return refs
}
