# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM gradle:8-jdk17@sha256:cc93961ddceffeeae953f92e9a05d64d585ae2a693331209c06e1ae6303ab9f7 AS builder

WORKDIR /usr/src/app/

COPY ./src/frauddetectionservice/ ./
COPY ./pb/ ./src/main/proto/
RUN gradle shadowJar

# -----------------------------------------------------------------------------

FROM gcr.io/distroless/java17-debian11@sha256:b7caa7280b8a0f227e0b2c16dde1a9abddfeedbaef97ce68ac349310d1d589be

ARG version=2.4.0
WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/build/libs/frauddetectionservice-1.0-all.jar ./
ADD --chmod=644 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$version/opentelemetry-javaagent.jar /app/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS=-javaagent:/app/opentelemetry-javaagent.jar

ENTRYPOINT [ "java", "-jar", "frauddetectionservice-1.0-all.jar" ]
