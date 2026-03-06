#!/usr/bin/env python3
"""
Comprehensive evidence plots — Blueprint §06.6
Generates all 10 publication-quality figures for the thesis.
"""
import csv, os, sys, json
import numpy as np
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    from matplotlib.patches import FancyBboxPatch
except ImportError:
    print("ERROR: matplotlib required. Install with: pip3 install matplotlib")
    sys.exit(1)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"
PLOT_DIR.mkdir(exist_ok=True)

# ─── Color palette ───
COLORS = {
    "baseline": "#2ecc71",
    "contention": "#e74c3c",
    "throttle": "#e67e22",
    "network": "#3498db",
    "mitigation": "#9b59b6",
    "stress": "#c0392b",
    "neutral": "#95a5a6",
    "highlight": "#f39c12",
}

# ─── Load data ───
def load_csv():
    csv_path = DATA_DIR / "all_experiments_summary.csv"
    if not csv_path.exists():
        print("ERROR: Run analyze_all.py first to generate CSV")
        sys.exit(1)
    rows = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            for k in ["p50_ms","p90_ms","p99_ms","avg_ms","rps","count","ok","errors","error_pct"]:
                if k in row:
                    row[k] = float(row[k])
            rows.append(row)
    return rows

def aggregate(rows):
    """Aggregate runs per experiment → mean, std, min, max."""
    exps = {}
    for r in rows:
        name = r["experiment"]
        if name not in exps:
            exps[name] = []
        exps[name].append(r)
    agg = {}
    for name, runs in exps.items():
        p99s = [r["p99_ms"] for r in runs]
        p50s = [r["p50_ms"] for r in runs]
        p90s = [r["p90_ms"] for r in runs]
        rpss = [r["rps"] for r in runs]
        errs = [r["error_pct"] for r in runs]
        # p999 if available in CSV
        p999s = [r.get("p999_ms", r["p99_ms"]) for r in runs]
        if isinstance(p999s[0], str):
            p999s = [float(x) for x in p999s]
        agg[name] = {
            "p50_mean": np.mean(p50s), "p50_std": np.std(p50s),
            "p90_mean": np.mean(p90s), "p90_std": np.std(p90s),
            "p99_mean": np.mean(p99s), "p99_std": np.std(p99s),
            "p99_min": np.min(p99s), "p99_max": np.max(p99s),
            "p999_mean": np.mean(p999s), "p999_std": np.std(p999s),
            "rps_mean": np.mean(rpss), "rps_std": np.std(rpss),
            "err_mean": np.mean(errs),
            "n_runs": len(runs),
        }
    return agg

def load_ghz_latencies(exp_name, run_idx=0):
    """Load histogram buckets from a ghz JSON (for CDF)."""
    exp_dir = DATA_DIR / exp_name
    jsons = sorted(exp_dir.glob("*.json"))
    if run_idx >= len(jsons):
        return [], []
    with open(jsons[run_idx]) as f:
        d = json.load(f)
    hist = d.get("histogram") or []
    if not hist:
        return [], []
    marks = []
    counts = []
    for bucket in hist:
        marks.append(bucket.get("mark", 0) * 1000)  # seconds → ms
        counts.append(bucket.get("count", 0))
    return marks, counts

def get_color(name):
    if "baseline" in name or name in ("e0-no-instrumentation", "e1-baseline"):
        return COLORS["baseline"]
    if "cfs" in name or "throttle" in name or name in ("e3a-cfs-tight","e3b-cfs-moderate","e5-throttle-crossnode","e10-throttle-contention"):
        return COLORS["throttle"]
    if "contention" in name or "noisy" in name or name in ("e4-noisy-neighbor","e2-cpu-contention"):
        return COLORS["contention"]
    if "cross-node" in name or "network" in name or name == "e2-cross-node":
        return COLORS["network"]
    if "isolation" in name or "pinning" in name or name in ("e13-cpu-pinning","e14-pinning-stress-crossnode","e15-full-isolation"):
        return COLORS["mitigation"]
    if "stress" in name or name == "e7-full-stress":
        return COLORS["stress"]
    if "host" in name or name == "e8-hostnetwork":
        return COLORS["network"]
    return COLORS["neutral"]

