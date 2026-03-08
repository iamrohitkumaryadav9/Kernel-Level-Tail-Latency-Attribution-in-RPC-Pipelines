#!/usr/bin/env python3
"""
Spike Detection Algorithm — Blueprint §06.3
Formal sliding-window p999 spike/calm classification with configurable thresholds.
Usage: python3 analysis/scripts/spike_detection.py
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

# Blueprint §06.3 parameters
WINDOW_WIDTH_S = 1.0       # 1-second sliding window
WINDOW_STEP_S = 0.1        # 100ms step
SPIKE_MULTIPLIER = 2.0     # spike = p999 > 2× baseline
CALM_MULTIPLIER = 1.2      # calm = p999 < 1.2× baseline

TARGET_EXPERIMENTS = [
    "e1-baseline", "e3a-cfs-tight", "e3b-cfs-moderate",
    "e4-noisy-neighbor", "e5-throttle-crossnode",
    "e7-full-stress", "e10-throttle-contention",
    "e13-cpu-pinning", "e15-full-isolation",
]


def load_per_request(exp_name, run_idx=0):
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


def sliding_window_p999(timestamps, latencies, width=1.0, step=0.1):
    """
    Blueprint §06.3: Sliding-window p99.9 computation.
    Returns arrays of (center_times, p999_values, p99_values, p50_values, counts)
    """
    valid = timestamps > 0
    ts, lats = timestamps[valid], latencies[valid]
    if len(ts) == 0:
        return [], [], [], [], []

    t0, t1 = ts.min(), ts.max()
    centers, p999s, p99s, p50s, counts = [], [], [], [], []

    t = t0 + width / 2
    while t <= t1 - width / 2:
        mask = (ts >= t - width/2) & (ts < t + width/2)
        w_lats = lats[mask]
        if len(w_lats) >= 10:  # Need enough samples for p999
            centers.append(t - t0)  # relative time
            p999s.append(float(np.percentile(w_lats, 99.9)))
            p99s.append(float(np.percentile(w_lats, 99)))
            p50s.append(float(np.percentile(w_lats, 50)))
            counts.append(len(w_lats))
        t += step

    return (np.array(centers), np.array(p999s), np.array(p99s),
            np.array(p50s), np.array(counts))


def classify_windows(p999s, baseline_p999, spike_mult=2.0, calm_mult=1.2):
    """
    Blueprint §06.3: Classify each window as spike/calm/intermediate.
    spike:  p999 > spike_mult × baseline_p999
    calm:   p999 < calm_mult × baseline_p999
    """
    spike_thresh = baseline_p999 * spike_mult
    calm_thresh = baseline_p999 * calm_mult

    classes = []
    for p in p999s:
        if p > spike_thresh:
            classes.append("spike")
        elif p < calm_thresh:
            classes.append("calm")
        else:
            classes.append("intermediate")
    return classes, spike_thresh, calm_thresh


def compute_spike_metrics(centers, p999s, classes):
    """Compute spike burst statistics: burst length, inter-spike intervals."""
    spike_mask = np.array([c == "spike" for c in classes])
    if not np.any(spike_mask):
        return {"n_bursts": 0, "mean_burst_len_s": 0, "max_burst_len_s": 0,
                "mean_inter_spike_s": 0, "spike_p999_mean": 0, "calm_p999_mean": 0}

    # Detect contiguous spike bursts
    bursts = []
    in_burst = False
    burst_start = 0
    for i, sc in enumerate(classes):
        if sc == "spike" and not in_burst:
            burst_start = centers[i]
            in_burst = True
        elif sc != "spike" and in_burst:
            bursts.append((burst_start, centers[i-1]))
            in_burst = False
    if in_burst:
        bursts.append((burst_start, centers[-1]))

    burst_durations = [end - start for start, end in bursts]
    inter_spikes = [bursts[i+1][0] - bursts[i][1] for i in range(len(bursts)-1)] if len(bursts) > 1 else []

    spike_p999s = p999s[spike_mask]
    calm_mask = np.array([c == "calm" for c in classes])
    calm_p999s = p999s[calm_mask] if np.any(calm_mask) else np.array([0])

    return {
        "n_bursts": len(bursts),
        "mean_burst_len_s": round(np.mean(burst_durations), 3) if burst_durations else 0,
        "max_burst_len_s": round(np.max(burst_durations), 3) if burst_durations else 0,
        "mean_inter_spike_s": round(np.mean(inter_spikes), 3) if inter_spikes else 0,
        "spike_p999_mean": round(float(np.mean(spike_p999s)), 2),
        "calm_p999_mean": round(float(np.mean(calm_p999s)), 2),
    }


def analyze_experiment(exp_name, baseline_p999):
    """Full spike detection for one experiment."""
    data = load_per_request(exp_name)
    if data is None:
        return None
    timestamps, latencies = data

    centers, p999s, p99s, p50s, counts = sliding_window_p999(
        timestamps, latencies, WINDOW_WIDTH_S, WINDOW_STEP_S)

    if len(centers) == 0:
        return None

    classes, spike_thresh, calm_thresh = classify_windows(
        p999s, baseline_p999, SPIKE_MULTIPLIER, CALM_MULTIPLIER)

    n_spike = sum(1 for c in classes if c == "spike")
    n_calm = sum(1 for c in classes if c == "calm")
    n_inter = sum(1 for c in classes if c == "intermediate")

    metrics = compute_spike_metrics(centers, p999s, classes)

    return {
        "experiment": exp_name,
        "centers": centers, "p999s": p999s, "p99s": p99s, "p50s": p50s,
        "counts": counts, "classes": classes,
        "spike_thresh": spike_thresh, "calm_thresh": calm_thresh,
        "n_windows": len(centers),
        "n_spike": n_spike, "n_calm": n_calm, "n_intermediate": n_inter,
        "spike_pct": round(n_spike / len(centers) * 100, 1),
        "metrics": metrics,
    }


def write_spike_csv(results):
    """Write detailed spike detection results."""
    csv_path = STATS_DIR / "spike_detection.csv"
    rows = []
    for r in results:
        if r is None:
            continue
        m = r["metrics"]
        rows.append({
            "experiment": r["experiment"],
            "n_windows": r["n_windows"],
            "n_spike": r["n_spike"],
            "n_calm": r["n_calm"],
            "n_intermediate": r["n_intermediate"],
            "spike_pct": r["spike_pct"],
            "spike_threshold_ms": round(r["spike_thresh"], 2),
            "calm_threshold_ms": round(r["calm_thresh"], 2),
            "n_spike_bursts": m["n_bursts"],
            "mean_burst_duration_s": m["mean_burst_len_s"],
            "max_burst_duration_s": m["max_burst_len_s"],
            "mean_inter_spike_s": m["mean_inter_spike_s"],
            "spike_p999_mean_ms": m["spike_p999_mean"],
            "calm_p999_mean_ms": m["calm_p999_mean"],
        })

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {csv_path} ({len(rows)} experiments)")


def plot_spike_timeseries(results, baseline_p999):
    """Multi-panel time-series showing p999 with spike/calm classification."""
    key_exps = ["e1-baseline", "e3a-cfs-tight", "e7-full-stress", "e13-cpu-pinning"]
    selected = [r for r in results if r is not None and r["experiment"] in key_exps]
    if not selected:
        selected = [r for r in results if r is not None][:4]
    if not selected:
        return

    fig, axes = plt.subplots(len(selected), 1, figsize=(16, 3.5 * len(selected)))
    if len(selected) == 1:
        axes = [axes]

    for ax, r in zip(axes, selected):
        centers = r["centers"]
        p999s = r["p999s"]
        classes = r["classes"]

        # Color each segment
        for i in range(len(centers) - 1):
            color = "#e74c3c" if classes[i] == "spike" else "#2ecc71" if classes[i] == "calm" else "#f39c12"
            ax.plot(centers[i:i+2], p999s[i:i+2], color=color, linewidth=1.5)

        # Background shading
        for i, (c, cls) in enumerate(zip(centers, classes)):
            if cls == "spike":
                ax.axvspan(c - 0.05, c + 0.05, alpha=0.08, color="red")

        ax.axhline(r["spike_thresh"], color="red", linestyle=":", alpha=0.5,
                   label=f'Spike ({r["spike_thresh"]:.0f}ms)')
        ax.axhline(r["calm_thresh"], color="green", linestyle=":", alpha=0.5,
                   label=f'Calm ({r["calm_thresh"]:.0f}ms)')

        m = r["metrics"]
        exp_label = r["experiment"].replace("e", "E", 1)
        ax.set_title(f"{exp_label} — {r['n_spike']} spikes ({r['spike_pct']:.1f}%), "
                     f"{m['n_bursts']} bursts, mean burst={m['mean_burst_len_s']:.1f}s",
                     fontsize=10, fontweight="bold")
        ax.set_ylabel("p99.9 (ms)", fontsize=9)
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (seconds)", fontsize=11)
    fig.suptitle(f"Spike Detection — Blueprint §06.3\n"
                 f"Window={WINDOW_WIDTH_S:.0f}s, Step={WINDOW_STEP_S*1000:.0f}ms, "
                 f"Spike=p999>{SPIKE_MULTIPLIER:.0f}× baseline ({baseline_p999:.0f}ms)",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_spike_detection.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def plot_spike_summary(results):
    """Summary plot: spike characteristics across experiments."""
    valid = [r for r in results if r is not None]
    if not valid:
        return

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 5))

    exps = [r["experiment"].replace("e", "E", 1) for r in valid]
    y = np.arange(len(exps))

    # Panel 1: Spike percentage
    spike_pcts = [r["spike_pct"] for r in valid]
    colors = ["#e74c3c" if p > 50 else "#e67e22" if p > 10 else "#2ecc71" for p in spike_pcts]
    ax1.barh(y, spike_pcts, color=colors, edgecolor="white")
    for i, p in enumerate(spike_pcts):
        ax1.text(p + 1, i, f"{p:.1f}%", va="center", fontsize=8)
    ax1.set_yticks(y)
    ax1.set_yticklabels(exps, fontsize=8)
    ax1.set_xlabel("Spike Windows (%)")
    ax1.set_title("Spike Frequency", fontweight="bold")
    ax1.invert_yaxis()
    ax1.grid(axis="x", alpha=0.3)

    # Panel 2: Number of spike bursts
    n_bursts = [r["metrics"]["n_bursts"] for r in valid]
    ax2.barh(y, n_bursts, color="#e67e22", edgecolor="white", alpha=0.8)
    for i, n in enumerate(n_bursts):
        ax2.text(n + 0.5, i, str(n), va="center", fontsize=8)
    ax2.set_yticks(y)
    ax2.set_yticklabels(exps, fontsize=8)
    ax2.set_xlabel("Spike Bursts")
    ax2.set_title("Burst Count", fontweight="bold")
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)

    # Panel 3: Mean spike p999 vs calm p999
    spike_means = [r["metrics"]["spike_p999_mean"] for r in valid]
    calm_means = [r["metrics"]["calm_p999_mean"] for r in valid]
    w = 0.35
    ax3.barh(y - w/2, calm_means, w, label="Calm p999", color="#2ecc71", alpha=0.7)
    ax3.barh(y + w/2, spike_means, w, label="Spike p999", color="#e74c3c", alpha=0.7)
    ax3.set_yticks(y)
    ax3.set_yticklabels(exps, fontsize=8)
    ax3.set_xlabel("Mean p99.9 (ms)")
    ax3.set_title("Spike vs Calm p999", fontweight="bold")
    ax3.legend(fontsize=8)
    ax3.invert_yaxis()
    ax3.grid(axis="x", alpha=0.3)

    fig.suptitle("Spike Detection Summary — Blueprint §06.3", fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_spike_summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def main():
    print("=" * 60)
    print("SPIKE DETECTION ALGORITHM — Blueprint §06.3")
    print(f"Window={WINDOW_WIDTH_S}s  Step={WINDOW_STEP_S*1000}ms  "
          f"Spike={SPIKE_MULTIPLIER}× baseline  Calm={CALM_MULTIPLIER}× baseline")
    print("=" * 60)

    # Compute baseline p999
    baseline_data = load_per_request("e1-baseline")
    if baseline_data is None:
        print("ERROR: e1-baseline required"); sys.exit(1)
    _, baseline_lats = baseline_data
    baseline_p999 = float(np.percentile(baseline_lats[baseline_lats > 0], 99.9))
    print(f"Baseline p99.9: {baseline_p999:.2f}ms")
    print(f"Spike threshold: {baseline_p999 * SPIKE_MULTIPLIER:.2f}ms")
    print(f"Calm threshold:  {baseline_p999 * CALM_MULTIPLIER:.2f}ms\n")

    results = []
    for exp in TARGET_EXPERIMENTS:
        print(f"  {exp}...", end=" ")
        r = analyze_experiment(exp, baseline_p999)
        if r:
            m = r["metrics"]
            print(f"{r['n_windows']} windows, {r['n_spike']} spikes ({r['spike_pct']:.1f}%), "
                  f"{m['n_bursts']} bursts")
            results.append(r)
        else:
            print("SKIP")

    valid = [r for r in results if r is not None]
    print(f"\n{len(valid)} experiments analyzed\n")

    print("Writing results:")
    write_spike_csv(valid)

    print("\nGenerating plots:")
    plot_spike_timeseries(valid, baseline_p999)
    plot_spike_summary(valid)

    print(f"\n✓ Spike detection complete")


if __name__ == "__main__":
    main()
