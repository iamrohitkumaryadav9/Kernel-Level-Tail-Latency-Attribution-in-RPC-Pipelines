#!/usr/bin/env python3
"""
Case/Control Analysis — Blueprint §06.4
Splits per-request latencies into slow (case) and fast (control) groups,
compares kernel signal density, and runs statistical tests.
Usage: python3 analysis/scripts/case_control_analysis.py
"""
import json, csv, sys, os, math
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

# Experiments to analyze (key experiments from the blueprint)
TARGET_EXPERIMENTS = [
    "e1-baseline", "e3a-cfs-tight", "e3b-cfs-moderate",
    "e4-noisy-neighbor", "e7-full-stress", "e10-throttle-contention",
    "e13-cpu-pinning", "e15-full-isolation",
]

# ─── eBPF data (experiment-level, from ebpf_per_experiment.csv) ───
def load_ebpf_data():
    """Load per-experiment eBPF metrics."""
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


def load_per_request_data(exp_name, run_idx=0):
    """Load per-request details from ghz JSON."""
    exp_dir = DATA_DIR / exp_name
    if not exp_dir.exists():
        return None, None

    jsons = sorted(exp_dir.glob("*.json"))
    if run_idx >= len(jsons):
        return None, None

    with open(jsons[run_idx]) as f:
        d = json.load(f)

    details = d.get("details", [])
    if not details:
        return None, None

    # Extract timestamps and latencies
    timestamps = []
    latencies_ms = []
    for det in details:
        lat_ns = det.get("latency", 0)
        status = det.get("status", "")
        if status == "OK" and lat_ns > 0:
            latencies_ms.append(lat_ns / 1e6)
            # Parse timestamp
            ts_str = det.get("timestamp", "")
            try:
                # Handle Go timestamp format
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")[:32])
                timestamps.append(dt.timestamp())
            except:
                timestamps.append(0)

    return np.array(timestamps), np.array(latencies_ms)


def case_control_split(latencies_ms, timestamps):
    """
    Split requests into case and control groups — Blueprint §06.4.
    Case: latency > p99 (top ~1%)
    Control: latency in [p40, p60] (median band, ~20%)
    """
    p99 = np.percentile(latencies_ms, 99)
    p40 = np.percentile(latencies_ms, 40)
    p60 = np.percentile(latencies_ms, 60)

    case_mask = latencies_ms > p99
    control_mask = (latencies_ms >= p40) & (latencies_ms <= p60)

    return {
        "case_latencies": latencies_ms[case_mask],
        "case_timestamps": timestamps[case_mask],
        "control_latencies": latencies_ms[control_mask],
        "control_timestamps": timestamps[control_mask],
        "p99_threshold": p99,
        "p40": p40,
        "p60": p60,
        "n_case": int(np.sum(case_mask)),
        "n_control": int(np.sum(control_mask)),
        "n_total": len(latencies_ms),
    }


def temporal_window_analysis(timestamps, latencies_ms, split, window_s=0.1):
    """
    Temporal analysis: bucket requests into 100ms windows using np.digitize (O(n)).
    Identify which windows contain case vs control requests.
    """
    # Filter valid timestamps
    valid_mask = timestamps > 0
    ts = timestamps[valid_mask]
    lats = latencies_ms[valid_mask]

    if len(ts) == 0:
        return None

    t_start = ts.min()
    t_end = ts.max()
    n_windows = int((t_end - t_start) / window_s) + 1

    # Assign each request to a window bin in O(n) using digitize
    bins = np.arange(t_start, t_end + window_s, window_s)
    bin_indices = np.digitize(ts, bins) - 1  # 0-indexed
    bin_indices = np.clip(bin_indices, 0, n_windows - 1)

    # Precompute case/control masks
    case_mask = lats > split["p99_threshold"]
    ctrl_mask = (lats >= split["p40"]) & (lats <= split["p60"])

    window_data = []
    for i in range(min(n_windows, len(bins))):
        w_mask = bin_indices == i
        w_lats = lats[w_mask]

        if len(w_lats) == 0:
            continue

        n_case = int(np.sum(case_mask[w_mask]))
        n_control = int(np.sum(ctrl_mask[w_mask]))

        window_data.append({
            "window_start": i * window_s,
            "n_requests": len(w_lats),
            "p99_ms": float(np.percentile(w_lats, 99)) if len(w_lats) >= 3 else float(np.max(w_lats)),
            "p50_ms": float(np.percentile(w_lats, 50)),
            "mean_ms": float(np.mean(w_lats)),
            "n_case": n_case,
            "n_control": n_control,
            "has_case": n_case > 0,
            "throughput": len(w_lats) / window_s,
        })

    return window_data


