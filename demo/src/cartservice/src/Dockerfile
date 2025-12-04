# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# https://mcr.microsoft.com/v2/dotnet/sdk/tags/list
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:8.0.302@sha256:02fdc848bbda5d57d9211a72c99bd665b421206002d66b8bc2cc0b2297c227fa AS builder
ARG TARGETARCH

WORKDIR /usr/src/app/

COPY ./src/cartservice/ ./
COPY ./pb/ ./pb/

RUN dotnet restore ./src/cartservice.csproj -v d -r linux-musl-$TARGETARCH

RUN dotnet publish ./src/cartservice.csproj -v d -r linux-musl-$TARGETARCH --no-restore -o /cartservice

# -----------------------------------------------------------------------------

# https://mcr.microsoft.com/v2/dotnet/runtime-deps/tags/list
FROM mcr.microsoft.com/dotnet/runtime-deps:8.0.6-alpine3.20@sha256:ce27c5bb26805aeeb726ae50b2f17bf58bef3818aeb9e189d2b8b7e25fcd46dc

WORKDIR /usr/src/app/
COPY --from=builder /cartservice/ ./

ENV DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE=false

EXPOSE ${CART_SERVICE_PORT}
ENTRYPOINT [ "./cartservice" ]
