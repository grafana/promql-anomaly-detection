package rules

import "fmt"

// Wave is a group of rules that can be evaluated in parallel —
// all their dependencies are satisfied by previous waves.
type Wave []Rule

// Waves performs a topological sort of rules based on their PromQL
// references and returns ordered waves of rules ready for backfilling.
//
// The same metric name may be defined by multiple rules (e.g. anomaly:level
// in both adaptive.yml and robust.yml). Each definition is treated as an
// independent node: rules that reference a metric name depend on all
// definitions of it.
func Waves(rules []Rule) ([]Wave, error) {
	// Build a set of all recorded metric names → list of rule indices.
	recorded := make(map[string][]int, len(rules))
	for i, r := range rules {
		recorded[r.Record] = append(recorded[r.Record], i)
	}

	// Build adjacency: rule index → set of rule indices it depends on.
	deps := make([]map[int]struct{}, len(rules))
	for i, r := range rules {
		deps[i] = make(map[int]struct{})
		for _, ref := range MetricRefs(r.Node) {
			for _, j := range recorded[ref] {
				if j != i {
					deps[i][j] = struct{}{}
				}
			}
		}
	}

	// Kahn's algorithm.
	inDegree := make([]int, len(rules))
	for i := range rules {
		inDegree[i] = len(deps[i])
	}

	var waves []Wave
	remaining := len(rules)
	resolved := make([]bool, len(rules))

	for remaining > 0 {
		var indices []int
		for i := range rules {
			if !resolved[i] && inDegree[i] == 0 {
				indices = append(indices, i)
			}
		}
		if len(indices) == 0 {
			return nil, fmt.Errorf("cycle detected in rule dependencies")
		}
		var wave Wave
		for _, idx := range indices {
			wave = append(wave, rules[idx])
			resolved[idx] = true
			remaining--
			for i := range rules {
				if resolved[i] {
					continue
				}
				if _, ok := deps[i][idx]; ok {
					inDegree[i]--
				}
			}
		}
		waves = append(waves, wave)
	}

	return waves, nil
}
