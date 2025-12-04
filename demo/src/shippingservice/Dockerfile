# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM --platform=${BUILDPLATFORM} rust:1.76@sha256:d36f9d8a9a4c76da74c8d983d0d4cb146dd2d19bb9bd60b704cdcf70ef868d3a as builder

ARG TARGETARCH TARGETPLATFORM BUILDPLATFORM

RUN echo Building on ${BUILDPLATFORM} for ${TARGETPLATFORM}

# Check if we are doing cross-compilation, if so we need to add in some more dependencies and run rustup
RUN if [ "${TARGETPLATFORM}" = "${BUILDPLATFORM}" ] ; then \
        apt-get update && apt-get install --no-install-recommends -y g++ libc6-dev libprotobuf-dev protobuf-compiler ca-certificates; \
    elif [ "${TARGETPLATFORM}" = "linux/arm64" ] ; then \
        apt-get update && apt-get install --no-install-recommends -y g++-aarch64-linux-gnu libc6-dev-arm64-cross libprotobuf-dev protobuf-compiler ca-certificates && \
        rustup target add aarch64-unknown-linux-gnu && \
        rustup toolchain install stable-aarch64-unknown-linux-gnu; \
    else \
        echo "${TARGETPLATFORM} is not supported"; \
        exit 1; \
    fi

WORKDIR /app/

COPY /src/shippingservice/ /app/
COPY /pb/ /app/proto/

# Compile or crosscompile
RUN if [ "${TARGETPLATFORM}" = "${BUILDPLATFORM}" ] ; then \
        cargo build -r --features="dockerproto"; \
    elif [ "${TARGETPLATFORM}" = "linux/arm64" ] ; then \
        env CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
            CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
            CXX_aarch64_unknown_linux_gnu=aarch64-linux-gnu-g++ \
        cargo build -r --features="dockerproto" --target aarch64-unknown-linux-gnu && \
        cp /app/target/aarch64-unknown-linux-gnu/release/shippingservice /app/target/release/shippingservice; \
    else \
        echo "${TARGETPLATFORM} is not supported"; \
        exit 1; \
    fi


ENV GRPC_HEALTH_PROBE_VERSION=v0.4.24
RUN wget -qO/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/${GRPC_HEALTH_PROBE_VERSION}/grpc_health_probe-linux-${TARGETARCH} && \
    chmod +x /bin/grpc_health_probe

FROM debian:bookworm-slim@sha256:b4aa902587c2e61ce789849cb54c332b0400fe27b1ee33af4669e1f7e7c3e22f as release

WORKDIR /app
COPY --from=builder /app/target/release/shippingservice /app/shippingservice
COPY --from=builder /bin/grpc_health_probe /bin/grpc_health_probe

EXPOSE ${SHIPPING_SERVICE_PORT}
ENTRYPOINT ["/app/shippingservice"]