def mann_whitney_u(x, y):
    """Simple Mann-Whitney U test (no scipy dependency)."""
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return 0, 0, 1.0

    combined = sorted([(v, 'x') for v in x] + [(v, 'y') for v in y], key=lambda t: t[0])
    rank_sum_x = sum(i + 1 for i, (_, g) in enumerate(combined) if g == 'x')
    U_x = rank_sum_x - nx * (nx + 1) / 2
    U_y = nx * ny - U_x
    U = min(U_x, U_y)
    mu = nx * ny / 2
    sigma = np.sqrt(nx * ny * (nx + ny + 1) / 12)
    z = (U - mu) / sigma if sigma > 0 else 0
    # Approximate p-value
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / np.sqrt(2))))
    return U, z, p


def cliffs_delta(x, y):
    """Cliff's delta effect size."""
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return 0
    # Optimized for large samples: use vectorized comparison
    if nx * ny > 1e7:
        # Sample for large datasets
        idx_x = np.random.choice(nx, min(nx, 5000), replace=False)
        idx_y = np.random.choice(ny, min(ny, 5000), replace=False)
        x_s, y_s = x[idx_x], y[idx_y]
        more = np.sum(x_s[:, None] > y_s[None, :])
        less = np.sum(x_s[:, None] < y_s[None, :])
        return (more - less) / (len(x_s) * len(y_s))
    else:
        more = np.sum(x[:, None] > y[None, :])
        less = np.sum(x[:, None] < y[None, :])
        return (more - less) / (nx * ny)


def analyze_experiment(exp_name, ebpf_data):
    """Run full case/control analysis for one experiment."""
    timestamps, latencies_ms = load_per_request_data(exp_name)
    if timestamps is None:
        return None

    # Case/control split
    split = case_control_split(latencies_ms, timestamps)

    # Temporal window analysis
    window_data = temporal_window_analysis(timestamps, latencies_ms, split)

    # Statistical comparison of windows containing case vs control requests
    stats = {}
    if window_data:
        case_windows = [w for w in window_data if w["has_case"]]
        control_windows = [w for w in window_data if not w["has_case"] and w["n_control"] > 0]

        if case_windows and control_windows:
            # Compare throughput in case vs control windows
            case_throughputs = np.array([w["throughput"] for w in case_windows])
            control_throughputs = np.array([w["throughput"] for w in control_windows])

            case_p99s = np.array([w["p99_ms"] for w in case_windows])
            control_p99s = np.array([w["p99_ms"] for w in control_windows])

            U, z, p = mann_whitney_u(case_p99s, control_p99s)
            d = cliffs_delta(case_p99s, control_p99s)

            stats["n_case_windows"] = len(case_windows)
            stats["n_control_windows"] = len(control_windows)
            stats["case_mean_throughput"] = round(np.mean(case_throughputs), 1)
            stats["control_mean_throughput"] = round(np.mean(control_throughputs), 1)
            stats["case_mean_p99"] = round(np.mean(case_p99s), 2)
            stats["control_mean_p99"] = round(np.mean(control_p99s), 2)
            stats["U_statistic"] = round(U, 1)
            stats["z_score"] = round(z, 3)
            stats["p_value"] = round(p, 6)
            stats["cliffs_delta"] = round(d, 3)

            # Latency ratio (case requests are how much slower?)
            stats["case_control_latency_ratio"] = round(
                np.mean(split["case_latencies"]) / np.mean(split["control_latencies"]), 2
            )

    # eBPF association
    ebpf = ebpf_data.get(exp_name, {})

    return {
        "experiment": exp_name,
        "split": split,
        "window_data": window_data,
        "stats": stats,
        "ebpf": ebpf,
    }


