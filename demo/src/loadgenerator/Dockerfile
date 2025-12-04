# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-slim-bookworm@sha256:78e702aee4d693e769430f0d7b4f4858d8ea3f1118dc3f57fee3f757d0ca64b1 as base

FROM base as builder
RUN apt-get -qq update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

COPY ./src/loadgenerator/requirements.txt .
RUN pip install --prefix="/reqs" -r requirements.txt

FROM base
WORKDIR /usr/src/app/
COPY --from=builder /reqs /usr/local
COPY ./src/loadgenerator/locustfile.py .
COPY ./src/loadgenerator/people.json .
ENV LOCUST_PLAYWRIGHT=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium
ENTRYPOINT locust --skip-log-setup
