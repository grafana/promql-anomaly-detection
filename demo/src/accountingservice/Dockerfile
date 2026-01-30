# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM mcr.microsoft.com/dotnet/aspnet:8.0@sha256:4b8f0b08534833b39bb662fb19a65e78cb086f5ca8dd35de3f87026de8885be4 AS base
USER app
WORKDIR /app

FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:aa05b91be697b83229cb000b90120f0783604ad74ed92a0b45cdf3d1a9c873de AS build
ARG BUILD_CONFIGURATION=Release
WORKDIR /src
COPY ["/src/accountingservice/", "AccountingService/"]
COPY ["/pb/demo.proto", "AccountingService/proto/"]
RUN dotnet restore "./AccountingService/AccountingService.csproj"
WORKDIR "/src/AccountingService"

RUN dotnet build "./AccountingService.csproj" -c $BUILD_CONFIGURATION -o /app/build

FROM build AS publish
ARG BUILD_CONFIGURATION=Release
RUN dotnet publish "./AccountingService.csproj" --use-current-runtime -c $BUILD_CONFIGURATION -o /app/publish /p:UseAppHost=false

FROM base AS final
WORKDIR /app
COPY --from=publish /app/publish .

USER root
RUN mkdir -p "/var/log/opentelemetry/dotnet"
RUN chown app "/var/log/opentelemetry/dotnet"
RUN chown app "/app/instrument.sh"
USER app

ENTRYPOINT ["./instrument.sh", "dotnet", "AccountingService.dll"]
