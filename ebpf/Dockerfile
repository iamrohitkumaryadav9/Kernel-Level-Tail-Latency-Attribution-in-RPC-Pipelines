# =========================
# Build stage (newer clang)
# =========================
FROM golang:1.24-trixie AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    clang \
    llvm \
    libbpf-dev \
    linux-headers-amd64 \
    gcc \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/ebpf

COPY ebpf/go.mod ebpf/go.sum ./
RUN go mod download

COPY ebpf/headers/vmlinux.h headers/vmlinux.h
COPY ebpf/ .

RUN go generate ./cmd/... \
    && CGO_ENABLED=0 go build -o /bin/rqdelay ./cmd/

# =========================
# Runtime stage
# =========================
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /bin/rqdelay /usr/local/bin/rqdelay

CMD ["/usr/local/bin/rqdelay"]
