#!/usr/bin/env bash
# scripts/cfs-stats-collector.sh — Blueprint §4.2.3
# Polls per-pod CFS cpu.stat every 100ms and writes CSV
# Usage: ./scripts/cfs-stats-collector.sh [output_file] [duration_seconds]

set -uo pipefail

OUTPUT="${1:-cfs_stats.csv}"
DURATION="${2:-120}"
INTERVAL=0.1  # 100ms
NS="latency-lab"

echo "timestamp,pod,nr_periods,nr_throttled,throttled_usec" > "$OUTPUT"

# Get pod UIDs and names
get_pod_info() {
    kubectl get pods -n "$NS" -o jsonpath='{range .items[*]}{.metadata.name},{.metadata.uid}{"\n"}{end}' 2>/dev/null
}

echo "[cfs-stats] Collecting CFS throttling data for ${DURATION}s → $OUTPUT"
echo "[cfs-stats] Interval: ${INTERVAL}s"

END_TIME=$(($(date +%s) + DURATION))

while [ "$(date +%s)" -lt "$END_TIME" ]; do
    TS=$(date +%s.%N)

    # Read cpu.stat for each pod via kubectl exec into the node
    while IFS=',' read -r pod_name pod_uid; do
        [ -z "$pod_name" ] && continue

        # Try cgroup v2 path first, then v1
        # In Kind, we can read from the node's filesystem
        STATS=$(kubectl exec -n "$NS" "$pod_name" -- cat /sys/fs/cgroup/cpu.stat 2>/dev/null || \
                kubectl exec -n "$NS" "$pod_name" -- cat /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null || \
                echo "")

        if [ -n "$STATS" ]; then
            NR_PERIODS=$(echo "$STATS" | grep -oP 'nr_periods\s+\K\d+' || echo "0")
            NR_THROTTLED=$(echo "$STATS" | grep -oP 'nr_throttled\s+\K\d+' || echo "0")
            THROTTLED_USEC=$(echo "$STATS" | grep -oP 'throttled_usec\s+\K\d+' || \
                             echo "$STATS" | grep -oP 'throttled_time\s+\K\d+' || echo "0")

            echo "$TS,$pod_name,$NR_PERIODS,$NR_THROTTLED,$THROTTLED_USEC" >> "$OUTPUT"
        fi
    done <<< "$(get_pod_info)"

    sleep "$INTERVAL"
done

LINES=$(wc -l < "$OUTPUT")
echo "[cfs-stats] Done. Collected $((LINES - 1)) data points → $OUTPUT"
