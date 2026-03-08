#!/usr/bin/env python3
"""
100ms Windowed Correlation Analysis — Blueprint §06.2, §06.3
Buckets per-request latencies into 100ms windows, detects tail spikes,
and computes correlation between windowed app metrics and load intensity.
Usage: python3 analysis/scripts/windowed_correlation.py
"""
import json, csv, sys, math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: pip3 install matplotlib numpy"); sys.exit(1)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"
STATS_DIR = Path(__file__).resolve().parent.parent / "stats"
PLOT_DIR.mkdir(exist_ok=True)
STATS_DIR.mkdir(exist_ok=True)

# Key experiments to analyze
TARGET_EXPERIMENTS = [
    "e1-baseline", "e3a-cfs-tight", "e3b-cfs-moderate",
    "e4-noisy-neighbor", "e6-contention-crossnode",
    "e7-full-stress", "e10-throttle-contention",
    "e13-cpu-pinning", "e15-full-isolation",
]

WINDOW_MS = 100   # 100ms windows — Blueprint §06.2
STEP_MS = 100     # Non-overlapping windows


def load_per_request_data(exp_name, run_idx=0):
    """Load per-request details from ghz JSON."""
    exp_dir = DATA_DIR / exp_name
    if not exp_dir.exists():
        return None

    jsons = sorted(exp_dir.glob("*.json"))
    if run_idx >= len(jsons):
        return None

    with open(jsons[run_idx]) as f:
        d = json.load(f)

    details = d.get("details", [])
    if not details:
        return None

    timestamps_s = []
    latencies_ms = []
    statuses = []
    for det in details:
        lat_ns = det.get("latency", 0)
        status = det.get("status", "")
        ts_str = det.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")[:32])
            timestamps_s.append(dt.timestamp())
        except:
            timestamps_s.append(0)
        latencies_ms.append(lat_ns / 1e6)
        statuses.append(status)

    return {
        "timestamps": np.array(timestamps_s),
        "latencies_ms": np.array(latencies_ms),
        "statuses": statuses,
        "count": d.get("count", 0),
        "rps": d.get("rps", 0),
    }


def load_ebpf_data():
    """Load experiment-level eBPF metrics."""
    ebpf_path = DATA_DIR / "ebpf_per_experiment.csv"
    if not ebpf_path.exists():
        return {}
    ebpf = {}
    with open(ebpf_path) as f:
        for row in csv.DictReader(f):
            ebpf[row["experiment"]] = {
                "rqdelay_p99_us": float(row["rqdelay_p99_us"]),
                "rqdelay_p50_us": float(row["rqdelay_p50_us"]),
                "softirq_time_ns": float(row["softirq_time_ns"]),
                "softirq_count": float(row["softirq_count"]),
                "tcp_retransmit": float(row["tcp_retransmit"]),
            }
    return ebpf


def bucket_into_windows(data, window_ms=100):
    """
    Bucket per-request data into 100ms windows — Blueprint §06.2.
    Returns list of window dicts with computed metrics.
    """
    ts = data["timestamps"]
    lats = data["latencies_ms"]

    if len(ts) == 0 or ts[0] == 0:
        return []

    # Filter valid timestamps
    valid = ts > 0
    ts = ts[valid]
    lats = lats[valid]

    if len(ts) == 0:
        return []

    t_start = ts.min()
    t_end = ts.max()
    window_s = window_ms / 1000.0
    n_windows = int((t_end - t_start) / window_s) + 1

    windows = []
    for i in range(n_windows):
        w_start = t_start + i * window_s
        w_end = w_start + window_s
        mask = (ts >= w_start) & (ts < w_end)
        w_lats = lats[mask]

        if len(w_lats) == 0:
            continue

        window = {
            "window_id": i,
            "window_start_s": round(w_start - t_start, 3),  # relative to experiment start
            "request_count": len(w_lats),
            "throughput": len(w_lats) / window_s,
            "app_p50": float(np.percentile(w_lats, 50)),
            "app_p99": float(np.percentile(w_lats, 99)) if len(w_lats) >= 3 else float(np.max(w_lats)),
            "app_mean": float(np.mean(w_lats)),
            "app_max": float(np.max(w_lats)),
            "app_std": float(np.std(w_lats)) if len(w_lats) > 1 else 0,
        }
        windows.append(window)

    return windows


