#!/usr/bin/env bash
# loadgen/run-experiment.sh — Blueprint §3.4 compliant
# Rate: 2000 req/s, Warmup: 30s, Duration: 120s, Reps: 3, Cooldown: 60s

set -uo pipefail

EXPERIMENT="${1:?Usage: $0 <experiment-name> [rate]}"
RATE="${2:-2000}"
DURATION="120s"
WARMUP_DURATION="30s"
REPETITIONS=3
COOLDOWN=60
NS="latency-lab"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY_DIR="$PROJECT_ROOT/deploy/overlays/$EXPERIMENT"
OUTPUT_DIR="$PROJECT_ROOT/data/$EXPERIMENT"
PROTO_PATH="$PROJECT_ROOT/proto"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

if [ ! -d "$OVERLAY_DIR" ]; then
    echo -e "${RED}✗ Overlay not found: $OVERLAY_DIR${NC}"
    exit 1
fi

# ── Helper: start port-forward ─────────────────────
start_port_forward() {
    pkill -f "kubectl.*port-forward.*50051" 2>/dev/null || true
    sleep 2
    kubectl -n "$NS" port-forward svc/gateway-svc 50051:50051 &
    PF_PID=$!
    sleep 3
    # Verify it's alive
    if ! kill -0 $PF_PID 2>/dev/null; then
        echo -e "${RED}✗ Port-forward failed to start${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ Port-forward active (PID: $PF_PID)${NC}"
}

# ── Cleanup function (always runs) ─────────────────
cleanup() {
    echo -e "\n${YELLOW}[cleanup] Restoring base deployment...${NC}"
    pkill -f "kubectl.*port-forward.*50051" 2>/dev/null || true

    kubectl -n "$NS" delete deployment cpu-hog cpu-hog-a cpu-hog-b 2>/dev/null || true
    kubectl -n "$NS" delete networkpolicies --all 2>/dev/null || true
    kubectl -n "$NS" delete hpa --all 2>/dev/null || true

    kubectl apply -k "$PROJECT_ROOT/deploy/base/" 2>&1 | grep -v "^$"

    sleep 5
    for deploy in gateway auth risk marketdata execution; do
        kubectl -n "$NS" rollout status deployment/"$deploy" --timeout=60s 2>/dev/null || true
    done

    echo ""
    kubectl -n "$NS" get pods
    echo -e "\n${GREEN}═══ $EXPERIMENT complete ═══${NC}"
    echo "Results: $OUTPUT_DIR/"
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"

PAYLOAD='{"order_id":"EXP-{{.RequestNumber}}","symbol":"AAPL","quantity":100,"price":150.25}'

echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  Experiment: $EXPERIMENT${NC}"
echo -e "${YELLOW}  Rate: $RATE req/s × $DURATION × $REPETITIONS reps${NC}"
echo -e "${YELLOW}  Warmup: $WARMUP_DURATION | Cooldown: ${COOLDOWN}s${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
echo ""

# ── Step 1: Apply overlay ──────────────────────────
echo -e "${YELLOW}[1/4] Applying overlay: $EXPERIMENT${NC}"
kubectl apply -k "$OVERLAY_DIR" 2>&1 | grep -v "^$"
echo -e "${GREEN}✓ Overlay applied${NC}\n"

# ── Step 2: Wait for pods ──────────────────────────
echo -e "${YELLOW}[2/4] Waiting for pods to be ready...${NC}"
sleep 5

for deploy in gateway auth risk marketdata execution redis; do
    echo -n "  Waiting for $deploy..."
    kubectl -n "$NS" rollout status deployment/"$deploy" --timeout=120s 2>/dev/null || true
    echo -e " ${GREEN}✓${NC}"
done

echo ""
kubectl -n "$NS" get pods -o wide
echo ""

# ── Step 3: Warmup ─────────────────────────────────
echo -e "${YELLOW}[3/4] Warmup phase ($WARMUP_DURATION)...${NC}"
start_port_forward

ghz --insecure \
    --proto "$PROTO_PATH/order.proto" \
    --import-paths "$PROTO_PATH" \
    --call order.GatewayService.SubmitOrder \
    -d "$PAYLOAD" \
    --rps "$RATE" \
    --duration "$WARMUP_DURATION" \
    --concurrency 50 \
    --connections 10 \
    localhost:50051 > /dev/null 2>&1 || true
echo -e "${GREEN}✓ Warmup complete${NC}\n"

# ── Step 4: Measurement runs ──────────────────────
echo -e "${YELLOW}[4/4] Running $REPETITIONS measurement runs at $RATE req/s × $DURATION...${NC}"

for rep in $(seq 1 $REPETITIONS); do
    echo -e "\n${CYAN}  ── Run $rep/$REPETITIONS ──${NC}"

    # Restart port-forward before EVERY run to prevent stale connections
    start_port_forward
    sleep 2

    OUTPUT_FILE="$OUTPUT_DIR/rate-${RATE}-run${rep}.json"

    ghz --insecure \
        --proto "$PROTO_PATH/order.proto" \
        --import-paths "$PROTO_PATH" \
        --call order.GatewayService.SubmitOrder \
        -d "$PAYLOAD" \
        --rps "$RATE" \
        --duration "$DURATION" \
        --concurrency 50 \
        --connections 10 \
        --format json \
        localhost:50051 > "$OUTPUT_FILE" 2>&1

    # Parse results
    python3 << PYEOF || true
import json
with open("$OUTPUT_FILE") as f:
    d = json.load(f)
count = d.get('count', 0)
avg = d.get('average', 0) / 1e6
rps_actual = d.get('rps', 0)
p50 = p99 = 0
for p in (d.get('latencyDistribution') or []):
    if p.get('percentage') == 50: p50 = p.get('latency', 0) / 1e6
    if p.get('percentage') == 99: p99 = p.get('latency', 0) / 1e6
scd = d.get('statusCodeDistribution') or {}
ok = scd.get('OK', 0)
errs = count - ok
print(f'  ┌─────────────────────────────────')
print(f'  │ Run:       $rep/$REPETITIONS')
print(f'  │ Requests:  {count}')
print(f'  │ RPS:       {rps_actual:.0f}')
print(f'  │ Avg:       {avg:.2f} ms')
print(f'  │ p50:       {p50:.2f} ms')
print(f'  │ p99:       {p99:.2f} ms')
print(f'  │ OK:        {ok}')
print(f'  │ Errors:    {errs}')
print(f'  └─────────────────────────────────')
PYEOF

    echo -e "${GREEN}  → saved: $OUTPUT_FILE${NC}"

    # Cooldown between runs (skip after last)
    if [ "$rep" -lt "$REPETITIONS" ]; then
        echo -e "  ${YELLOW}Cooldown: ${COOLDOWN}s...${NC}"
        sleep "$COOLDOWN"
    fi
done

echo ""
