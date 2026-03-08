#!/usr/bin/env python3
"""
Fig 8: Burst response with recovery time analysis — Blueprint §06.6, §04.1
Enhanced: burst amplification ratio, recovery time detection, per-burst metrics.
Usage: python3 analysis/scripts/plot_burst.py
"""
import csv, sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: pip3 install matplotlib numpy"); sys.exit(1)

CSV_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "burst-results.csv"
PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"
STATS_DIR = Path(__file__).resolve().parent.parent / "stats"
PLOT_DIR.mkdir(exist_ok=True)
STATS_DIR.mkdir(exist_ok=True)


def load_burst_data():
    """Load burst CSV with per-request timestamps and latencies."""
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found. Run burst load generator first.")
        sys.exit(1)

    rows = list(csv.DictReader(open(CSV_PATH)))
    print(f"Loaded {len(rows)} requests from burst CSV")

    ts0 = int(rows[0]["timestamp_ns"])
    times = np.array([(int(r["timestamp_ns"]) - ts0) / 1e9 for r in rows])
    latencies = np.array([int(r["latency_us"]) / 1000 for r in rows])  # ms
    is_burst = np.array([r["burst"] == "true" for r in rows])
    is_ok = np.array([r["ok"] == "true" for r in rows])

    return times, latencies, is_burst, is_ok


def compute_sliding_window(times, latencies, window_s=1.0, step_s=0.1):
    """
    Compute sliding-window percentiles — Blueprint §06.3.
    Window = 1 second, Step = 100ms.
    Returns: window_centers, p99s, p50s, p999s, counts
    """
    t_start = times[0]
    t_end = times[-1]
    centers, p99s, p50s, p999s, counts = [], [], [], [], []

    t = t_start + window_s / 2
    while t < t_end - window_s / 2:
        mask = (times >= t - window_s/2) & (times < t + window_s/2)
        window_lats = latencies[mask]
        if len(window_lats) >= 5:
            centers.append(t)
            p99s.append(np.percentile(window_lats, 99))
            p50s.append(np.percentile(window_lats, 50))
            p999s.append(np.percentile(window_lats, 99.9) if len(window_lats) >= 100 else np.percentile(window_lats, 99))
            counts.append(len(window_lats))
        t += step_s

    return np.array(centers), np.array(p99s), np.array(p50s), np.array(p999s), np.array(counts)


def detect_burst_windows(times, is_burst):
    """Detect contiguous burst windows (start_time, end_time)."""
    bursts = []
    in_burst = False
    start = 0

    for i, (t, b) in enumerate(zip(times, is_burst)):
        if b and not in_burst:
            start = t
            in_burst = True
        elif not b and in_burst:
            bursts.append((start, t))
            in_burst = False

    if in_burst:
        bursts.append((start, times[-1]))

    return bursts


def compute_recovery_time(window_centers, window_p99, burst_windows, baseline_p99, threshold=1.2):
    """
    Compute recovery time for each burst — Blueprint §04.1.
    Recovery = time from burst end until sliding p99 returns to <= threshold × baseline_p99.
    """
    recovery_threshold = baseline_p99 * threshold
    recoveries = []

    for burst_start, burst_end in burst_windows:
        # Find the peak p99 during burst
        burst_mask = (window_centers >= burst_start) & (window_centers <= burst_end)
        burst_p99s = window_p99[burst_mask]
        peak_p99 = np.max(burst_p99s) if len(burst_p99s) > 0 else baseline_p99

        # Find recovery: first window after burst end where p99 <= threshold
        post_burst_mask = window_centers > burst_end
        post_centers = window_centers[post_burst_mask]
        post_p99s = window_p99[post_burst_mask]

        recovery_time_ms = None
        recovery_center = None
        for ct, p99 in zip(post_centers, post_p99s):
            if p99 <= recovery_threshold:
                recovery_time_ms = (ct - burst_end) * 1000  # seconds → ms
                recovery_center = ct
                break

        # Amplification ratio for this burst
        pre_burst_mask = (window_centers >= burst_start - 2.0) & (window_centers < burst_start)
        pre_p99s = window_p99[pre_burst_mask]
        pre_p99 = np.mean(pre_p99s) if len(pre_p99s) > 0 else baseline_p99
        amplification = peak_p99 / pre_p99 if pre_p99 > 0 else 0

        recoveries.append({
            "burst_start_s": round(burst_start, 3),
            "burst_end_s": round(burst_end, 3),
            "burst_duration_ms": round((burst_end - burst_start) * 1000, 1),
            "pre_burst_p99_ms": round(pre_p99, 2),
            "peak_p99_ms": round(peak_p99, 2),
            "amplification_ratio": round(amplification, 2),
            "recovery_time_ms": round(recovery_time_ms, 1) if recovery_time_ms is not None else None,
            "recovery_center_s": recovery_center,
        })

    return recoveries