# ═══════════════════════════════════════════════════
# Figure 1: p99 bar chart (all experiments)
# ═══════════════════════════════════════════════════
def fig1_p99_bar(agg):
    order = [
        "e0-no-instrumentation", "e1-baseline", "e2-cross-node", "e3-memory-pressure",
        "e3a-cfs-tight", "e3b-cfs-moderate", "e4-noisy-neighbor", "e5-throttle-crossnode",
        "e6-contention-crossnode", "e7-full-stress", "e8-hostnetwork",
        "e9-hostnet-stress-crossnode", "e10-throttle-contention",
        "e11-network-policy", "e12-hpa", "e13-cpu-pinning",
        "e14-pinning-stress-crossnode", "e15-full-isolation",
        "e2-cpu-contention", "e8-resource-limits",
    ]
    names = [n for n in order if n in agg]
    fig, ax = plt.subplots(figsize=(16, 7))
    x = np.arange(len(names))
    colors = [get_color(n) for n in names]
    p99s = [agg[n]["p99_mean"] for n in names]
    errs = [agg[n]["p99_std"] for n in names]
    bars = ax.bar(x, p99s, yerr=errs, color=colors, edgecolor="white", linewidth=0.5, capsize=3, alpha=0.9)
    # Add value labels
    for bar, val in zip(bars, p99s):
        y = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, y + max(p99s)*0.02, f"{val:.1f}",
                ha="center", va="bottom", fontsize=7, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("e","E",1) for n in names], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax.set_title("Fig 1: p99 Latency Across All Experiments\n(ghz does not report p99.9; p99 is highest available percentile)",
                 fontsize=13, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    baseline_p99 = agg.get("e1-baseline", {}).get("p99_mean", 0)
    if baseline_p99 > 0:
        ax.axhline(baseline_p99, color=COLORS["baseline"], linestyle="--", alpha=0.5, label=f"Baseline p99={baseline_p99:.1f}ms")
        ax.legend(fontsize=9)
    fig.tight_layout()
    path = PLOT_DIR / "fig1_p99_bar_chart.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 2: Wakeup delay proxy vs p99 (scatter)
# ═══════════════════════════════════════════════════
def fig2_scatter_p99_vs_baseline(agg):
    baseline_p99 = agg.get("e1-baseline", {}).get("p99_mean", 1)
    names = sorted(agg.keys())
    fig, ax = plt.subplots(figsize=(10, 7))
    for n in names:
        ratio = agg[n]["p99_mean"] / baseline_p99
        rps = agg[n]["rps_mean"]
        c = get_color(n)
        ax.scatter(ratio, agg[n]["p99_mean"], color=c, s=100, edgecolors="black", linewidth=0.5, zorder=3)
        ax.annotate(n.replace("e","E",1), (ratio, agg[n]["p99_mean"]),
                    fontsize=6, ha="left", va="bottom", xytext=(5, 5), textcoords="offset points")
    ax.set_xlabel("p99 / Baseline p99 (ratio)", fontsize=12)
    ax.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax.set_title("Fig 2: Latency Inflation Scatter Plot", fontsize=14, fontweight="bold")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3)
    ax.axhline(baseline_p99, color=COLORS["baseline"], linestyle="--", alpha=0.4)
    ax.axvline(1.0, color=COLORS["baseline"], linestyle="--", alpha=0.4)
    fig.tight_layout()
    path = PLOT_DIR / "fig2_inflation_scatter.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 3: CDF overlay (E1 vs E3a vs E7 vs E15)
