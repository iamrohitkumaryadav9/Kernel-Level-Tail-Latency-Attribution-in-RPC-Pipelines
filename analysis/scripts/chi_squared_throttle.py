#!/usr/bin/env python3
"""
Chi-Squared Test for Throttle Events — Blueprint §06.5
Tests whether CFS throttle indicators are more frequent during spike windows
vs calm windows using chi-squared contingency test.
Usage: python3 analysis/scripts/chi_squared_throttle.py
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

# Experiments where CFS throttling is active (per blueprint §03)
THROTTLE_EXPERIMENTS = [
    "e3a-cfs-tight", "e3b-cfs-moderate", "e5-throttle-crossnode",
    "e10-throttle-contention", "e7-full-stress",
]
CONTROL_EXPERIMENTS = [
    "e1-baseline", "e4-noisy-neighbor", "e13-cpu-pinning",
]

WINDOW_S = 0.1  # 100ms windows


def load_per_request(exp_name, run_idx=0):
    """Load per-request ghz data."""
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
        latencies.append(det["latency"] / 1e6)
    return np.array(timestamps), np.array(latencies)


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
                "softirq_count": float(row["softirq_count"]),
                "tcp_retransmit": float(row["tcp_retransmit"]),
            }
    return ebpf


def window_analysis(timestamps, latencies, baseline_p999):
    """Classify windows and detect throttle-like behavior."""
    valid = timestamps > 0
    ts, lats = timestamps[valid], latencies[valid]
    if len(ts) == 0:
        return None

    t0, t1 = ts.min(), ts.max()
    bins = np.arange(t0, t1 + WINDOW_S, WINDOW_S)
    bin_idx = np.digitize(ts, bins) - 1
    bin_idx = np.clip(bin_idx, 0, len(bins) - 2)

    spike_thresh = baseline_p999 * 2.0
    calm_thresh = baseline_p999 * 1.2

    spike_windows, calm_windows = 0, 0
    # Throttle indicators: high latency variance + high p99/p50 ratio within window
    throttle_in_spike, throttle_in_calm = 0, 0
    no_throttle_in_spike, no_throttle_in_calm = 0, 0

    for i in range(len(bins) - 1):
        mask = bin_idx == i
        w_lats = lats[mask]
        if len(w_lats) < 5:
            continue

        p999 = np.percentile(w_lats, 99.9) if len(w_lats) >= 100 else np.percentile(w_lats, 99)
        p50 = np.percentile(w_lats, 50)
        cv = np.std(w_lats) / np.mean(w_lats) if np.mean(w_lats) > 0 else 0

        # Throttle indicator: high p99/p50 ratio (>3×) AND high CV (>0.5)
        # This indicates bimodal latency distribution typical of CFS throttling
        has_throttle_signature = (p999 / p50 > 3.0) and (cv > 0.5) if p50 > 0 else False

        if p999 > spike_thresh:
            spike_windows += 1
            if has_throttle_signature:
                throttle_in_spike += 1
            else:
                no_throttle_in_spike += 1
        elif p999 < calm_thresh:
            calm_windows += 1
            if has_throttle_signature:
                throttle_in_calm += 1
            else:
                no_throttle_in_calm += 1

    return {
        "spike_windows": spike_windows,
        "calm_windows": calm_windows,
        "throttle_in_spike": throttle_in_spike,
        "no_throttle_in_spike": no_throttle_in_spike,
        "throttle_in_calm": throttle_in_calm,
        "no_throttle_in_calm": no_throttle_in_calm,
    }


def chi_squared_test(contingency):
    """
    2×2 chi-squared test of independence.
    Contingency table:
                     Throttle   No-Throttle
    Spike windows       a           b
    Calm windows        c           d
    """
    a = contingency["throttle_in_spike"]
    b = contingency["no_throttle_in_spike"]
    c = contingency["throttle_in_calm"]
    d = contingency["no_throttle_in_calm"]
    n = a + b + c + d

    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return 0, 1.0, 0

    # Expected values under independence
    row1 = a + b
    row2 = c + d
    col1 = a + c
    col2 = b + d

    E_a = row1 * col1 / n
    E_b = row1 * col2 / n
    E_c = row2 * col1 / n
    E_d = row2 * col2 / n

    # Chi-squared statistic (with Yates' correction for 2×2)
    chi2 = 0
    for obs, exp in [(a, E_a), (b, E_b), (c, E_c), (d, E_d)]:
        if exp > 0:
            chi2 += (abs(obs - exp) - 0.5) ** 2 / exp

    # p-value from chi-squared distribution with df=1
    # Using Wilson-Hilferty approximation
    if chi2 <= 0:
        p_val = 1.0
    else:
        z = (chi2 ** (1/3) - (1 - 2/9)) / math.sqrt(2/9)
        p_val = 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))
        p_val = max(0, min(1, p_val))

    # Cramér's V (effect size for chi-squared)
    cramers_v = math.sqrt(chi2 / n) if n > 0 else 0

    return chi2, p_val, cramers_v


def analyze_all(baseline_p999, ebpf_data):
    """Run chi-squared analysis across all target experiments."""
    all_exps = list(set(THROTTLE_EXPERIMENTS + CONTROL_EXPERIMENTS))
    results = []

    for exp in sorted(all_exps):
        data = load_per_request(exp)
        if data is None:
            continue

        contingency = window_analysis(data[0], data[1], baseline_p999)
        if contingency is None:
            continue

        chi2, p_val, cramers_v = chi_squared_test(contingency)
        is_throttle_exp = exp in THROTTLE_EXPERIMENTS

        results.append({
            "experiment": exp,
            "is_throttle_exp": is_throttle_exp,
            "spike_windows": contingency["spike_windows"],
            "calm_windows": contingency["calm_windows"],
            "throttle_in_spike": contingency["throttle_in_spike"],
            "no_throttle_in_spike": contingency["no_throttle_in_spike"],
            "throttle_in_calm": contingency["throttle_in_calm"],
            "no_throttle_in_calm": contingency["no_throttle_in_calm"],
            "chi2": round(chi2, 3),
            "p_value": round(p_val, 6),
            "cramers_v": round(cramers_v, 3),
            "significant": p_val < 0.05,
        })

    return results


def write_csv(results):
    """Write chi-squared results."""
    csv_path = STATS_DIR / "chi_squared_throttle.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys() if results else [])
        w.writeheader()
        w.writerows(results)
    print(f"  ✓ {csv_path} ({len(results)} experiments)")


def plot_contingency(results):
    """Visualize chi-squared contingency tables."""
    valid = [r for r in results if r["spike_windows"] > 0 or r["calm_windows"] > 0]
    if not valid:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Panel 1: Throttle signature rate in spike vs calm windows
    exps = [r["experiment"].replace("e", "E", 1) for r in valid]
    y = np.arange(len(exps))

    spike_throttle_rate = []
    calm_throttle_rate = []
    for r in valid:
        total_spike = r["throttle_in_spike"] + r["no_throttle_in_spike"]
        total_calm = r["throttle_in_calm"] + r["no_throttle_in_calm"]
        spike_throttle_rate.append(r["throttle_in_spike"] / total_spike * 100 if total_spike > 0 else 0)
        calm_throttle_rate.append(r["throttle_in_calm"] / total_calm * 100 if total_calm > 0 else 0)

    w = 0.35
    ax1.barh(y - w/2, calm_throttle_rate, w, label="Calm windows", color="#2ecc71", alpha=0.7)
    ax1.barh(y + w/2, spike_throttle_rate, w, label="Spike windows", color="#e74c3c", alpha=0.7)
    ax1.set_yticks(y)
    ax1.set_yticklabels(exps, fontsize=8)
    ax1.set_xlabel("% Windows with Throttle Signature")
    ax1.set_title("Throttle Signature: Spike vs Calm", fontweight="bold")
    ax1.legend(fontsize=8)
    ax1.invert_yaxis()
    ax1.grid(axis="x", alpha=0.3)

    # Panel 2: Chi-squared statistic with significance markers
    chi2s = [r["chi2"] for r in valid]
    colors = ["#e74c3c" if r["significant"] else "#95a5a6" for r in valid]
    ax2.barh(y, chi2s, color=colors, edgecolor="white", alpha=0.8)
    for i, r in enumerate(valid):
        marker = "★" if r["significant"] else ""
        ax2.text(r["chi2"] + max(chi2s) * 0.02, i,
                 f'χ²={r["chi2"]:.1f} {marker} (V={r["cramers_v"]:.2f})',
                 va="center", fontsize=8)
    ax2.set_yticks(y)
    ax2.set_yticklabels(exps, fontsize=8)
    ax2.set_xlabel("χ² Statistic (df=1)")
    ax2.set_title("Chi-Squared Test (★ = significant at p<0.05)", fontweight="bold")
    ax2.axvline(3.841, color="gray", linestyle=":", alpha=0.5, label="p=0.05 critical (3.841)")
    ax2.legend(fontsize=7)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)

    fig.suptitle("Chi-Squared Test: Throttle Events × Spike Windows — Blueprint §06.5\n"
                 "H₀: Throttle signature and spike occurrence are independent",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    path = PLOT_DIR / "fig_chi_squared_throttle.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  → {path}")


def main():
    print("=" * 60)
    print("CHI-SQUARED TEST FOR THROTTLE EVENTS — Blueprint §06.5")
    print("=" * 60)

    ebpf_data = load_ebpf_data()

    # Baseline p999
    baseline_data = load_per_request("e1-baseline")
    if baseline_data is None:
        print("ERROR: e1-baseline required"); sys.exit(1)
    _, bl = baseline_data
    baseline_p999 = float(np.percentile(bl[bl > 0], 99.9))
    print(f"Baseline p99.9: {baseline_p999:.2f}ms\n")

    results = analyze_all(baseline_p999, ebpf_data)

    if not results:
        print("ERROR: No results"); sys.exit(1)

    # Print contingency tables
    print(f"\n{'Experiment':<30} {'Spike':>6} {'Calm':>6} {'Thr/Spike':>10} {'Thr/Calm':>10} "
          f"{'χ²':>8} {'p-val':>8} {'V':>6} {'Sig':>4}")
    print("─" * 100)
    for r in results:
        sig = "★" if r["significant"] else ""
        print(f"{r['experiment']:<30} {r['spike_windows']:>6} {r['calm_windows']:>6} "
              f"{r['throttle_in_spike']:>10} {r['throttle_in_calm']:>10} "
              f"{r['chi2']:>8.2f} {r['p_value']:>8.4f} {r['cramers_v']:>6.3f} {sig:>4}")

    print("\nWriting results:")
    write_csv(results)

    print("\nGenerating plots:")
    plot_contingency(results)

    print(f"\n✓ Chi-squared throttle analysis complete")


if __name__ == "__main__":
    main()
