#!/usr/bin/env python3
"""
analysis/scripts/plot_results.py — Generate comparison plots from CSV
Usage: python3 analysis/scripts/plot_results.py
Requires: pip3 install matplotlib numpy
"""
import csv
import sys
import os
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np
except ImportError:
    print("Install dependencies: pip3 install matplotlib numpy")
    sys.exit(1)

PROJECT = Path(__file__).resolve().parent.parent.parent
CSV_PATH = PROJECT / 'data' / 'all_experiments_summary.csv'
PLOT_DIR = PROJECT / 'analysis' / 'plots'


def load_csv():
    if not CSV_PATH.exists():
        print(f"Run analyze_all.py first to generate {CSV_PATH}")
        sys.exit(1)
    experiments = defaultdict(list)
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            experiments[row['experiment']].append(row)
    return experiments


def plot_latency_bars(experiments):
    """Bar chart: p50 vs p99 across experiments."""
    names, p50s, p99s = [], [], []
    for exp in sorted(experiments.keys()):
        runs = experiments[exp]
        names.append(exp.replace('-', '\n', 1))
        p50s.append(np.mean([float(r['p50_ms']) for r in runs]))
        p99s.append(np.mean([float(r['p99_ms']) for r in runs]))

    fig, ax = plt.subplots(figsize=(16, 7))
    x = np.arange(len(names))
    w = 0.35
    ax.bar(x - w/2, p50s, w, label='p50', color='#2196F3', alpha=0.85)
    ax.bar(x + w/2, p99s, w, label='p99', color='#F44336', alpha=0.85)
    ax.set_ylabel('Latency (ms)', fontsize=12)
    ax.set_title('End-to-End Latency: p50 vs p99', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=7, ha='center')
    ax.legend(fontsize=12)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    out = PLOT_DIR / 'latency_comparison.png'
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"  → {out}")


def plot_p99_waterfall(experiments):
    """p99 inflation ratio vs baseline."""
    if 'e1-baseline' not in experiments:
        print("  SKIP waterfall: no e1-baseline")
        return
    base_p99 = np.mean([float(r['p99_ms']) for r in experiments['e1-baseline']])
    if base_p99 <= 0:
        return

    order = [
        'e1-baseline', 'e2-cross-node', 'e3a-cfs-tight', 'e3b-cfs-moderate',
        'e4-noisy-neighbor', 'e5-throttle-crossnode', 'e6-contention-crossnode',
        'e7-full-stress', 'e8-hostnetwork', 'e9-hostnet-stress-crossnode',
        'e10-throttle-contention', 'e13-cpu-pinning', 'e14-pinning-stress-crossnode',
        'e15-full-isolation'
    ]

    names, ratios = [], []
    for e in order:
        if e in experiments:
            names.append(e.split('-')[0].replace('e', 'E'))
            ratios.append(np.mean([float(r['p99_ms']) for r in experiments[e]]) / base_p99)

    fig, ax = plt.subplots(figsize=(14, 6))
    colors = ['#4CAF50' if r <= 1.5 else '#FF9800' if r <= 3.0 else '#F44336' for r in ratios]
    bars = ax.bar(names, ratios, color=colors, edgecolor='black', linewidth=0.5)
    ax.axhline(y=1.0, color='#2196F3', ls='--', lw=1.5, label='Baseline')
    ax.set_ylabel('p99 Ratio vs Baseline')
    ax.set_title('p99 Latency Inflation Factor', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)
    for bar, r in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.05,
                f'{r:.1f}×', ha='center', va='bottom', fontsize=9, fontweight='bold')
    plt.tight_layout()
    out = PLOT_DIR / 'p99_waterfall.png'
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"  → {out}")


def plot_mitigation(experiments):
    """Before/after mitigation comparison."""
    pairs = [
        ('e4-noisy-neighbor', 'e13-cpu-pinning', 'Contention\n→ Pinning'),
        ('e7-full-stress', 'e15-full-isolation', 'Full Stress\n→ Isolation'),
        ('e1-baseline', 'e8-hostnetwork', 'Pod Net\n→ hostNet'),
    ]
    found = []
    for bef, aft, label in pairs:
        if bef in experiments and aft in experiments:
            bp = np.mean([float(r['p99_ms']) for r in experiments[bef]])
            ap = np.mean([float(r['p99_ms']) for r in experiments[aft]])
            red = (1 - ap / bp) * 100 if bp > 0 else 0
            found.append((label, bp, ap, red))

    if not found:
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(found))
    w = 0.35
    labels = [f[0] for f in found]
    bvals = [f[1] for f in found]
    avals = [f[2] for f in found]

    ax.bar(x - w/2, bvals, w, label='Before', color='#F44336', alpha=0.85)
    ax.bar(x + w/2, avals, w, label='After', color='#4CAF50', alpha=0.85)
    for i, (_, _, _, red) in enumerate(found):
        ax.text(i, max(bvals[i], avals[i]) * 1.05,
                f'{red:+.0f}%', ha='center', fontsize=11, fontweight='bold',
                color='#4CAF50' if red > 0 else '#F44336')
    ax.set_ylabel('p99 Latency (ms)')
    ax.set_title('Mitigation Effectiveness', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.legend(fontsize=12)
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    out = PLOT_DIR / 'mitigation_comparison.png'
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"  → {out}")


def main():
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading CSV data...")
    experiments = load_csv()
    print(f"Found {len(experiments)} experiments\n")
    print("Generating plots:")
    plot_latency_bars(experiments)
    plot_p99_waterfall(experiments)
    plot_mitigation(experiments)
    print(f"\n✓ All plots saved to {PLOT_DIR}/")


if __name__ == '__main__':
    main()
