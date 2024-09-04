# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
# Modifications:
# - [Jorge Creixell]: Modified [removed unnecessary parts for the purposes of this demo, added additional prometheus config]

DOCKER_COMPOSE_CMD ?= docker compose
DOCKER_COMPOSE_ENV=--env-file .env

.PHONY: start
start:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) -f docker-compose.yml up --force-recreate --remove-orphans --detach
	@echo ""
	@echo "OpenTelemetry Demo in minimal mode is running."
	@echo "Go to http://localhost:8080 for the demo UI."
	@echo "Go to http://localhost:8080/grafana/ for the Grafana UI."
	@echo "Go to http://localhost:8080/prometheus/ for the Prometheus UI."
	@echo "Go to http://localhost:8080/loadgen/ for the Load Generator UI."
	@echo "Go to https://opentelemetry.io/docs/demo/feature-flags/ to learn how to change feature flags."

.PHONY: stop
stop:
	$(DOCKER_COMPOSE_CMD) down --remove-orphans --volumes
	@echo ""
	@echo "OpenTelemetry Demo is stopped."
