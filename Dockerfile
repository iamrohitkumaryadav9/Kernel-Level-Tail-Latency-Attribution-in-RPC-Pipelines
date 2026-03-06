# Multi-stage build: one Dockerfile for all services.
# Usage: docker build --build-arg SERVICE=gateway -t latency-attribution-gateway .

FROM golang:1.24-alpine AS builder

ARG SERVICE=gateway
WORKDIR /app

# Copy module files first (layer caching)
COPY go.mod go.sum ./
RUN go mod download

# Copy source
COPY . .

# Build the specific service
RUN CGO_ENABLED=0 GOOS=linux go build -o /bin/service ./services/${SERVICE}/

# ── Runtime ────────────────────────────────────────
FROM alpine:3.19

RUN apk --no-cache add ca-certificates

COPY --from=builder /bin/service /usr/local/bin/service

# Default env vars (overridden by K8s manifests)
ENV LISTEN_ADDR=":50051"
ENV DELAY_US="50"
ENV DELAY_MODE="busyspin"

ENTRYPOINT ["/usr/local/bin/service"]