def write_results_csv(results):
    """Write case/control analysis results to CSV."""
    csv_path = STATS_DIR / "case_control_results.csv"
    rows = []
    for r in results:
        if r is None:
            continue
        split = r["split"]
        stats = r["stats"]
        ebpf = r["ebpf"]
        rows.append({
            "experiment": r["experiment"],
            "n_total": split["n_total"],
            "n_case": split["n_case"],
            "n_control": split["n_control"],
            "p99_threshold_ms": round(split["p99_threshold"], 2),
            "case_mean_latency_ms": round(np.mean(split["case_latencies"]), 2),
            "control_mean_latency_ms": round(np.mean(split["control_latencies"]), 2),
            "case_control_ratio": stats.get("case_control_latency_ratio", ""),
            "n_case_windows": stats.get("n_case_windows", ""),
            "n_control_windows": stats.get("n_control_windows", ""),
            "window_U_stat": stats.get("U_statistic", ""),
            "window_p_value": stats.get("p_value", ""),
            "window_cliffs_delta": stats.get("cliffs_delta", ""),
            "ebpf_wakeup_p99_us": ebpf.get("rqdelay_p99_us", ""),
            "ebpf_softirq_count": ebpf.get("softirq_count", ""),
            "ebpf_tcp_retransmit": ebpf.get("tcp_retransmit", ""),
        })

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {csv_path} ({len(rows)} experiments)")