def write_burst_csv(recoveries, global_stats):
    """Write burst sensitivity analysis to CSV."""
    csv_path = STATS_DIR / "burst_sensitivity.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "burst_id", "burst_start_s", "burst_end_s", "burst_duration_ms",
            "pre_burst_p99_ms", "peak_p99_ms", "amplification_ratio", "recovery_time_ms"
        ])
        w.writeheader()
        for i, rec in enumerate(recoveries, 1):
            w.writerow({
                "burst_id": i,
                "burst_start_s": rec["burst_start_s"],
                "burst_end_s": rec["burst_end_s"],
                "burst_duration_ms": rec["burst_duration_ms"],
                "pre_burst_p99_ms": rec["pre_burst_p99_ms"],
                "peak_p99_ms": rec["peak_p99_ms"],
                "amplification_ratio": rec["amplification_ratio"],
                "recovery_time_ms": rec["recovery_time_ms"] if rec["recovery_time_ms"] else "N/A",
            })

    # Append summary row
    with open(csv_path, "a") as f:
        f.write(f"\n# Summary Statistics\n")
        for k, v in global_stats.items():
            f.write(f"# {k}: {v}\n")

    print(f"  ✓ {csv_path} ({len(recoveries)} bursts)")


def plot_burst_with_recovery(times, latencies, is_burst, window_centers, window_p99,
                              window_p50, burst_windows, recoveries, baseline_p99):
    """Enhanced Fig 8 with recovery time annotations."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), height_ratios=[3, 1],
                                     sharex=True, gridspec_kw={"hspace": 0.05})

    # ── Top panel: latency time-series ──
    # Shade burst windows
    first_burst = True
    for bstart, bend in burst_windows:
        lbl = "Burst window" if first_burst else ""
        ax1.axvspan(bstart, bend, alpha=0.12, color="red", label=lbl)
        first_burst = False

    # Shade recovery windows
    first_recovery = True
    for rec in recoveries:
        if rec["recovery_time_ms"] is not None and rec["recovery_center_s"] is not None:
            lbl = "Recovery window" if first_recovery else ""
            ax1.axvspan(rec["burst_end_s"], rec["recovery_center_s"],
                       alpha=0.10, color="orange", label=lbl)
            # Add recovery time annotation
            mid = (rec["burst_end_s"] + rec["recovery_center_s"]) / 2
            ax1.annotate(f'{rec["recovery_time_ms"]:.0f}ms',
                        xy=(mid, baseline_p99 * 1.2), fontsize=7,
                        ha="center", va="bottom", color="darkorange", fontweight="bold")
            first_recovery = False

    # Scatter individual requests (downsample if too many)
    max_points = 20000
    if len(times) > max_points:
        idx = np.random.choice(len(times), max_points, replace=False)
        idx = np.sort(idx)
    else:
        idx = np.arange(len(times))

    burst_idx = idx[is_burst[idx]]
    base_idx = idx[~is_burst[idx]]

    ax1.scatter(times[base_idx], latencies[base_idx], s=0.5, alpha=0.2,
               color="#3498db", label="Base req", rasterized=True)
    ax1.scatter(times[burst_idx], latencies[burst_idx], s=1, alpha=0.4,
               color="#e74c3c", label="Burst req", rasterized=True)

    # Rolling percentiles
    ax1.plot(window_centers, window_p99, color="darkred", linewidth=2,
            label="p99 (1s window)", zorder=5)
    ax1.plot(window_centers, window_p50, color="steelblue", linewidth=1.5,
            linestyle="--", label="p50 (1s window)", zorder=5)

    # Recovery threshold line
    ax1.axhline(baseline_p99 * 1.2, color="orange", linestyle=":", alpha=0.6,
               label=f"Recovery threshold (1.2× baseline = {baseline_p99*1.2:.1f}ms)")

    ax1.set_ylabel("Latency (ms)", fontsize=12)
    ax1.set_title("Fig 8: Burst Response with Recovery Time Analysis\n"
                  "Blueprint §04.1, §06.6 — 1000 rps base + 5000 rps bursts (50ms every 2s)",
                  fontsize=13, fontweight="bold")
    ax1.legend(fontsize=8, loc="upper right", ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, np.percentile(latencies, 99.5) * 1.3)

    # ── Bottom panel: request rate ──
    rate_window = 0.5  # 500ms
    rate_times = np.arange(times[0], times[-1], 0.1)
    rates = []
    for t in rate_times:
        mask = (times >= t) & (times < t + rate_window)
        rates.append(np.sum(mask) / rate_window)
    rates = np.array(rates)

    ax2.fill_between(rate_times, rates, alpha=0.5, color="#3498db")
    ax2.set_ylabel("Request Rate\n(req/s)", fontsize=10)
    ax2.set_xlabel("Time (s)", fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, max(rates) * 1.2 if len(rates) > 0 else 100)

    fig.tight_layout()
    out = PLOT_DIR / "fig8_burst_response.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  → {out}")


def main():
    print("=" * 60)
    print("BURST SENSITIVITY ANALYSIS — Blueprint §04.1, §06.6")
    print("=" * 60)

    times, latencies, is_burst, is_ok = load_burst_data()

    # Compute sliding window (1s window, 100ms step — per §06.3)
    print("\nComputing sliding-window metrics (1s window, 100ms step)...")
    win_c, win_p99, win_p50, win_p999, win_counts = compute_sliding_window(times, latencies)
    print(f"  {len(win_c)} windows computed")

    # Detect burst windows
    burst_windows = detect_burst_windows(times, is_burst)
    print(f"  {len(burst_windows)} burst windows detected")

    # Compute baseline p99 from non-burst requests
    base_lats = latencies[~is_burst]
    burst_lats = latencies[is_burst]
    baseline_p99 = np.percentile(base_lats, 99)
    baseline_p50 = np.percentile(base_lats, 50)
    print(f"  Baseline (non-burst): p50={baseline_p50:.1f}ms  p99={baseline_p99:.1f}ms  n={len(base_lats)}")
    print(f"  Burst:                p50={np.percentile(burst_lats,50):.1f}ms  p99={np.percentile(burst_lats,99):.1f}ms  n={len(burst_lats)}")

    # Recovery time analysis
    print("\nRecovery time analysis:")
    recoveries = compute_recovery_time(win_c, win_p99, burst_windows, baseline_p99)

    valid_recoveries = [r for r in recoveries if r["recovery_time_ms"] is not None]
    recovery_times = [r["recovery_time_ms"] for r in valid_recoveries]
    amplifications = [r["amplification_ratio"] for r in recoveries if r["amplification_ratio"] > 0]

    for i, rec in enumerate(recoveries[:10], 1):  # Print first 10
        rt = f'{rec["recovery_time_ms"]:.0f}ms' if rec["recovery_time_ms"] else "N/A"
        print(f"  Burst {i}: amp={rec['amplification_ratio']:.2f}×  "
              f"peak_p99={rec['peak_p99_ms']:.1f}ms  recovery={rt}")

    if len(recoveries) > 10:
        print(f"  ... ({len(recoveries) - 10} more bursts)")

    # Global summary
    global_stats = {
        "total_bursts": len(burst_windows),
        "total_requests": len(times),
        "burst_requests": int(np.sum(is_burst)),
        "base_requests": int(np.sum(~is_burst)),
        "baseline_p50_ms": round(baseline_p50, 2),
        "baseline_p99_ms": round(baseline_p99, 2),
        "burst_p99_ms": round(np.percentile(burst_lats, 99), 2),
        "global_amplification": round(np.percentile(burst_lats, 99) / baseline_p99, 2),
        "mean_recovery_time_ms": round(np.mean(recovery_times), 1) if recovery_times else "N/A",
        "median_recovery_time_ms": round(np.median(recovery_times), 1) if recovery_times else "N/A",
        "max_recovery_time_ms": round(np.max(recovery_times), 1) if recovery_times else "N/A",
        "mean_amplification": round(np.mean(amplifications), 2) if amplifications else "N/A",
        "bursts_with_recovery": len(valid_recoveries),
    }

    print(f"\n  Global amplification: {global_stats['global_amplification']}×")
    print(f"  Mean recovery time:  {global_stats['mean_recovery_time_ms']}ms")
    print(f"  Median recovery:     {global_stats['median_recovery_time_ms']}ms")

    # Write CSV
    write_burst_csv(recoveries, global_stats)

    # Generate enhanced plot
    print("\nGenerating enhanced burst response plot:")
    plot_burst_with_recovery(times, latencies, is_burst, win_c, win_p99, win_p50,
                             burst_windows, recoveries, baseline_p99)

    print(f"\n✓ Burst sensitivity analysis complete")


if __name__ == "__main__":
    main()
