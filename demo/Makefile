# SPDX-License-Identifier: Apache-2.0
# Provenance-includes-location: https://github.com/open-telemetry/opentelemetry-demo/blob/main/Makefile
# Provenance-includes-license: Apache-2.0
# Provenance-includes-copyright: The OpenTelemetry Authors.
# Modifications [Grafana Labs]:
# - Removed unnecessary parts for the purposes of this demo
# - Exposed Prometheus url
# - Changed log messages

DOCKER_COMPOSE_CMD ?= docker compose
DOCKER_COMPOSE_ENV=--env-file .env

.PHONY: start
start:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) build frontendproxy
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) up --force-recreate --remove-orphans --detach
	@echo ""
	@echo "PromQL Anomaly Detection Demo is running."
	@echo "Go to http://localhost:8080 for the demo UI."
	@echo "Go to http://localhost:8080/grafana/ for the Grafana UI."
	@echo "Go to http://localhost:8080/prometheus/ for the Prometheus UI."
	@echo "Go to http://localhost:8080/loadgen/ for the Load Generator UI."

.PHONY: stop
stop:
	$(DOCKER_COMPOSE_CMD) down --remove-orphans --volumes
	@echo ""
	@echo "PromQL Anomaly Detection Demo is stopped."
