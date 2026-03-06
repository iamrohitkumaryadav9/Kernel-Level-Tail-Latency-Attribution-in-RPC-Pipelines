#!/usr/bin/env python3
"""
analysis/scripts/parse_ghz.py — Parse ghz JSON output into structured CSV
Blueprint §6: Data parsing pipeline

Usage: python3 analysis/scripts/parse_ghz.py [data_dir]
       Default data_dir: ./data
"""
import json
import csv
import sys
import os
from pathlib import Path

def parse_ghz_file(filepath):
    """Parse a single ghz JSON output file."""
    with open(filepath) as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError:
            print(f"  WARNING: Could not parse {filepath}", file=sys.stderr)
            return None

    # Extract latency distribution
    lat_dist = {}
    for p in (d.get('latencyDistribution') or []):
        pct = p.get('percentage', 0)
        lat_ns = p.get('latency', 0)
        lat_dist[f'p{pct}'] = lat_ns

    # Status code distribution
    scd = d.get('statusCodeDistribution') or {}
    ok_count = scd.get('OK', 0)
    total = d.get('count', 0)
    error_count = total - ok_count

    return {
        'count': total,
        'rps': d.get('rps', 0),
        'average_ns': d.get('average', 0),
        'fastest_ns': d.get('fastest', 0),
        'slowest_ns': d.get('slowest', 0),
        'p10_ns': lat_dist.get('p10', 0),
        'p25_ns': lat_dist.get('p25', 0),
        'p50_ns': lat_dist.get('p50', 0),
        'p75_ns': lat_dist.get('p75', 0),
        'p90_ns': lat_dist.get('p90', 0),
        'p95_ns': lat_dist.get('p95', 0),
        'p99_ns': lat_dist.get('p99', 0),
        'ok_count': ok_count,
        'error_count': error_count,
    }


def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('data')
    output_csv = data_dir / 'all_experiments_summary.csv'

    rows = []
    for exp_dir in sorted(data_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        experiment = exp_dir.name

        for json_file in sorted(exp_dir.glob('*.json')):
            result = parse_ghz_file(json_file)
            if result is None:
                continue

            # Extract run number from filename
            fname = json_file.stem
            run_num = fname.split('run')[-1] if 'run' in fname else '1'

            row = {'experiment': experiment, 'run': run_num, 'file': json_file.name}
            row.update(result)
            rows.append(row)

    if not rows:
        print("No ghz JSON files found!", file=sys.stderr)
        sys.exit(1)

    # Write CSV
    fields = list(rows[0].keys())
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"✓ Parsed {len(rows)} runs from {len(set(r['experiment'] for r in rows))} experiments")
    print(f"  → {output_csv}")

    # Print summary table
    print(f"\n{'Experiment':<35} {'Runs':>4} {'Avg p50 (ms)':>12} {'Avg p99 (ms)':>12} {'Avg RPS':>8} {'Errors':>7}")
    print("─" * 85)

    from collections import defaultdict
    by_exp = defaultdict(list)
    for r in rows:
        by_exp[r['experiment']].append(r)

    for exp in sorted(by_exp.keys()):
        runs = by_exp[exp]
        n = len(runs)
        avg_p50 = sum(r['p50_ns'] for r in runs) / n / 1e6
        avg_p99 = sum(r['p99_ns'] for r in runs) / n / 1e6
        avg_rps = sum(r['rps'] for r in runs) / n
        total_err = sum(r['error_count'] for r in runs)
        print(f"{exp:<35} {n:>4} {avg_p50:>12.2f} {avg_p99:>12.2f} {avg_rps:>8.0f} {total_err:>7}")


if __name__ == '__main__':
    main()
