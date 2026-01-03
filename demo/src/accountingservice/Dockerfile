# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM mcr.microsoft.com/dotnet/aspnet:8.0@sha256:39b1272ca9a951df21a72df91cba0344a1a01a9e85ae61c90cb4db059ac62388 AS base
USER app
WORKDIR /app

FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:2c7794fef0107c1815193dc157ed3f8ca60eb504a439806a29b6f9bf1ce73679 AS build
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
