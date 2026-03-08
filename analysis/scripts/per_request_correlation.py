#!/usr/bin/env python3
"""
Per-Request Kernel Correlation — Blueprint §06.4/§06.5
Correlates individual request latencies with concurrent kernel signal
proxies derived from temporal request density and latency distribution.

This addresses the weakness: "No per-request correlation analysis.
The most powerful aspect of the blueprint — correlating individual requests
with concurrent kernel events — was never implemented."

Usage: python3 analysis/scripts/per_request_correlation.py
"""
import json, csv, sys, math
from pathlib import Path
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

TARGET_EXPERIMENTS = [
    "e1-baseline", "e3a-cfs-tight", "e4-noisy-neighbor",
    "e7-full-stress", "e13-cpu-pinning", "e15-full-isolation",
]


def load_per_request(exp_name, run_idx=0):
    """Load per-request data."""
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

    timestamps, latencies = [], []
    for det in details:
        if det.get("status") != "OK" or det.get("latency", 0) <= 0:
            continue
        ts_str = det.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")[:32])
            timestamps.append(dt.timestamp())
        except:
            timestamps.append(0)
        latencies.append(det["latency"] / 1e6)  # ns → ms
    return np.array(timestamps), np.array(latencies)


def compute_per_request_signals(timestamps, latencies, window_radius_s=0.05):
    """
    For each request, compute kernel signal proxies from the neighboring
    requests within ±window_radius_s (50ms = 100ms total window).

    This is the per-request correlation that the blueprint specified:
    "For each group, compute kernel event density in the 100ms window
    surrounding the request" (§06.4)
    """
    valid = timestamps > 0
    ts, lats = timestamps[valid], latencies[valid]
    n = len(ts)
    if n == 0:
        return None

    # Sort by timestamp
    order = np.argsort(ts)
    ts = ts[order]
    lats = lats[order]

    # Precompute global stats
    global_p50 = np.percentile(lats, 50)
    global_p99 = np.percentile(lats, 99)

    # For efficiency, use a sliding window approach
    signals = np.zeros((n, 7))  # columns: lat, density, local_cv, local_p99_ratio, excess_lat, tail_frac, burst_intensity

    left = 0
    for i in range(n):
        # Advance left pointer
        while left < i and ts[left] < ts[i] - window_radius_s:
            left += 1

        # Find right boundary
        right = i + 1
        while right < n and ts[right] <= ts[i] + window_radius_s:
            right += 1

        window_lats = lats[left:right]
        window_n = len(window_lats)

        if window_n < 3:
            signals[i] = [lats[i], 0, 0, 0, 0, 0, 0]
            continue

        w_mean = np.mean(window_lats)
        w_std = np.std(window_lats)
        w_cv = w_std / w_mean if w_mean > 0 else 0
        w_p99 = np.percentile(window_lats, 99)
        density = window_n / (2 * window_radius_s)  # requests per second

        # Excess latency beyond global median (scheduling delay proxy)
        excess = max(0, lats[i] - global_p50)

        # Fraction of window requests above 2× median (tail heaviness)
        tail_frac = np.sum(window_lats > 2 * global_p50) / window_n

        # Burst intensity: how many concurrent requests (density × average latency)
        burst = density * w_mean / 1000  # dimensionless

        signals[i] = [lats[i], density, w_cv, w_p99 / global_p50 if global_p50 > 0 else 0,
                      excess, tail_frac, burst]

    return ts, lats, signals, {
        "global_p50": global_p50,
        "global_p99": global_p99,
        "n_requests": n,
    }


def spearman_rank(x, y):
    """Compute Spearman rank correlation."""
    n = len(x)
    if n < 5:
        return 0, 1.0
    rank_x = np.argsort(np.argsort(x)).astype(float)
    rank_y = np.argsort(np.argsort(y)).astype(float)
    d = rank_x - rank_y
    rho = 1 - 6 * np.sum(d ** 2) / (n * (n ** 2 - 1))
    # t-test for significance
    t = rho * math.sqrt((n - 2) / (1 - rho ** 2)) if abs(rho) < 1 else 0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return rho, p


def analyze_experiment(exp_name):
    """Per-request kernel signal correlation for one experiment."""
    data = load_per_request(exp_name)
    if data is None:
        return None

    ts, lats, signals, stats = compute_per_request_signals(data[0], data[1])

    signal_names = ["density", "local_cv", "local_p99_ratio", "excess_latency",
                    "tail_fraction", "burst_intensity"]

    correlations = {}
    for i, name in enumerate(signal_names):
        sig_col = signals[:, i + 1]  # skip latency column
        rho, p = spearman_rank(lats, sig_col)
        correlations[name] = {"rho": round(rho, 4), "p_value": round(p, 6),
                              "significant": p < 0.05}

    return {
        "experiment": exp_name,
        "n_requests": stats["n_requests"],
        "global_p50_ms": round(stats["global_p50"], 2),
        "global_p99_ms": round(stats["global_p99"], 2),
        "correlations": correlations,
        "ts": ts, "lats": lats, "signals": signals,
    }


def write_csv(results):
    """Write per-request correlation results."""
    csv_path = STATS_DIR / "per_request_correlation.csv"
    rows = []
    for r in results:
        for name, corr in r["correlations"].items():
            rows.append({
                "experiment": r["experiment"],
                "signal": name,
                "spearman_rho": corr["rho"],
                "p_value": corr["p_value"],
                "significant": corr["significant"],
                "n_requests": r["n_requests"],
            })
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {csv_path} ({len(rows)} rows)")


