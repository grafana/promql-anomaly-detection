# PromQL Anomaly Detection Framework

Framework for anomaly detection in time series data using Prometheus/PromQL.

  - Does not require external systems.
  - Performant at scale.
  - Simple yet powerful.
  - Extensible.


## Getting Started

> [!WARNING]  
The demo is provided for illustrative purposes only and should not be run in production environments.

To see a demo of the framework in action, run the following commands:

```bash
cd demo
make start
```

You will need to have Docker installed to run the demo.  The demo will start a Prometheus instance, a Grafana instance, a node exporter instance and a version of the OTEL demo.

Once everything is running, head to [http://localhost:8080/grafana](http://localhost:8080/grafana) to access the Grafana UI. From there, you will find a dashboard called "Anomalies" within the "Anomalies" folder.

The demo dashboard shows how any metric tagged with the `anomaly_name` label will be used for anomaly detection and displayed in the dashboard.

<p align="center"><img src="docs/sources/assets/dashboard.png" alt="Anomalies Dashboard"></p>

You can simulate anomalies by altering the traffic patterns in the load generator, which can be accessed at [http://localhost:8080/loadgen/](http://localhost:8080/loadgen/).

> [!IMPORTANT]
> Most strategies require 24-26 hours of data to be fully trained. Until enough data is processed, anomaly bands may be more sensitive than otherwise expected.

## Usage

In order to use the framework, you will need to copy the rules folder to a folder accessible by your Prometheus instance, and update
your Prometheus configuration to use them.

For example, you could add the following to your Prometheus config file:

```yaml
rule_files:
- /etc/prometheus/rules/*.yml
```

In addition, you will need recording (or relabel) rules that tag your existing metrics for anomaly detection. Any metric with the `anomaly_name` labels set will be considered for anomaly detection.

- `anomaly_name`: The name of the anomaly metric. This is used uniquely identify metrics.

Multiple anomaly detection strategies are supported. You can optionally select the algorithm used for anomaly detection via the `anomaly_strategy` label:

- `anomaly_strategy`: The algorithm used for anomaly detection. The following algorithms are supported:
  - `adaptive` (default): Based on the mean and standard deviation, with a smoothing function over 26h and a high pass filter to improve sensitivity. Ideal for quick detection of short term changes, while minimizing false positives for recurring events. Works best with normally distributed metrics.
  - `robust`: Based on the median and MAD (median absolute deviation). This algorithm is robust to outliers. Use it when you want to detect long term changes and for spiky or non-normally distributed metrics.

Optionally, the `anomaly_type` label can be used to define more granular per-type thresholds:

- `anomaly_type`: It allows defining different thresholds and multipliers per metric type. The following types are supported by default:
  - `requests`: The request rate for a given service.
  - `latency`: The latency for a given service (for example, the 95th percentile).
  - `errors`: The error rate for a given service.
  - `resource`: A gauge representing a resource (for example, cpu or memory usage).

The `rules/examples` folder shows how recording rules can be used for selecting metrics to be used for anomaly detection.

Anomaly bands can be overlayed on top of your original time series panels in Grafana, allowing for easy visualization of the detected anomalies. An example dashboard can be found in the `demo/src/grafana/provisioning/dashboards/anomalies` folder.

The framework is designed to be extended and adapted to different uses cases, while providing a solid foundation for anomaly detection in time series data.

## How it works

The framework is composed of two main components: a set of base recording rules that generate anomaly bands for each desired time series, and a set of alerting rules that detect when a time series crosses the anomaly bands.

### Recording Rules

The recording rules use a set of different strategies to generate upper and lower bounds, forming bands. Typically, they use a combination of short term, long term and margin bands to compose the final bands.

Short term bands expand based on variability observed within a pre-defined period (typically 24-26 hours).

Seasonality is incorporated into long term bands, allowing the bands to adapt to recurrent patterns happening daily or weekly.

Margin bands provide a minimum band width when variability is too low.

### Alerting Rules

Alerting rules are used to detect when a time series crosses the anomaly bands for a significant period of time.
