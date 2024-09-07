# PromQL Anomaly Detection Framework

Framework for anomaly detection in time series data using Prometheus/PromQL.

  - Does not require external systems.
  - Performant at scale.
  - Designed to minimize false positives.


## Getting Started

To see a demo of the framework in action, run the following commands:

```bash
cd demo
make start
```

You will need to have Docker installed to run the demo.  The demo will start a Prometheus instance, a Grafana instance, a node exporter instance and a version of the OTEL demo.

Once everything is running, head to [http://localhost:8080/grafana](http://localhost:8080/grafana) to access the Grafana UI. From there, you will find a dashboard called "Anomalies" within the "Anomalies" Folder.

The demo dashboard shows how any metric tagged with the `anomaly_name` and `anomaly_type` labels will be used used for anomaly detection and displayed in the dashboard.

<p align="center"><img src="docs/sources/assets/dashboard.png" alt="Anomalies Dashboard"></p>

You can simulate anomalies by altering the traffic patterns in the load generator, which can be accessed at [http://localhost:8080/loadgen/](http://localhost:8080/loadgen/).

## Usage

Any metric with the `anomaly_name` and `anomaly_type` labels set will be considered for anomaly detection. The `/examples` folder shows how recording rules could be used for such purposes. This allows defining custom aggregation dimensions for the bands, such as "service", "job", "instance" or any other label.

Anomaly bands can be overlayed on top of the original time series in Grafana, allowing for easy visualization of the detected anomalies. An example of dashboard can be found in the `demo/src/grafana/provisioning/dashboards/anomalies` folder.

The framework is designed to be extended and adapted to different uses cases, while providing a solid foundation for anomaly detection in time series data.

## How it works

The framework is composed of two main components: a set of base recording rules that generate anomaly bands for each desired time series, and a set of alerting rules that detect when a time series crosses the anomaly bands.

### Recording Rules

The recording rules use a combination of average and standard deviation over time to generate the bands. Smoothing is applied to the bands to reduce false positives and improve the stability of the bands in the presence of extreme outliers. In addition, a high pass filter is applied pre-smoothing to remove low frequency samples, increasing the sensitivity of the bands.

Seasonality is also incorporated into the bands, allowing the bands to adapt to recurrent patterns happening daily or weekly. Custom seasonality patterns can be added easily.

### Alerting Rules

Alerting rules are used to detect when a time series crosses the anomaly bands. They can be found in the `/rules` folder
