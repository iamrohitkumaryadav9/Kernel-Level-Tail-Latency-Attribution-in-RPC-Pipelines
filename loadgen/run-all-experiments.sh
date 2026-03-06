#!/usr/bin/env bash
# loadgen/run-all-experiments.sh — Run full experiment matrix E1-E15
# Blueprint §3: Complete experiment matrix
# Usage: ./loadgen/run-all-experiments.sh [rate]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RATE="${1:-1000}"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Full experiment matrix from Blueprint §3
EXPERIMENTS=(
    "e0-no-instrumentation"
    "e1-baseline"
    "e2-cross-node"
    "e2-cpu-contention"
    "e3a-cfs-tight"
    "e3-memory-pressure"
    "e3b-cfs-moderate"
    "e4-noisy-neighbor"
    "e5-throttle-crossnode"
    "e6-contention-crossnode"
    "e7-full-stress"
    "e8-hostnetwork"
    "e8-resource-limits"
    "e9-hostnet-stress-crossnode"
    "e10-throttle-contention"
    "e11-network-policy"
    "e12-hpa"
    "e13-cpu-pinning"
    "e14-pinning-stress-crossnode"
    "e15-full-isolation"
)

echo -e "${YELLOW}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║  Full Experiment Matrix: ${#EXPERIMENTS[@]} experiments        ║${NC}"
echo -e "${YELLOW}║  Rate: $RATE req/s                                ║${NC}"
echo -e "${YELLOW}╚═══════════════════════════════════════════════════╝${NC}"
echo ""

TOTAL=${#EXPERIMENTS[@]}
COMPLETED=0
FAILED=()

for exp in "${EXPERIMENTS[@]}"; do
    COMPLETED=$((COMPLETED + 1))
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  [$COMPLETED/$TOTAL] Running: $exp${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    if "$SCRIPT_DIR/run-experiment.sh" "$exp" "$RATE"; then
        echo -e "${GREEN}  ✓ $exp completed${NC}"
    else
        echo -e "${RED}  ✗ $exp FAILED${NC}"
        FAILED+=("$exp")
    fi

    # Cool down between experiments
    if [ "$COMPLETED" -lt "$TOTAL" ]; then
        echo "  Cooling down 15s before next experiment..."
        sleep 15
    fi
done

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           All experiments complete!               ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo "Results:"
for exp in "${EXPERIMENTS[@]}"; do
    echo "  data/$exp/"
done

if [ ${#FAILED[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}Failed experiments:${NC}"
    for f in "${FAILED[@]}"; do
        echo -e "  ${RED}✗ $f${NC}"
    done
fi
