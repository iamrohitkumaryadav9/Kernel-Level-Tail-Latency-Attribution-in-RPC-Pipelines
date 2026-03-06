#!/usr/bin/env python3
"""
analysis/scripts/analyze_all.py — Unified analysis pipeline
Handles large ghz JSON files by extracting only summary fields.
Usage: python3 analysis/scripts/analyze_all.py
"""
import json
import csv
import os
import sys
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')
OUTPUT_CSV = os.path.join(DATA_DIR, 'all_experiments_summary.csv')


def extract_summary(filepath):
    """Extract only summary-level fields from ghz JSON (skip details array)."""
    try:
        with open(filepath) as f:
            d = json.load(f)
    except Exception as e:
        print(f"  SKIP {filepath}: {e}", file=sys.stderr)
        return None

    lat_dist = {}
    for p in (d.get('latencyDistribution') or []):
        pct = p.get('percentage', 0)
        lat_dist[pct] = p.get('latency', 0)

    scd = d.get('statusCodeDistribution') or {}
    total = d.get('count', 0)
    ok = scd.get('OK', 0)

    return {
        'count': total,
        'rps': d.get('rps', 0),
        'avg_ns': d.get('average', 0),
        'fastest_ns': d.get('fastest', 0),
        'slowest_ns': d.get('slowest', 0),
        'p10_ns': lat_dist.get(10, 0),
        'p25_ns': lat_dist.get(25, 0),
        'p50_ns': lat_dist.get(50, 0),
        'p75_ns': lat_dist.get(75, 0),
        'p90_ns': lat_dist.get(90, 0),
        'p95_ns': lat_dist.get(95, 0),
        'p99_ns': lat_dist.get(99, 0),
        'p999_ns': lat_dist.get(99.9, 0),
        'ok': ok,
        'errors': total - ok,
        'error_pct': round((total - ok) / total * 100, 2) if total > 0 else 0,
    }


def load_all(data_dir):
    experiments = defaultdict(list)
    for exp in sorted(os.listdir(data_dir)):
        exp_path = os.path.join(data_dir, exp)
        if not os.path.isdir(exp_path):
            continue
        for fname in sorted(os.listdir(exp_path)):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(exp_path, fname)
            result = extract_summary(fpath)
            if result:
                result['file'] = fname
                experiments[exp].append(result)
    return experiments


def write_csv(experiments, output_path):
    rows = []
    for exp in sorted(experiments.keys()):
        for i, r in enumerate(experiments[exp], 1):
            row = {'experiment': exp, 'run': i}
            row['p50_ms'] = round(r['p50_ns'] / 1e6, 3)
            row['p90_ms'] = round(r['p90_ns'] / 1e6, 3)
            row['p99_ms'] = round(r['p99_ns'] / 1e6, 3)
            # p999: use latencyDistribution p99.9 if available, else slowest
            p999_ns = r.get('p999_ns', 0)
            if p999_ns == 0:
                p999_ns = r.get('slowest_ns', r['p99_ns'])  # fallback to slowest
            row['p999_ms'] = round(p999_ns / 1e6, 3)
            row['avg_ms'] = round(r['avg_ns'] / 1e6, 3)
            row['rps'] = round(r['rps'], 1)
            row['count'] = r['count']
            row['ok'] = r['ok']
            row['errors'] = r['errors']
            row['error_pct'] = r['error_pct']
            row['file'] = r['file']
            rows.append(row)

    if rows:
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"✓ CSV written: {output_path} ({len(rows)} rows)")
    return rows


def print_summary_table(experiments):
    print(f"\n{'Experiment':<35} {'Runs':>4} {'p50(ms)':>10} {'p99(ms)':>10} {'RPS':>8} {'Err%':>6}")
    print("─" * 75)
    for exp in sorted(experiments.keys()):
        runs = experiments[exp]
        n = len(runs)
        p50 = sum(r['p50_ns'] for r in runs) / n / 1e6
        p99 = sum(r['p99_ns'] for r in runs) / n / 1e6
        rps = sum(r['rps'] for r in runs) / n
        err = sum(r['error_pct'] for r in runs) / n
        print(f"{exp:<35} {n:>4} {p50:>10.2f} {p99:>10.2f} {rps:>8.0f} {err:>6.1f}")


def validate(experiments):
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)
    base = experiments.get('e1-baseline')
    if not base:
        print("⚠ No e1-baseline data found")
        return

    bp99 = sum(r['p99_ns'] for r in base) / len(base)
    bp50 = sum(r['p50_ns'] for r in base) / len(base)
    print(f"Baseline: p50={bp50/1e6:.2f}ms  p99={bp99/1e6:.2f}ms")

    checks = [
        ('e3a-cfs-tight', 'CFS throttling', 'H2'),
        ('e4-noisy-neighbor', 'CPU contention', 'H1'),
        ('e7-full-stress', 'Full stress', 'H1+H2'),
        ('e13-cpu-pinning', 'CPU pinning mitigation', 'H3'),
        ('e15-full-isolation', 'Full isolation', 'H3'),
        ('e2-cross-node', 'Cross-node', 'Network'),
        ('e8-hostnetwork', 'hostNetwork', 'M4'),
    ]

    for ename, label, hyp in checks:
        if ename not in experiments:
            continue
        runs = experiments[ename]
        ep99 = sum(r['p99_ns'] for r in runs) / len(runs)
        ratio = ep99 / bp99 if bp99 > 0 else 0
        direction = "↑" if ratio > 1.1 else "↓" if ratio < 0.9 else "≈"
        print(f"  {ename:<35} p99={ep99/1e6:>8.2f}ms  {ratio:.2f}× baseline  {direction}  ({hyp})")

    # Mitigation effectiveness
    print("\nMitigation Effectiveness:")
    pairs = [
        ('e4-noisy-neighbor', 'e13-cpu-pinning', 'Contention → Pinning'),
        ('e7-full-stress', 'e15-full-isolation', 'Full Stress → Isolation'),
    ]
    for before, after, label in pairs:
        if before in experiments and after in experiments:
            bp = sum(r['p99_ns'] for r in experiments[before]) / len(experiments[before])
            ap = sum(r['p99_ns'] for r in experiments[after]) / len(experiments[after])
            red = (1 - ap / bp) * 100 if bp > 0 else 0
            print(f"  {label:<35} {red:>+.0f}% p99 reduction")


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else DATA_DIR
    print("Loading experiment data...")
    experiments = load_all(data_dir)
    total_runs = sum(len(v) for v in experiments.values())
    print(f"Found {total_runs} runs across {len(experiments)} experiments")

    print_summary_table(experiments)
    write_csv(experiments, OUTPUT_CSV)
    validate(experiments)
    print("\n✓ Analysis complete")


if __name__ == '__main__':
    main()