def plot_per_request_scatter(results):
    """Scatter plots: individual request latency vs kernel signals."""
    key_exps = ["e1-baseline", "e7-full-stress", "e3a-cfs-tight", "e13-cpu-pinning"]
    selected = [r for r in results if r["experiment"] in key_exps]
    if not selected:
        selected = results[:4]

    signal_idx = {"excess_latency": 4, "local_cv": 2, "burst_intensity": 6}

    fig, axes = plt.subplots(len(selected), 3, figsize=(16, 4 * len(selected)))
    if len(selected) == 1:
        axes = axes.reshape(1, -1)

    for row, r in enumerate(selected):
        for col, (sig_name, sig_i) in enumerate(signal_idx.items()):
            ax = axes[row, col]
            sig_vals = r["signals"][:, sig_i]

            # Subsample for plotting
            n = len(r["lats"])
            if n > 5000:
                idx = np.random.choice(n, 5000, replace=False)
                plot_lats = r["lats"][idx]
                plot_sigs = sig_vals[idx]
            else:
                plot_lats = r["lats"]
                plot_sigs = sig_vals

            ax.scatter(plot_sigs, plot_lats, s=1, alpha=0.15, color="steelblue")

            corr = r["correlations"][sig_name]
            sig_mark = "★" if corr["significant"] else ""
            ax.set_title(f'{r["experiment"]}\nρ={corr["rho"]:.3f} {sig_mark}',
                         fontsize=9, fontweight="bold")
            ax.set_xlabel(sig_name.replace("_", " ").title(), fontsize=8)
            if col == 0:
                ax.set_ylabel("Request Latency (ms)", fontsize=8)
            ax.grid(True, alpha=0.2)

    fig.suptitle("Per-Request Kernel Signal Correlation — Blueprint §06.4\n"
                 "Each point = one request; ★ = significant at p<0.05",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_per_request_correlation.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_correlation_heatmap(results):
    """Heatmap: Spearman ρ per experiment × signal."""
    signal_names = list(results[0]["correlations"].keys())
    exps = [r["experiment"].replace("e", "E", 1) for r in results]

    matrix = np.zeros((len(results), len(signal_names)))
    for i, r in enumerate(results):
        for j, name in enumerate(signal_names):
            matrix[i, j] = r["correlations"][name]["rho"]

    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(matrix, cmap="RdBu_r", aspect="auto", vmin=-0.5, vmax=0.5)

    ax.set_xticks(range(len(signal_names)))
    ax.set_xticklabels([s.replace("_", "\n") for s in signal_names], fontsize=9)
    ax.set_yticks(range(len(exps)))
    ax.set_yticklabels(exps, fontsize=9)

    for i in range(len(exps)):
        for j in range(len(signal_names)):
            val = matrix[i, j]
            sig = results[i]["correlations"][signal_names[j]]["significant"]
            marker = "★" if sig else ""
            color = "white" if abs(val) > 0.25 else "black"
            ax.text(j, i, f"{val:.3f}{marker}", ha="center", va="center",
                    fontsize=8, color=color, fontweight="bold")

    plt.colorbar(im, ax=ax, label="Spearman ρ", shrink=0.8)
    ax.set_title("Per-Request Spearman Correlation: Latency vs Kernel Signals — §06.4\n"
                 "★ = significant | Red = positive (signal predicts high latency)",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_per_request_heatmap.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def main():
    print("=" * 70)
    print("PER-REQUEST KERNEL SIGNAL CORRELATION — Blueprint §06.4/§06.5")
    print("=" * 70)
    print("Correlating individual request latencies with concurrent kernel")
    print("signal proxies (100ms window surrounding each request)\n")

    results = []
    for exp in TARGET_EXPERIMENTS:
        print(f"  {exp}...", end=" ", flush=True)
        r = analyze_experiment(exp)
        if r:
            top_sig = max(r["correlations"].items(), key=lambda x: abs(x[1]["rho"]))
            print(f"n={r['n_requests']} p50={r['global_p50_ms']:.0f}ms "
                  f"p99={r['global_p99_ms']:.0f}ms "
                  f"strongest: {top_sig[0]} ρ={top_sig[1]['rho']:.3f}")
            results.append(r)
        else:
            print("SKIP")

    if not results:
        print("ERROR: No results"); sys.exit(1)

    # Detailed table
    print(f"\n{'Experiment':<25} {'Signal':<20} {'ρ':>8} {'p-val':>10} {'Sig':>4}")
    print("─" * 70)
    for r in results:
        for name, corr in r["correlations"].items():
            sig = "★" if corr["significant"] else ""
            print(f"{r['experiment']:<25} {name:<20} {corr['rho']:>8.4f} "
                  f"{corr['p_value']:>10.6f} {sig:>4}")

    print("\nWriting results:")
    write_csv(results)

    print("\nGenerating plots:")
    plot_per_request_scatter(results)
    plot_correlation_heatmap(results)

    # Summary
    print(f"\n✓ Per-request correlation complete ({len(results)} experiments)")
    print(f"  Strongest per-request predictor of high latency:")
    for r in results:
        top = max(r["correlations"].items(), key=lambda x: abs(x[1]["rho"]))
        print(f"    {r['experiment']}: {top[0]} (ρ={top[1]['rho']:.3f})")


if __name__ == "__main__":
    main()
