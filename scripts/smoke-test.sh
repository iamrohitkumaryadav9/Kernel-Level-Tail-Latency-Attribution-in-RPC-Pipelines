#!/usr/bin/env bash
# smoke-test.sh — Starts all 5 pipeline services + Redis locally,
# sends a test gRPC request, and reports the result.
#
# Usage: ./scripts/smoke-test.sh
# Requires: Redis running (or docker), grpcurl installed

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$PROJECT_ROOT/bin"
PROTO_FILE="$PROJECT_ROOT/proto/order.proto"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PIDS=()

cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    # Stop Redis if we started it via Docker
    docker stop smoke-redis 2>/dev/null || true
    docker rm smoke-redis 2>/dev/null || true
    echo -e "${GREEN}✓ All processes stopped${NC}"
}
trap cleanup EXIT

echo -e "${YELLOW}═══ Local Smoke Test ═══${NC}\n"

# ── Step 1: Ensure binaries exist ──────────────────
echo "Checking binaries..."
for svc in gateway auth risk marketdata execution; do
    if [[ ! -f "$BIN_DIR/$svc" ]]; then
        echo -e "${RED}✗ Missing binary: bin/$svc — run 'make build' first${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ All binaries found${NC}"

# ── Step 2: Start Redis ───────────────────────────
echo "Starting Redis..."
if command -v redis-server &>/dev/null; then
    redis-server --port 6379 --daemonize yes --loglevel warning
    echo -e "${GREEN}✓ Redis started (native)${NC}"
elif command -v docker &>/dev/null; then
    docker run -d --name smoke-redis -p 6379:6379 redis:7-alpine >/dev/null 2>&1 || true
    sleep 1
    echo -e "${GREEN}✓ Redis started (docker)${NC}"
else
    echo -e "${RED}✗ Neither redis-server nor docker found. Install one.${NC}"
    exit 1
fi

# ── Step 3: Start services (reverse order: leaf first) ──
echo "Starting pipeline services..."

DELAY_US=50 DELAY_MODE=busyspin LISTEN_ADDR=":50055" \
    "$BIN_DIR/execution" &
PIDS+=($!)
sleep 0.3

DELAY_US=50 DELAY_MODE=busyspin LISTEN_ADDR=":50054" DOWNSTREAM_ADDR="localhost:50055" REDIS_ADDR="localhost:6379" \
    "$BIN_DIR/marketdata" &
PIDS+=($!)
sleep 0.3

DELAY_US=50 DELAY_MODE=busyspin LISTEN_ADDR=":50053" DOWNSTREAM_ADDR="localhost:50054" \
    "$BIN_DIR/risk" &
PIDS+=($!)
sleep 0.3

DELAY_US=50 DELAY_MODE=busyspin LISTEN_ADDR=":50052" DOWNSTREAM_ADDR="localhost:50053" \
    "$BIN_DIR/auth" &
PIDS+=($!)
sleep 0.3

DELAY_US=50 DELAY_MODE=busyspin LISTEN_ADDR=":50051" DOWNSTREAM_ADDR="localhost:50052" \
    "$BIN_DIR/gateway" &
PIDS+=($!)
sleep 0.5

echo -e "${GREEN}✓ All 5 services started${NC}"

# ── Step 4: Send test request ─────────────────────
echo -e "\n${YELLOW}Sending test order...${NC}"

if command -v grpcurl &>/dev/null; then
    RESULT=$(grpcurl -plaintext \
        -import-path "$PROJECT_ROOT/proto" \
        -proto order.proto \
        -d '{"order_id":"SMOKE-001","symbol":"AAPL","quantity":100,"price":150.25}' \
        localhost:50051 order.GatewayService/SubmitOrder 2>&1)

    echo "$RESULT"

    if echo "$RESULT" | grep -q '"accepted"'; then
        LATENCY=$(echo "$RESULT" | grep -o '"latencyNs": "[^"]*"' | grep -o '[0-9]*')
        if [[ -n "$LATENCY" ]]; then
            LATENCY_US=$((LATENCY / 1000))
            echo -e "\n${GREEN}✓ SMOKE TEST PASSED — e2e latency: ${LATENCY_US} µs${NC}"
        else
            echo -e "\n${GREEN}✓ SMOKE TEST PASSED${NC}"
        fi
    else
        echo -e "\n${RED}✗ SMOKE TEST FAILED — unexpected response${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}grpcurl not installed. Install with:${NC}"
    echo "  go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest"
    echo ""
    echo "Or test manually:"
    echo "  grpcurl -plaintext -import-path proto -proto order.proto \\"
    echo "    -d '{\"order_id\":\"TEST\",\"symbol\":\"AAPL\",\"quantity\":100,\"price\":150.25}' \\"
    echo "    localhost:50051 order.GatewayService/SubmitOrder"
    echo ""
    echo -e "${YELLOW}Services are running. Press Ctrl+C to stop.${NC}"
    wait
fi
