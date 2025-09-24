## Rules

Each yaml file in this folder implements a different anomaly detection strategy. Each strategy can contain as many recording and alerting rules
as necessary, but 3 metrics including the `anomaly_strategy` label must be exposed:

- `anomaly:upper_band`: Represents the upper limit of the anomaly band.
- `anomaly:lower_band`: Represents the lower limit of the anomaly band.
- `anomaly:level`: The line used for alerts, when it falls outside of the bands for a certain period of time. 

In addition, a selection mechanism is required to ensure only metrics tagged with a specific strategy are processed. Example:

```yaml
- record: anomaly:<my_strategy>:select
    expr: |-
        {anomaly_name!="", anomaly_select="", anomaly_strategy="<my_strategy>"}
    labels:
        anomaly_select: "1"
```

Note how `anomaly_select` is used to avoid processing cycles, since all metrics derived from subsequent recording rules
should include both `anomaly_strategy` and `anomaly_select`.

## Examples

The examples folder shows how recording rules can be used to "tag" metrics to be processed by the anomaly detection framework.