def detect_spikes(windows, baseline_p99):
    """
    Spike detection — Blueprint §06.3.
    Spike window: p99 > 2× baseline p99
    Calm window:  p99 < 1.2× baseline p99
    """
    spike_threshold = baseline_p99 * 2.0
    calm_threshold = baseline_p99 * 1.2

    for w in windows:
        if w["app_p99"] > spike_threshold:
            w["spike_class"] = "spike"
        elif w["app_p99"] < calm_threshold:
            w["spike_class"] = "calm"
        else:
            w["spike_class"] = "intermediate"

    return windows


def compute_spearman_rho(x, y):
    """Compute Spearman rank correlation coefficient."""
    if len(x) < 3 or len(y) < 3 or len(x) != len(y):
        return 0.0, 1.0

    # Rank the values
    def rank(arr):
        sorted_idx = np.argsort(arr)
        ranks = np.empty_like(sorted_idx, dtype=float)
        ranks[sorted_idx] = np.arange(1, len(arr) + 1)
        # Handle ties: average rank
        for val in np.unique(arr):
            mask = arr == val
            if np.sum(mask) > 1:
                ranks[mask] = np.mean(ranks[mask])
        return ranks

    rx = rank(np.array(x, dtype=float))
    ry = rank(np.array(y, dtype=float))

    # Pearson of ranks = Spearman
    n = len(rx)
    mean_rx = np.mean(rx)
    mean_ry = np.mean(ry)
    num = np.sum((rx - mean_rx) * (ry - mean_ry))
    den = np.sqrt(np.sum((rx - mean_rx)**2) * np.sum((ry - mean_ry)**2))
    rho = num / den if den > 0 else 0

    # Approximate p-value for Spearman (using t-distribution approximation)
    if abs(rho) >= 1:
        p_val = 0.0
    else:
        t_stat = rho * np.sqrt((n - 2) / (1 - rho**2))
        # Approximate: for large n, t ≈ normal
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / np.sqrt(2))))

    return rho, p_val


def compute_pearson_r(x, y):
    """Compute Pearson correlation coefficient."""
    if len(x) < 3:
        return 0.0, 1.0

    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    n = len(x)

    mean_x, mean_y = np.mean(x), np.mean(y)
    num = np.sum((x - mean_x) * (y - mean_y))
    den = np.sqrt(np.sum((x - mean_x)**2) * np.sum((y - mean_y)**2))
    r = num / den if den > 0 else 0

    if abs(r) >= 1:
        p_val = 0.0
    else:
        t_stat = r * np.sqrt((n - 2) / (1 - r**2))
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / np.sqrt(2))))

    return r, p_val


def analyze_experiment(exp_name, baseline_p99):
    """Run windowed analysis for one experiment."""
    data = load_per_request_data(exp_name)
    if data is None:
        return None

    # Bucket into 100ms windows
    windows = bucket_into_windows(data, WINDOW_MS)
    if not windows:
        return None

    # Spike detection
    windows = detect_spikes(windows, baseline_p99)

    n_spike = sum(1 for w in windows if w["spike_class"] == "spike")
    n_calm = sum(1 for w in windows if w["spike_class"] == "calm")
    n_inter = sum(1 for w in windows if w["spike_class"] == "intermediate")

    # Correlation: p99 vs throughput (proxy for load-induced scheduling pressure)
    p99s = [w["app_p99"] for w in windows]
    throughputs = [w["throughput"] for w in windows]
    req_counts = [w["request_count"] for w in windows]

    rho, rho_p = compute_spearman_rho(p99s, throughputs)
    r, r_p = compute_pearson_r(p99s, throughputs)

    # Correlation: p99 vs request variability (std dev)
    stds = [w["app_std"] for w in windows]
    rho_std, _ = compute_spearman_rho(p99s, stds)

    return {
        "experiment": exp_name,
        "windows": windows,
        "n_windows": len(windows),
        "n_spike": n_spike,
        "n_calm": n_calm,
        "n_intermediate": n_inter,
        "spike_pct": round(n_spike / len(windows) * 100, 1) if windows else 0,
        "spearman_rho_p99_vs_throughput": round(rho, 3),
        "spearman_p_value": round(rho_p, 6),
        "pearson_r_p99_vs_throughput": round(r, 3),
        "pearson_p_value": round(r_p, 6),
        "spearman_rho_p99_vs_variability": round(rho_std, 3),
        "mean_throughput": round(np.mean(throughputs), 1),
        "mean_p99": round(np.mean(p99s), 2),
    }


