#!/usr/bin/env bash
# loadgen/run-baseline.sh — Run E1 baseline load test using ghz
# Sends gRPC traffic at varying rates and collects latency distributions.
#
# Usage: ./loadgen/run-baseline.sh [TARGET] [OUTPUT_DIR]
#   TARGET:     host:port (default: localhost:50051)
#   OUTPUT_DIR: where to write results (default: data/e1-baseline)

set -euo pipefail

TARGET="${1:-localhost:50051}"
OUTPUT_DIR="${2:-data/e1-baseline}"
PROTO_PATH="proto"
PROTO_FILE="order.proto"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "$OUTPUT_DIR"

# Test payload
PAYLOAD='{"order_id":"LOAD-{{.RequestNumber}}","symbol":"AAPL","quantity":100,"price":150.25}'

echo -e "${YELLOW}═══ E1 Baseline Load Test ═══${NC}"
echo "Target: $TARGET"
echo "Output: $OUTPUT_DIR"
echo ""

# Warmup (discard results)
echo -e "${YELLOW}Warming up (500 requests)...${NC}"
ghz --insecure \
    --proto "$PROJECT_ROOT/$PROTO_PATH/$PROTO_FILE" \
    --import-paths "$PROJECT_ROOT/$PROTO_PATH" \
    --call order.GatewayService.SubmitOrder \
    -d "$PAYLOAD" \
    -n 500 -c 10 \
    "$TARGET" > /dev/null 2>&1 || true
echo -e "${GREEN}✓ Warmup done${NC}\n"

# Run at different rates
RATES=(100 500 1000 2000)
DURATION="30s"

for rate in "${RATES[@]}"; do
    echo -e "${YELLOW}▶ Rate: ${rate} req/s for ${DURATION}${NC}"

    OUTPUT_FILE="$OUTPUT_DIR/rate-${rate}.json"
    SUMMARY_FILE="$OUTPUT_DIR/rate-${rate}-summary.txt"

    ghz --insecure \
        --proto "$PROJECT_ROOT/$PROTO_PATH/$PROTO_FILE" \
        --import-paths "$PROJECT_ROOT/$PROTO_PATH" \
        --call order.GatewayService.SubmitOrder \
        -d "$PAYLOAD" \
        --rps "$rate" \
        --duration "$DURATION" \
        --concurrency 50 \
        --connections 10 \
        --format json \
        "$TARGET" > "$OUTPUT_FILE" 2>&1

    # Extract key metrics from JSON
    if command -v python3 &>/dev/null; then
        python3 -c "
import json, sys
with open('$OUTPUT_FILE') as f:
    d = json.load(f)
count = d.get('count', 0)
avg = d.get('average', 0) / 1e6  # ns → ms
p50 = d.get('latencyDistribution', [{}])
p50_val = 0
p99_val = 0
for p in d.get('latencyDistribution', []):
    if p.get('percentage') == 50:
        p50_val = p.get('latency', 0) / 1e6
    if p.get('percentage') == 99:
        p99_val = p.get('latency', 0) / 1e6
errCount = d.get('statusCodeDistribution', {}).get('Unknown', 0) + d.get('statusCodeDistribution', {}).get('Unavailable', 0)
okCount = d.get('statusCodeDistribution', {}).get('OK', 0)
rps_actual = d.get('rps', 0)
print(f'  Requests:  {count}')
print(f'  RPS:       {rps_actual:.0f}')
print(f'  Avg:       {avg:.2f} ms')
print(f'  p50:       {p50_val:.2f} ms')
print(f'  p99:       {p99_val:.2f} ms')
print(f'  OK:        {okCount}')
print(f'  Errors:    {errCount}')
" 2>/dev/null || echo "  (install python3 for parsed output)"
    fi

    echo -e "${GREEN}  → saved: $OUTPUT_FILE${NC}\n"
done

echo -e "${GREEN}═══ Baseline complete ═══${NC}"
echo "Results in: $OUTPUT_DIR/"
echo "Files:"
ls -la "$OUTPUT_DIR/"
