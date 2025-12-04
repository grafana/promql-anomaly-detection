# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM eclipse-temurin:21-jdk@sha256:ec2005c536f3661c6ef1253292c9c623e582186749a3ef2ed90903d1aaf74640 as builder

WORKDIR /usr/src/app/

COPY ./src/adservice/gradlew* ./src/adservice/settings.gradle* ./src/adservice/build.gradle ./
COPY ./src/adservice/gradle ./gradle

RUN ./gradlew
RUN ./gradlew downloadRepos

COPY ./src/adservice/ ./
COPY ./pb/ ./proto
RUN ./gradlew installDist -PprotoSourceDir=./proto

# -----------------------------------------------------------------------------

FROM eclipse-temurin:21-jre@sha256:4332b7939ba5b7fabde48f4da21ebe45a4f8943d5b3319720c321ac577e65fb1

ARG version=2.6.0
WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/ ./
ADD --chmod=644 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$version/opentelemetry-javaagent.jar /usr/src/app/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar

EXPOSE ${AD_SERVICE_PORT}
ENTRYPOINT [ "./build/install/opentelemetry-demo-ad-service/bin/AdService" ]
