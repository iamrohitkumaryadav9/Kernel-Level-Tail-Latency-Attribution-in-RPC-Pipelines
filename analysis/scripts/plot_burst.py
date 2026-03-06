#!/usr/bin/env python3
"""
Fig 8: Burst response time-series — Blueprint §06.6
Reads burst CSV output from loadgen/burst and plots latency vs time with burst windows highlighted.
"""
import csv, sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: pip3 install matplotlib"); sys.exit(1)

CSV_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "burst-results.csv"
PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"
PLOT_DIR.mkdir(exist_ok=True)

def main():
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found. Run burst load generator first.")
        sys.exit(1)

    rows = list(csv.DictReader(open(CSV_PATH)))
    print(f"Loaded {len(rows)} requests from burst CSV")

    ts0 = int(rows[0]["timestamp_ns"])
    times = [(int(r["timestamp_ns"]) - ts0) / 1e9 for r in rows]
    latencies = [int(r["latency_us"]) / 1000 for r in rows]  # ms
    is_burst = [r["burst"] == "true" for r in rows]

    # 500ms rolling p99
    window = 0.5
    roll_t, roll_p99, roll_p50 = [], [], []
    for i, t in enumerate(times):
        window_lats = [latencies[j] for j, tj in enumerate(times) if abs(tj - t) <= window/2]
        if window_lats:
            roll_t.append(t)
            roll_p99.append(np.percentile(window_lats, 99))
            roll_p50.append(np.percentile(window_lats, 50))

    fig, ax = plt.subplots(figsize=(14, 6))

    # Shade burst windows
    in_burst = False
    burst_start = None
    for t, b in zip(times, is_burst):
        if b and not in_burst:
            burst_start = t
            in_burst = True
        elif not b and in_burst:
            ax.axvspan(burst_start, t, alpha=0.15, color="red", label="Burst window" if burst_start == [x for x,y in zip(times,is_burst) if y][0] else "")
            in_burst = False

    # Scatter individual requests
    burst_t = [t for t, b in zip(times, is_burst) if b]
    burst_l = [l for l, b in zip(latencies, is_burst) if b]
    base_t  = [t for t, b in zip(times, is_burst) if not b]
    base_l  = [l for l, b in zip(latencies, is_burst) if not b]

    ax.scatter(base_t, base_l, s=1, alpha=0.3, color="#3498db", label="Base req")
    ax.scatter(burst_t, burst_l, s=2, alpha=0.5, color="#e74c3c", label="Burst req")

    # Rolling percentiles
    ax.plot(roll_t, roll_p99, color="darkred", linewidth=2, label="p99 (500ms window)", zorder=5)
    ax.plot(roll_t, roll_p50, color="steelblue", linewidth=1.5, linestyle="--", label="p50 (500ms window)", zorder=5)

    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Fig 8: Burst Response — 1000 rps base + 5000 rps bursts (50ms every 2s)\nBlueprint §3.4 / §06.6", fontsize=13, fontweight="bold")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, max(latencies) * 1.1)

    fig.tight_layout()
    out = PLOT_DIR / "fig8_burst_response.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)

    # Stats
    burst_lats = [l for l, b in zip(latencies, is_burst) if b]
    base_lats  = [l for l, b in zip(latencies, is_burst) if not b]
    print(f"  Base:  p50={np.percentile(base_lats,50):.1f}ms  p99={np.percentile(base_lats,99):.1f}ms  n={len(base_lats)}")
    print(f"  Burst: p50={np.percentile(burst_lats,50):.1f}ms  p99={np.percentile(burst_lats,99):.1f}ms  n={len(burst_lats)}")
    print(f"  Amplification: p99 burst/base = {np.percentile(burst_lats,99)/np.percentile(base_lats,99):.2f}×")
    print(f"\n  → {out}")

if __name__ == "__main__":
    main()
