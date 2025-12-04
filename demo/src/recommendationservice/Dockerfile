# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-slim-bookworm@sha256:78e702aee4d693e769430f0d7b4f4858d8ea3f1118dc3f57fee3f757d0ca64b1 as base

#
# Fetch requirements
#
FROM base as builder
RUN apt-get -qq update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app/
COPY ./src/recommendationservice/requirements.txt ./

RUN pip install --upgrade pip
RUN pip install --prefix="/reqs" -r requirements.txt

#
# Build gRPC files
#
FROM base as grpc-builder
WORKDIR /usr/src/app/
COPY ./pb/ ./proto/

RUN python -m pip install grpcio-tools==1.59.2
RUN python -m grpc_tools.protoc -I=./proto/ --python_out=./ --grpc_python_out=./ ./proto/demo.proto

#
# Runtime
#
FROM base as runtime
WORKDIR /usr/src/app/
COPY --from=builder /reqs /usr/local
COPY --from=grpc-builder /usr/src/app/ .
COPY ./src/recommendationservice/ ./

RUN opentelemetry-bootstrap -a install

EXPOSE ${RECOMMENDATION_SERVICE_PORT}
ENTRYPOINT [ "opentelemetry-instrument", "python", "recommendation_server.py" ]
