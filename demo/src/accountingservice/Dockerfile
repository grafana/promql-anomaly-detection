# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM mcr.microsoft.com/dotnet/aspnet:8.0@sha256:86cbca9fc08f712273edef23b0dff38d81fe0d20486662946f98b164c3af6d33 AS base
USER app
WORKDIR /app

FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:8f710c2f7f3e60392fba1d408dcd9c30c26295bd4ded326cb7cf6cc6fd66cfde AS build
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