# ═══════════════════════════════════════════════════
def fig3_cdf_overlay(agg):
    target_exps = ["e1-baseline", "e3a-cfs-tight", "e7-full-stress", "e15-full-isolation"]
    colors_map = {
        "e1-baseline": COLORS["baseline"],
        "e3a-cfs-tight": COLORS["throttle"],
        "e7-full-stress": COLORS["stress"],
        "e15-full-isolation": COLORS["mitigation"],
    }
    fig, ax = plt.subplots(figsize=(10, 7))
    for exp in target_exps:
        marks, counts = load_ghz_latencies(exp)
        if not marks:
            continue
        total = sum(counts)
        if total == 0:
            continue
        cumulative = np.cumsum(counts) / total
        ax.plot(marks, cumulative, label=exp.replace("e","E",1), color=colors_map.get(exp, "gray"), linewidth=2)
    ax.set_xlabel("Latency (ms)", fontsize=12)
    ax.set_ylabel("CDF", fontsize=12)
    ax.set_title("Fig 3: Latency CDF Overlay — Baseline vs Throttled vs Stress vs Isolated", fontsize=13, fontweight="bold")
    ax.set_xlim(0, min(250, ax.get_xlim()[1]))
    ax.axhline(0.99, color="gray", linestyle=":", alpha=0.5, label="p99 line")
    ax.axhline(0.50, color="gray", linestyle=":", alpha=0.3, label="p50 line")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig3_cdf_overlay.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 4: CFS throttle dose-response