def write_windowed_csv(results):
    """Write per-window data for all experiments."""
    csv_path = STATS_DIR / "windowed_correlation.csv"
    rows = []
    for r in results:
        if r is None:
            continue
        for w in r["windows"]:
            rows.append({
                "experiment": r["experiment"],
                "window_id": w["window_id"],
                "window_start_s": w["window_start_s"],
                "request_count": w["request_count"],
                "throughput": round(w["throughput"], 1),
                "app_p50_ms": round(w["app_p50"], 2),
                "app_p99_ms": round(w["app_p99"], 2),
                "app_mean_ms": round(w["app_mean"], 2),
                "app_std_ms": round(w["app_std"], 2),
                "spike_class": w["spike_class"],
            })

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {csv_path} ({len(rows)} windows)")


def write_spike_summary(results):
    """Write spike detection summary per experiment."""
    csv_path = STATS_DIR / "spike_summary.csv"
    rows = []
    for r in results:
        if r is None:
            continue
        rows.append({
            "experiment": r["experiment"],
            "n_windows": r["n_windows"],
            "n_spike": r["n_spike"],
            "n_calm": r["n_calm"],
            "n_intermediate": r["n_intermediate"],
            "spike_pct": r["spike_pct"],
            "spearman_rho": r["spearman_rho_p99_vs_throughput"],
            "spearman_p": r["spearman_p_value"],
            "pearson_r": r["pearson_r_p99_vs_throughput"],
            "pearson_p": r["pearson_p_value"],
            "mean_throughput": r["mean_throughput"],
            "mean_p99_ms": r["mean_p99"],
        })

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {csv_path} ({len(rows)} experiments)")


