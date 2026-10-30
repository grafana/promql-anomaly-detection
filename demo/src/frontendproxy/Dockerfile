# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM envoyproxy/envoy:v1.39-latest@sha256:1fde8efee875e4e4f6a39a90fe49d9338e1caacce5762034ef9fb4bbb9ad6aed
RUN apt-get update && apt-get install -y gettext-base && apt-get clean && rm -rf /var/lib/apt/lists/*

USER envoy
WORKDIR /home/envoy
COPY ./src/frontendproxy/envoy.tmpl.yaml envoy.tmpl.yaml

ENTRYPOINT ["/bin/sh", "-c", "envsubst < envoy.tmpl.yaml > envoy.yaml && envoy -c envoy.yaml;"]