# ═══════════════════════════════════════════════════
def fig4_dose_response(agg):
    configs = [
        ("Unlimited\n(E1)", "e1-baseline"),
        ("500m\n(E3b)", "e3b-cfs-moderate"),
        ("200m\n(E3a)", "e3a-cfs-tight"),
        ("100m\n(E2-cpu)", "e2-cpu-contention"),
    ]
    configs = [(l, n) for l, n in configs if n in agg]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))

    labels = [l for l, _ in configs]
    p99s = [agg[n]["p99_mean"] for _, n in configs]
    rpss = [agg[n]["rps_mean"] for _, n in configs]
    colors = [get_color(n) for _, n in configs]

    ax1.bar(labels, p99s, color=colors, edgecolor="white", alpha=0.9)
    for i, v in enumerate(p99s):
        ax1.text(i, v + max(p99s)*0.02, f"{v:.1f}ms", ha="center", fontsize=10, fontweight="bold")
    ax1.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax1.set_title("p99 vs CPU Limit", fontsize=13, fontweight="bold")
    ax1.grid(axis="y", alpha=0.3)

    ax2.bar(labels, rpss, color=colors, edgecolor="white", alpha=0.9)
    for i, v in enumerate(rpss):
        ax2.text(i, v + max(rpss)*0.02, f"{v:.0f}", ha="center", fontsize=10, fontweight="bold")
    ax2.set_ylabel("Throughput (RPS)", fontsize=12)
    ax2.set_title("Throughput vs CPU Limit", fontsize=13, fontweight="bold")
    ax2.grid(axis="y", alpha=0.3)

    fig.suptitle("Fig 4: CFS Bandwidth Throttle — Dose-Response Relationship", fontsize=14, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig4_dose_response.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 5: Contention vs baseline vs hostNetwork
# ═══════════════════════════════════════════════════
def fig5_contention_comparison(agg):
    groups = [
        ("E1\nBaseline", "e1-baseline"),
        ("E4\nNoisy\nNeighbor", "e4-noisy-neighbor"),
        ("E8\nhostNet", "e8-hostnetwork"),
        ("E6\nContention\n+XN", "e6-contention-crossnode"),
        ("E9\nhostNet\n+Stress+XN", "e9-hostnet-stress-crossnode"),
    ]
    groups = [(l, n) for l, n in groups if n in agg]
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(groups))
    p50s = [agg[n]["p50_mean"] for _, n in groups]
    p99s = [agg[n]["p99_mean"] for _, n in groups]
    w = 0.35

    ax.bar(x - w/2, p50s, w, label="p50", color="#3498db", alpha=0.8, edgecolor="white")
    ax.bar(x + w/2, p99s, w, label="p99", color="#e74c3c", alpha=0.8, edgecolor="white")
    for i, (p50, p99) in enumerate(zip(p50s, p99s)):
        ax.text(i - w/2, p50 + max(p99s)*0.02, f"{p50:.2f}", ha="center", fontsize=7)
        ax.text(i + w/2, p99 + max(p99s)*0.02, f"{p99:.2f}", ha="center", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([l for l, _ in groups], fontsize=9)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Fig 5: Runqueue Contention & softirq — Baseline vs Contention vs hostNetwork", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig5_contention_comparison.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 6: Cross-node effect (SN vs XN pairs)
# ═══════════════════════════════════════════════════
def fig6_crossnode_effect(agg):
    pairs = [
        ("E1/E2\nBaseline", "e1-baseline", "e2-cross-node"),
        ("E3a/E5\nThrottle", "e3a-cfs-tight", "e5-throttle-crossnode"),
        ("E4/E6\nContention", "e4-noisy-neighbor", "e6-contention-crossnode"),
        ("E10/E7\nThrottle+\nContention", "e10-throttle-contention", "e7-full-stress"),
    ]
    pairs = [(l, sn, xn) for l, sn, xn in pairs if sn in agg and xn in agg]
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(pairs))
    w = 0.35
    sn_vals = [agg[sn]["p99_mean"] for _, sn, _ in pairs]
    xn_vals = [agg[xn]["p99_mean"] for _, _, xn in pairs]

    ax.bar(x - w/2, sn_vals, w, label="Same-Node (SN)", color="#2ecc71", alpha=0.8, edgecolor="white")
    ax.bar(x + w/2, xn_vals, w, label="Cross-Node (XN)", color="#e74c3c", alpha=0.8, edgecolor="white")
    for i, (sv, xv) in enumerate(zip(sn_vals, xn_vals)):
        ratio = xv / sv if sv > 0 else 0
        ax.text(i, max(sv, xv) + max(max(sn_vals), max(xn_vals))*0.03,
                f"{ratio:.2f}×", ha="center", fontsize=10, fontweight="bold", color="#e74c3c")

    ax.set_xticks(x)
    ax.set_xticklabels([l for l, _, _ in pairs], fontsize=9)
    ax.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax.set_title("Fig 6: Cross-Node Placement Effect on p99 (SN vs XN)", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig6_crossnode_effect.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 7: Mitigation waterfall
# ═══════════════════════════════════════════════════
def fig7_mitigation_waterfall(agg):
    stages = [
        ("E7\nFull Stress", "e7-full-stress"),
        ("E10\nThrottle+\nContention", "e10-throttle-contention"),
        ("E13\nCPU Pinning", "e13-cpu-pinning"),
        ("E14\nPinning+\nStress+XN", "e14-pinning-stress-crossnode"),
        ("E15\nFull\nIsolation", "e15-full-isolation"),
        ("E1\nBaseline", "e1-baseline"),
    ]
    stages = [(l, n) for l, n in stages if n in agg]
    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(stages))
    p99s = [agg[n]["p99_mean"] for _, n in stages]
    colors_ = []
    for _, n in stages:
        if "stress" in n or "throttle" in n:
            colors_.append(COLORS["stress"])
        elif "pinning" in n or "isolation" in n:
            colors_.append(COLORS["mitigation"])
        else:
            colors_.append(COLORS["baseline"])

    bars = ax.bar(x, p99s, color=colors_, edgecolor="white", alpha=0.9)
    for i, (bar, val) in enumerate(zip(bars, p99s)):
        ax.text(bar.get_x() + bar.get_width()/2, val + max(p99s)*0.02,
                f"{val:.1f}ms", ha="center", fontsize=10, fontweight="bold")
        if i > 0:
            reduction = (1 - val / p99s[0]) * 100
            if reduction > 0:
                ax.text(bar.get_x() + bar.get_width()/2, val/2,
                        f"-{reduction:.0f}%", ha="center", fontsize=9, color="white", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([l for l, _ in stages], fontsize=9)
    ax.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax.set_title("Fig 7: Mitigation Waterfall — Worst Case → Best Case", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig7_mitigation_waterfall.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 8: Throughput impact
# ═══════════════════════════════════════════════════
def fig8_throughput(agg):
    order = [
        "e1-baseline", "e2-cross-node", "e3a-cfs-tight", "e3b-cfs-moderate",
        "e4-noisy-neighbor", "e7-full-stress", "e8-hostnetwork",
        "e12-hpa", "e15-full-isolation", "e2-cpu-contention",
    ]
    names = [n for n in order if n in agg]
    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(names))
    rpss = [agg[n]["rps_mean"] for n in names]
    colors_ = [get_color(n) for n in names]

    bars = ax.bar(x, rpss, color=colors_, edgecolor="white", alpha=0.9)
    ax.axhline(2000, color="gray", linestyle="--", alpha=0.5, label="Target: 2000 rps")
    for bar, val in zip(bars, rpss):
        ax.text(bar.get_x() + bar.get_width()/2, val + 30, f"{val:.0f}",
                ha="center", fontsize=8, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("e","E",1) for n in names], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Actual Throughput (RPS)", fontsize=12)
    ax.set_title("Fig 8: Throughput Degradation Under Different Conditions", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig8_throughput_impact.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 9: Error rate plot
# ═══════════════════════════════════════════════════
def fig9_error_rates(agg):
    names = sorted(agg.keys())
    names = [n for n in names if agg[n]["err_mean"] > 0]
    if not names:
        print("  → fig9 skipped (no experiments with errors)")
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(names))
    errs = [agg[n]["err_mean"] for n in names]
    colors_ = [get_color(n) for n in names]

    ax.bar(x, errs, color=colors_, edgecolor="white", alpha=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("e","E",1) for n in names], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Error Rate (%)", fontsize=12)
    ax.set_title("Fig 9: Error Rates Across Experiments", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = PLOT_DIR / "fig9_error_rates.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Figure 10: Latency variance (box-plot style)
# ═══════════════════════════════════════════════════
def fig10_variance(rows):
    from collections import defaultdict
    exps = defaultdict(list)
    for r in rows:
        exps[r["experiment"]].append(r["p99_ms"])
    order = [
        "e1-baseline", "e3a-cfs-tight", "e3b-cfs-moderate", "e4-noisy-neighbor",
        "e7-full-stress", "e13-cpu-pinning", "e15-full-isolation",
    ]
    names = [n for n in order if n in exps]
    data = [exps[n] for n in names]

    fig, ax = plt.subplots(figsize=(10, 6))
    bp = ax.boxplot(data, patch_artist=True, labels=[n.replace("e","E",1) for n in names])
    colors_ = [get_color(n) for n in names]
    for patch, c in zip(bp["boxes"], colors_):
        patch.set_facecolor(c)
        patch.set_alpha(0.7)

    ax.set_ylabel("p99 Latency (ms)", fontsize=12)
    ax.set_title("Fig 10: p99 Latency Variance Across Runs (3 Repetitions)", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    path = PLOT_DIR / "fig10_variance_boxplot.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")

# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════
def main():
    print("Loading CSV data...")
    rows = load_csv()
    agg = aggregate(rows)
    print(f"Found {len(agg)} experiments\n")
    print("Generating comprehensive evidence plots:")

    fig1_p99_bar(agg)
    fig2_scatter_p99_vs_baseline(agg)
    fig3_cdf_overlay(agg)
    fig4_dose_response(agg)
    fig5_contention_comparison(agg)
    fig6_crossnode_effect(agg)
    fig7_mitigation_waterfall(agg)
    fig8_throughput(agg)
    fig9_error_rates(agg)
    fig10_variance(rows)

    print(f"\n✓ All 10 evidence plots saved to {PLOT_DIR}/")

if __name__ == "__main__":
    main()