def plot_windowed_timeseries(results, baseline_p99):
    """Time-series of windowed p99 with spike/calm shading for key experiments."""
    key_exps = ["e1-baseline", "e3a-cfs-tight", "e4-noisy-neighbor", "e7-full-stress"]
    selected = [r for r in results if r is not None and r["experiment"] in key_exps]
    if not selected:
        selected = [r for r in results if r is not None][:4]

    if not selected:
        print("  → No data for windowed timeseries")
        return

    fig, axes = plt.subplots(len(selected), 1, figsize=(16, 3.5 * len(selected)), sharex=False)
    if len(selected) == 1:
        axes = [axes]

    spike_thresh = baseline_p99 * 2.0
    calm_thresh = baseline_p99 * 1.2

    for ax, r in zip(axes, selected):
        windows = r["windows"]
        times = [w["window_start_s"] for w in windows]
        p99s = [w["app_p99"] for w in windows]
        p50s = [w["app_p50"] for w in windows]

        # Color-code windows by spike class
        for w in windows:
            if w["spike_class"] == "spike":
                ax.axvspan(w["window_start_s"], w["window_start_s"] + 0.1,
                          alpha=0.15, color="red")
            elif w["spike_class"] == "calm":
                ax.axvspan(w["window_start_s"], w["window_start_s"] + 0.1,
                          alpha=0.05, color="green")

        ax.plot(times, p99s, color="darkred", linewidth=1.5, label="p99", zorder=3)
        ax.plot(times, p50s, color="steelblue", linewidth=1, linestyle="--",
                label="p50", alpha=0.7, zorder=2)

        # Threshold lines
        ax.axhline(spike_thresh, color="red", linestyle=":", alpha=0.4,
                   label=f"Spike threshold ({spike_thresh:.0f}ms)")
        ax.axhline(calm_thresh, color="green", linestyle=":", alpha=0.4,
                   label=f"Calm threshold ({calm_thresh:.0f}ms)")

        exp_label = r["experiment"].replace("e", "E", 1)
        ax.set_title(f"{exp_label} — {r['n_spike']} spikes / {r['n_windows']} windows "
                     f"({r['spike_pct']:.1f}%) | ρ={r['spearman_rho_p99_vs_throughput']:.2f}",
                     fontsize=10, fontweight="bold")
        ax.set_ylabel("Latency (ms)", fontsize=9)
        ax.legend(fontsize=7, loc="upper right", ncol=2)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time from experiment start (seconds)", fontsize=11)

    fig.suptitle("Windowed Correlation Analysis — 100ms Windows (Blueprint §06.2)\n"
                 "Red shading = spike windows (p99 > 2× baseline)",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_windowed_timeseries.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_spike_heatmap(results):
    """Heatmap of spike frequency across experiments."""
    valid = [r for r in results if r is not None]
    if not valid:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # ── Left: Spike percentage bar chart ──
    exps = [r["experiment"].replace("e", "E", 1) for r in valid]
    spike_pcts = [r["spike_pct"] for r in valid]
    colors = []
    for pct in spike_pcts:
        if pct > 50:
            colors.append("#e74c3c")
        elif pct > 20:
            colors.append("#e67e22")
        elif pct > 5:
            colors.append("#f39c12")
        else:
            colors.append("#2ecc71")

    y = np.arange(len(exps))
    ax1.barh(y, spike_pcts, color=colors, edgecolor="white", alpha=0.8)
    for i, pct in enumerate(spike_pcts):
        ax1.text(pct + 1, i, f"{pct:.1f}%", va="center", fontsize=8, fontweight="bold")
    ax1.set_yticks(y)
    ax1.set_yticklabels(exps, fontsize=9)
    ax1.set_xlabel("Spike Windows (%)", fontsize=11)
    ax1.set_title("Spike Frequency by Experiment", fontsize=12, fontweight="bold")
    ax1.grid(axis="x", alpha=0.3)
    ax1.invert_yaxis()

    # ── Right: Stacked bar of spike/calm/intermediate ──
    spikes = [r["n_spike"] for r in valid]
    calms = [r["n_calm"] for r in valid]
    inters = [r["n_intermediate"] for r in valid]

    ax2.barh(y, calms, color="#2ecc71", alpha=0.7, label="Calm (<1.2×)")
    ax2.barh(y, inters, left=calms, color="#f39c12", alpha=0.7, label="Intermediate")
    left_combined = [c + i for c, i in zip(calms, inters)]
    ax2.barh(y, spikes, left=left_combined, color="#e74c3c", alpha=0.7, label="Spike (>2×)")

    ax2.set_yticks(y)
    ax2.set_yticklabels(exps, fontsize=9)
    ax2.set_xlabel("Number of 100ms Windows", fontsize=11)
    ax2.set_title("Window Classification Distribution", fontsize=12, fontweight="bold")
    ax2.legend(fontsize=8)
    ax2.grid(axis="x", alpha=0.3)
    ax2.invert_yaxis()

    fig.suptitle("Spike Detection Summary — Blueprint §06.3\n"
                 "Spike = p99 > 2× baseline | Calm = p99 < 1.2× baseline",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_spike_heatmap.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_windowed_correlation(results, ebpf_data):
    """Scatter plot of windowed p99 vs request density across experiments."""
    valid = [r for r in results if r is not None]
    if not valid:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # ── Left: p99 vs throughput scatter (per-window, for a few experiments) ──
    cmap = plt.cm.tab10
    key_exps = ["e1-baseline", "e3a-cfs-tight", "e4-noisy-neighbor", "e7-full-stress"]
    plotted = 0
    for r in valid:
        if r["experiment"] not in key_exps:
            continue
        windows = r["windows"]
        p99s = [w["app_p99"] for w in windows]
        thrpts = [w["throughput"] for w in windows]

        # Downsample for readability
        if len(p99s) > 200:
            idx = np.random.choice(len(p99s), 200, replace=False)
            p99s = [p99s[i] for i in idx]
            thrpts = [thrpts[i] for i in idx]

        label = r["experiment"].replace("e", "E", 1) + f" (ρ={r['spearman_rho_p99_vs_throughput']:.2f})"
        ax1.scatter(thrpts, p99s, s=10, alpha=0.4, label=label, color=cmap(plotted))
        plotted += 1

    ax1.set_xlabel("Window Throughput (req/s)", fontsize=11)
    ax1.set_ylabel("Window p99 Latency (ms)", fontsize=11)
    ax1.set_title("Throughput vs p99 (per 100ms window)", fontsize=12, fontweight="bold")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    # ── Right: Experiment-level correlation summary ──
    exp_names = [r["experiment"].replace("e", "E", 1) for r in valid]
    rhos = [r["spearman_rho_p99_vs_throughput"] for r in valid]
    spike_pcts = [r["spike_pct"] for r in valid]

    colors = [plt.cm.RdYlGn_r(sp / 100 if max(spike_pcts) > 0 else 0) for sp in spike_pcts]
    y = np.arange(len(exp_names))
    ax2.barh(y, rhos, color=colors, edgecolor="white", alpha=0.8)
    for i, rho in enumerate(rhos):
        ax2.text(rho + 0.02 if rho >= 0 else rho - 0.08, i, f"ρ={rho:.2f}",
                va="center", fontsize=8, fontweight="bold")
    ax2.set_yticks(y)
    ax2.set_yticklabels(exp_names, fontsize=9)
    ax2.set_xlabel("Spearman ρ (p99 vs throughput)", fontsize=11)
    ax2.set_title("Per-Experiment Correlation", fontsize=12, fontweight="bold")
    ax2.axvline(0, color="black", linestyle="-", alpha=0.3)
    ax2.grid(axis="x", alpha=0.3)
    ax2.invert_yaxis()

    fig.suptitle("Windowed Correlation Analysis — Blueprint §06.2\n"
                 "Spearman ρ between 100ms window throughput and p99 latency",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_windowed_correlation.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def main():
    print("=" * 60)
    print("WINDOWED CORRELATION ANALYSIS — Blueprint §06.2, §06.3")
    print("=" * 60)

    ebpf_data = load_ebpf_data()

    # First, compute baseline p99 from E1
    baseline_data = load_per_request_data("e1-baseline")
    if baseline_data is None:
        print("ERROR: e1-baseline data required")
        sys.exit(1)

    baseline_lats = baseline_data["latencies_ms"]
    baseline_p99 = float(np.percentile(baseline_lats[baseline_lats > 0], 99))
    print(f"Baseline p99: {baseline_p99:.2f}ms (spike threshold: {baseline_p99 * 2:.2f}ms)\n")

    # Analyze each experiment
    results = []
    for exp in TARGET_EXPERIMENTS:
        print(f"  Processing {exp}...", end=" ")
        r = analyze_experiment(exp, baseline_p99)
        if r:
            print(f"{r['n_windows']} windows, {r['n_spike']} spikes ({r['spike_pct']:.1f}%), "
                  f"ρ={r['spearman_rho_p99_vs_throughput']:.3f}")
            results.append(r)
        else:
            print("SKIP (no data)")

    valid = [r for r in results if r is not None]
    print(f"\n{len(valid)} experiments analyzed\n")

    if not valid:
        print("ERROR: No experiment data available")
        sys.exit(1)

    # Print summary table
    print(f"{'Experiment':<30} {'Windows':>8} {'Spikes':>8} {'Spike%':>8} {'Spearman ρ':>12} {'Pearson r':>11}")
    print("─" * 82)
    for r in valid:
        print(f"{r['experiment']:<30} {r['n_windows']:>8} {r['n_spike']:>8} "
              f"{r['spike_pct']:>7.1f}% {r['spearman_rho_p99_vs_throughput']:>12.3f} "
              f"{r['pearson_r_p99_vs_throughput']:>11.3f}")

    # Write CSVs
    print("\nWriting results:")
    write_windowed_csv(valid)
    write_spike_summary(valid)

    # Generate plots
    print("\nGenerating plots:")
    plot_windowed_timeseries(valid, baseline_p99)
    plot_spike_heatmap(valid)
    plot_windowed_correlation(valid, ebpf_data)

    print(f"\n✓ Windowed correlation analysis complete")


if __name__ == "__main__":
    main()
