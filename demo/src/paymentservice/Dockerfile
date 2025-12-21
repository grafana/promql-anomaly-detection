# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM node:21-alpine@sha256:78c45726ea205bbe2f23889470f03b46ac988d14b6d813d095e2e9909f586f93 AS build

WORKDIR /usr/src/app/

COPY ./src/paymentservice/package*.json ./

RUN apk add --no-cache python3 make g++ && npm ci --omit=dev

# -----------------------------------------------------------------------------

FROM node:21-alpine@sha256:78c45726ea205bbe2f23889470f03b46ac988d14b6d813d095e2e9909f586f93

USER node
WORKDIR /usr/src/app/
ENV NODE_ENV production

COPY --chown=node:node --from=build /usr/src/app/node_modules/ ./node_modules/
COPY ./src/paymentservice/ ./
COPY ./pb/demo.proto ./

EXPOSE ${PAYMENT_SERVICE_PORT}
ENTRYPOINT [ "npm", "run", "start" ]
