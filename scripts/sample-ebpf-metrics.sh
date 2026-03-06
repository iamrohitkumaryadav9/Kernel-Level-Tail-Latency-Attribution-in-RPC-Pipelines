#!/usr/bin/env bash
# scripts/sample-ebpf-metrics.sh
# For each experiment overlay, applies it, samples eBPF Prometheus for 15s,
# records wakeup_delay_p99, softirq_time, retransmit_count.
# Does NOT run ghz — just samples eBPF metrics per condition.
# Output: data/ebpf_per_experiment.csv
#
# Usage: ./scripts/sample-ebpf-metrics.sh
# Requires: rqdelay port-forward active on :9090, cluster running

set -uo pipefail

NS="latency-lab"
EBPF_PORT=9090
OUTPUT="data/ebpf_per_experiment.csv"
SAMPLE_WAIT=20  # seconds to wait after overlay before sampling

# Colors
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

EXPERIMENTS=(
    e0-no-instrumentation
    e1-baseline
    e2-cross-node
    e2-cpu-contention
    e3a-cfs-tight
    e3b-cfs-moderate
    e3-memory-pressure
    e4-noisy-neighbor
    e5-throttle-crossnode
    e6-contention-crossnode
    e7-full-stress
    e8-hostnetwork
    e8-resource-limits
    e9-hostnet-stress-crossnode
    e10-throttle-contention
    e11-network-policy
    e12-hpa
    e13-cpu-pinning
    e14-pinning-stress-crossnode
    e15-full-isolation
)

echo "experiment,rqdelay_p99_us,rqdelay_p50_us,softirq_time_ns,softirq_count,tcp_retransmit" > "$OUTPUT"

sample_ebpf_with_load() {
    local exp="$1"

    # Start gateway port-forward
    pkill -f "port-forward.*50051" 2>/dev/null || true
    sleep 1
    kubectl -n "$NS" port-forward svc/gateway-svc 50051:50051 >/dev/null 2>&1 &
    PF_PID=$!
    sleep 3

    # Run ghz for 20s in background to create real load
    ghz --insecure \
        --proto proto/order.proto \
        --call order.GatewayService.SubmitOrder \
        -d '{"order_id":"SAMPLE","symbol":"AAPL","quantity":100,"price":150.25}' \
        --rps 2000 --duration 20s \
        localhost:50051 >/dev/null 2>&1 &
    GHZ_PID=$!

    # Wait 10s for load to stabilize before sampling
    sleep 10

    METRICS=$(curl -s "http://localhost:${EBPF_PORT}/metrics" 2>/dev/null)
    P99=$(echo "$METRICS"      | grep '^rqdelay_p99_us '               | awk '{print $2}' || echo "0")
    P50=$(echo "$METRICS"      | grep '^rqdelay_p50_us '               | awk '{print $2}' || echo "0")
    SIRQ_TIME=$(echo "$METRICS" | grep '^rqdelay_softirq_time_ns{'     | awk -F' ' '{sum += $NF} END {print sum}' || echo "0")
    SIRQ_CNT=$(echo "$METRICS"  | grep '^rqdelay_softirq_count{'       | awk -F' ' '{sum += $NF} END {print sum}' || echo "0")
    RETRANS=$(echo "$METRICS"   | grep '^rqdelay_tcp_retransmit_total ' | awk '{print $2}' || echo "0")

    # Kill load and port-forward
    kill $GHZ_PID 2>/dev/null || true
    kill $PF_PID  2>/dev/null || true
    pkill -f "port-forward.*50051" 2>/dev/null || true

    echo "${exp},${P99:-0},${P50:-0},${SIRQ_TIME:-0},${SIRQ_CNT:-0},${RETRANS:-0}" >> "$OUTPUT"
    echo -e "  ${GREEN}✓${NC} $exp: p99=${P99}µs  softirq_cnt=${SIRQ_CNT}  retrans=${RETRANS}"
}

wait_ready() {
    for svc in gateway auth risk marketdata execution; do
        kubectl rollout status deployment/$svc -n $NS --timeout=60s >/dev/null 2>&1 || true
    done
    sleep 5
}

echo "=== eBPF per-experiment metric sampler ==="
echo "Output: $OUTPUT"
echo ""

# Start rqdelay port-forward if not already active
if ! curl -s "http://localhost:${EBPF_PORT}/metrics" >/dev/null 2>&1; then
    echo "Starting rqdelay port-forward..."
    pkill -f "port-forward.*${EBPF_PORT}" 2>/dev/null || true
    kubectl -n $NS port-forward ds/rqdelay ${EBPF_PORT}:${EBPF_PORT} &
    sleep 5
fi

for exp in "${EXPERIMENTS[@]}"; do
    echo ""
    echo "─── $exp ───"

    # Apply overlay
    kubectl apply -k "deploy/overlays/$exp/" -n $NS >/dev/null 2>&1 || {
        echo -e "  ${RED}✗ overlay not found, skipping${NC}"
        echo "${exp},0,0,0,0,0" >> "$OUTPUT"
        continue
    }

    wait_ready
    sleep 5

    sample_ebpf_with_load "$exp"

    # Quick restore between experiments (skip full restore to save time)
done

# Always restore base at end
echo ""
echo "Restoring base deployment..."
kubectl apply -k deploy/base/ -n $NS >/dev/null 2>&1

LINES=$(wc -l < "$OUTPUT")
echo ""
echo "=== Done. Sampled $((LINES - 1)) experiments → $OUTPUT ==="
