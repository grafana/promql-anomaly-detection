# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM envoyproxy/envoy:v1.38-latest@sha256:8146b97ee61a42cd216514709e4e3198af75f014974e3d9f310aef9c901fcbdf
RUN apt-get update && apt-get install -y gettext-base && apt-get clean && rm -rf /var/lib/apt/lists/*

USER envoy
WORKDIR /home/envoy
COPY ./src/frontendproxy/envoy.tmpl.yaml envoy.tmpl.yaml

ENTRYPOINT ["/bin/sh", "-c", "envsubst < envoy.tmpl.yaml > envoy.yaml && envoy -c envoy.yaml;"]
