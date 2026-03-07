# Multi-stage C++ build for matching engine service
# Usage: docker build -t latency-attribution-cpp-execution -f cpp/matching_engine/Dockerfile .

FROM ubuntu:24.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ make \
    libgrpc++-dev libprotobuf-dev protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy proto files
COPY proto/ proto/

# Copy C++ source
COPY cpp/ cpp/

# Build
RUN mkdir -p cpp/build && cd cpp/build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) hft-execution

# ── Runtime ────────────────────────────────────────
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgrpc++1.51t64 libprotobuf32t64 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/cpp/build/matching_engine/hft-execution /usr/local/bin/hft-execution

ENV LISTEN_ADDR=":50055"

ENTRYPOINT ["/usr/local/bin/hft-execution"]
