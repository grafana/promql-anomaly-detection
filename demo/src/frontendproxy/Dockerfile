# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM envoyproxy/envoy:v1.30-latest@sha256:b5cc70f5fe5503858817e897ae1da5d873dc32cbc493790b4e330b8a42c4af9d
RUN apt-get update && apt-get install -y gettext-base && apt-get clean && rm -rf /var/lib/apt/lists/*

USER envoy
WORKDIR /home/envoy
COPY ./src/frontendproxy/envoy.tmpl.yaml envoy.tmpl.yaml

ENTRYPOINT ["/bin/sh", "-c", "envsubst < envoy.tmpl.yaml > envoy.yaml && envoy -c envoy.yaml;"]