def plot_case_control_latency(results):
    """Violin/box plot: case vs control latency distribution per experiment."""
    valid = [r for r in results if r is not None and r["split"]["n_case"] > 0]
    if not valid:
        print("  → No valid results for latency plot")
        return

    n_exps = len(valid)
    fig, axes = plt.subplots(1, n_exps, figsize=(4 * n_exps, 6), sharey=False)
    if n_exps == 1:
        axes = [axes]

    for ax, r in zip(axes, valid):
        split = r["split"]
        case_data = split["case_latencies"]
        ctrl_data = split["control_latencies"]

        # Box plots side by side
        data = [ctrl_data, case_data]
        bp = ax.boxplot(data, patch_artist=True, labels=["Control\n(p40-p60)", "Case\n(>p99)"],
                       widths=0.6, showfliers=False)

        bp["boxes"][0].set_facecolor("#2ecc71")
        bp["boxes"][0].set_alpha(0.7)
        bp["boxes"][1].set_facecolor("#e74c3c")
        bp["boxes"][1].set_alpha(0.7)

        # Add median annotations
        ax.text(1, np.median(ctrl_data), f" {np.median(ctrl_data):.1f}ms",
                fontsize=8, va="center", ha="left", color="#27ae60")
        ax.text(2, np.median(case_data), f" {np.median(case_data):.1f}ms",
                fontsize=8, va="center", ha="left", color="#c0392b")

        exp_label = r["experiment"].replace("e", "E", 1)
        ratio = r["stats"].get("case_control_latency_ratio", 0)
        ax.set_title(f"{exp_label}\n({ratio:.1f}× slower)", fontsize=10, fontweight="bold")
        ax.set_ylabel("Latency (ms)", fontsize=9)
        ax.grid(axis="y", alpha=0.3)

        # Add sample sizes
        ax.text(0.5, -0.12, f"n={split['n_control']}", transform=ax.transAxes,
                fontsize=7, ha="left", color="#27ae60")
        ax.text(0.5, -0.16, f"n={split['n_case']}", transform=ax.transAxes,
                fontsize=7, ha="right", color="#c0392b")

    fig.suptitle("Case/Control Latency Comparison — Blueprint §06.4\n"
                 "Case (>p99 latency) vs Control (p40-p60 median band)",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_case_control_latency.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_case_control_temporal(results):
    """Temporal heatmap showing where slow requests cluster."""
    valid = [r for r in results if r is not None and r.get("window_data")]
    if not valid:
        print("  → No valid results for temporal plot")
        return

    # Select up to 4 key experiments for the temporal plot
    key_exps = ["e1-baseline", "e3a-cfs-tight", "e4-noisy-neighbor", "e7-full-stress"]
    selected = [r for r in valid if r["experiment"] in key_exps]
    if not selected:
        selected = valid[:4]

    fig, axes = plt.subplots(len(selected), 1, figsize=(14, 3 * len(selected)), sharex=True)
    if len(selected) == 1:
        axes = [axes]

    for ax, r in zip(axes, selected):
        wd = r["window_data"]
        times = [w["window_start"] for w in wd]
        p99s = [w["p99_ms"] for w in wd]
        has_case = [w["has_case"] for w in wd]

        # Color by case/control
        colors = ["#e74c3c" if hc else "#3498db" for hc in has_case]
        alphas = [0.8 if hc else 0.3 for hc in has_case]

        ax.bar(times, p99s, width=0.1, color=colors, alpha=0.6)
        ax.set_ylabel("Window p99 (ms)", fontsize=9)

        exp_label = r["experiment"].replace("e", "E", 1)
        n_case = sum(1 for hc in has_case if hc)
        pct = n_case / len(has_case) * 100 if has_case else 0
        ax.set_title(f"{exp_label} — {n_case}/{len(has_case)} windows contain >p99 requests ({pct:.1f}%)",
                     fontsize=10, fontweight="bold")
        ax.grid(axis="y", alpha=0.3)

        # Threshold line
        p99_thresh = r["split"]["p99_threshold"]
        ax.axhline(p99_thresh, color="gray", linestyle=":", alpha=0.5)

    axes[-1].set_xlabel("Time (seconds from experiment start)", fontsize=11)

    # Legend
    from matplotlib.patches import Patch
    legend_elements = [Patch(facecolor="#e74c3c", alpha=0.6, label="Window has >p99 requests (case)"),
                       Patch(facecolor="#3498db", alpha=0.3, label="Normal window")]
    axes[0].legend(handles=legend_elements, fontsize=8, loc="upper right")

    fig.suptitle("Case/Control Temporal Analysis — 100ms Windows\n"
                 "Red bars = windows containing slow (>p99) requests",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_case_control_temporal.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_kernel_signal_comparison(results, ebpf_data):
    """Compare kernel signals between experiments with high vs low case/control ratios."""
    valid = [r for r in results if r is not None and r["stats"] and r["ebpf"]]
    if len(valid) < 3:
        print("  → Not enough experiments with eBPF data for kernel signal plot")
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    exps = [r["experiment"] for r in valid]
    x = np.arange(len(exps))

    # ─ Panel 1: Case/control latency ratio vs wakeup delay ─
    ratios = [r["stats"].get("case_control_latency_ratio", 1) for r in valid]
    wakeups = [r["ebpf"].get("rqdelay_p99_us", 0) for r in valid]
    axes[0].scatter(wakeups, ratios, s=80, c=[plt.cm.RdYlGn_r(r/max(ratios)) for r in ratios],
                    edgecolors="black", zorder=3)
    for i, exp in enumerate(exps):
        axes[0].annotate(exp.replace("e", "E", 1), (wakeups[i], ratios[i]),
                        fontsize=7, ha="left", xytext=(5, 5), textcoords="offset points")
    axes[0].set_xlabel("eBPF Wakeup Delay p99 (µs)", fontsize=10)
    axes[0].set_ylabel("Case/Control Latency Ratio", fontsize=10)
    axes[0].set_title("Wakeup Delay vs Slow Requests", fontsize=11, fontweight="bold")
    axes[0].grid(True, alpha=0.3)

    # ─ Panel 2: Softirq count vs latency ratio ─
    softirqs = [r["ebpf"].get("softirq_count", 0) for r in valid]
    axes[1].scatter(softirqs, ratios, s=80, c=[plt.cm.RdYlGn_r(r/max(ratios)) for r in ratios],
                    edgecolors="black", zorder=3)
    for i, exp in enumerate(exps):
        axes[1].annotate(exp.replace("e", "E", 1), (softirqs[i], ratios[i]),
                        fontsize=7, ha="left", xytext=(5, 5), textcoords="offset points")
    axes[1].set_xlabel("Softirq Count", fontsize=10)
    axes[1].set_ylabel("Case/Control Latency Ratio", fontsize=10)
    axes[1].set_title("Softirq Activity vs Slow Requests", fontsize=11, fontweight="bold")
    axes[1].grid(True, alpha=0.3)

    # ─ Panel 3: TCP retransmit vs latency ratio ─
    retrans = [r["ebpf"].get("tcp_retransmit", 0) for r in valid]
    axes[2].scatter(retrans, ratios, s=80, c=[plt.cm.RdYlGn_r(r/max(ratios)) for r in ratios],
                    edgecolors="black", zorder=3)
    for i, exp in enumerate(exps):
        axes[2].annotate(exp.replace("e", "E", 1), (retrans[i], ratios[i]),
                        fontsize=7, ha="left", xytext=(5, 5), textcoords="offset points")
    axes[2].set_xlabel("TCP Retransmits", fontsize=10)
    axes[2].set_ylabel("Case/Control Latency Ratio", fontsize=10)
    axes[2].set_title("TCP Retransmits vs Slow Requests", fontsize=11, fontweight="bold")
    axes[2].grid(True, alpha=0.3)

    fig.suptitle("Kernel Signal Association with Case/Control Split — Blueprint §06.4",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_case_control_kernel.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def main():
    print("=" * 60)
    print("CASE/CONTROL ANALYSIS — Blueprint §06.4")
    print("=" * 60)

    ebpf_data = load_ebpf_data()
    print(f"Loaded eBPF data for {len(ebpf_data)} experiments\n")

    results = []
    for exp in TARGET_EXPERIMENTS:
        print(f"  Analyzing {exp}...", end=" ")
        r = analyze_experiment(exp, ebpf_data)
        if r:
            split = r["split"]
            stats = r["stats"]
            ratio = stats.get("case_control_latency_ratio", 0)
            print(f"n={split['n_total']}  case={split['n_case']}  control={split['n_control']}  "
                  f"ratio={ratio:.2f}×")
            results.append(r)
        else:
            print("SKIP (no data)")

    valid = [r for r in results if r is not None]
    print(f"\n{len(valid)} experiments analyzed\n")

    if not valid:
        print("ERROR: No experiment data available")
        sys.exit(1)

    # Print statistical summary
    print("Statistical Summary (case-window vs control-window p99):")
    print(f"  {'Experiment':<30} {'Case/Ctrl Ratio':>15} {'Cliff δ':>10} {'p-value':>10}")
    print("  " + "─" * 70)
    for r in valid:
        stats = r["stats"]
        print(f"  {r['experiment']:<30} "
              f"{stats.get('case_control_latency_ratio', 'N/A'):>15} "
              f"{stats.get('cliffs_delta', 'N/A'):>10} "
              f"{stats.get('p_value', 'N/A'):>10}")

    # Write CSV
    print("\nWriting results:")
    write_results_csv(valid)

    # Generate plots
    print("\nGenerating plots:")
    plot_case_control_latency(valid)
    plot_case_control_temporal(valid)
    plot_kernel_signal_comparison(valid, ebpf_data)

    print(f"\n✓ Case/control analysis complete")


if __name__ == "__main__":
    main()
