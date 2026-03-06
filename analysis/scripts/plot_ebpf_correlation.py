#!/usr/bin/env python3
"""
Figs 2, 5, 9: eBPF correlation plots — Blueprint §06.6
Uses DELTA softirq/retransmit between consecutive experiments
(since counters are cumulative from pod start).
"""
import csv, sys
from pathlib import Path
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: pip3 install matplotlib"); sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent.parent
EBPF_CSV = ROOT / "data" / "ebpf_per_experiment.csv"
APP_CSV  = ROOT / "data" / "all_experiments_summary.csv"
PLOT_DIR = ROOT / "analysis" / "plots"
PLOT_DIR.mkdir(exist_ok=True)

COLORS = {
    "baseline":   "#2ecc71",
    "throttling": "#e74c3c",
    "contention": "#e67e22",
    "crossnode":  "#3498db",
    "mitigation": "#9b59b6",
    "other":      "#95a5a6",
}
MARKERS = {
    "baseline": "o", "throttling": "s", "contention": "^",
    "crossnode": "D", "mitigation": "P", "other": "x",
}

def classify(exp):
    if "baseline" in exp or exp in ("e0-no-instrumentation","e8-hostnetwork"): return "baseline"
    if "cfs" in exp or "throttl" in exp or "resource" in exp: return "throttling"
    if "contention" in exp or "noisy" in exp or "full-stress" in exp or "memory" in exp: return "contention"
    if "crossnode" in exp or "cross" in exp: return "crossnode"
    if "isolation" in exp or "pinning" in exp: return "mitigation"
    return "other"

def load_app_p99():
    rows = list(csv.DictReader(open(APP_CSV)))
    exp_p99 = defaultdict(list)
    for r in rows:
        try: exp_p99[r["experiment"]].append(float(r["p99_ms"]))
        except: pass
    return {exp: np.mean(vals) for exp, vals in exp_p99.items()}

def load_ebpf_with_deltas():
    """Load eBPF CSV and compute per-experiment deltas for cumulative counters."""
    rows = list(csv.DictReader(open(EBPF_CSV)))
    data = []
    for i, r in enumerate(rows):
        prev = rows[i-1] if i > 0 else r
        sirq_delta   = max(0, float(r.get("softirq_count","0") or 0)  - float(prev.get("softirq_count","0") or 0))
        retrans_delta = max(0, float(r.get("tcp_retransmit","0") or 0) - float(prev.get("tcp_retransmit","0") or 0))
        data.append({
            "exp":          r["experiment"],
            "wakeup_p99":   float(r.get("rqdelay_p99_us","0") or 0),
            "sirq_delta":   sirq_delta,
            "retrans_delta":retrans_delta,
        })
    return data

def main():
    if not EBPF_CSV.exists():
        print(f"ERROR: {EBPF_CSV} not found. Run ./scripts/sample-ebpf-metrics.sh"); sys.exit(1)

    app_p99 = load_app_p99()
    ebpf    = load_ebpf_with_deltas()

    # Build joined dataset
    data = []
    for d in ebpf:
        exp = d["exp"]
        if exp not in app_p99: continue
        data.append({
            "exp":          exp,
            "label":        exp.replace("e","E").split("-")[0].upper(),
            "app_p99_ms":   app_p99[exp],
            "wakeup_p99":   d["wakeup_p99"],
            "sirq_delta":   d["sirq_delta"],
            "retrans_delta":d["retrans_delta"],
            "cat":          classify(exp),
        })
    print(f"Joined {len(data)} experiments")

    def scatter_plot(ax, x_vals, y_vals, labels, cats, xlabel, ylabel, title):
        for cat, color in COLORS.items():
            pts = [(x, y, l) for x, y, l, c in zip(x_vals, y_vals, labels, cats) if c == cat]
            if not pts:continue
            ax.scatter([p[0] for p in pts], [p[1] for p in pts],
                       c=color, s=90, marker=MARKERS[cat],
                       label=cat.capitalize(), zorder=5, edgecolors="white", linewidths=0.5)
            for x, y, l in pts:
                ax.annotate(l, (x, y), fontsize=7, alpha=0.75,
                            xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel(xlabel, fontsize=11); ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.legend(fontsize=8, loc="upper left"); ax.grid(True, alpha=0.3)

    x_vals  = [d["wakeup_p99"]   for d in data]
    y_vals  = [d["app_p99_ms"]   for d in data]
    sx_vals = [d["sirq_delta"]   for d in data]
    rx_vals = [d["retrans_delta"]for d in data]
    labels  = [d["label"]        for d in data]
    cats    = [d["cat"]          for d in data]

    # ── Fig 2: wakeup_delay_p99 vs app p99 ──
    fig2, ax2 = plt.subplots(figsize=(11, 7))
    scatter_plot(ax2, x_vals, y_vals, labels, cats,
                 "eBPF Wakeup Delay p99 (µs)",
                 "Application p99 (ms)",
                 "Fig 2: eBPF Wakeup Delay p99 vs Application p99\nBlueprint §06.6 — color by mechanism (note: CFS throttle ≠ wakeup delay in Kind)")
    # Add note about CFS
    ax2.text(0.02, 0.95,
             "Note: CFS throttling inflates app p99 without\n"
             "increasing eBPF wakeup_delay_p99 (different kernel mechanism)",
             transform=ax2.transAxes, fontsize=8, va="top",
             bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))
    fig2.tight_layout()
    p = PLOT_DIR / "fig2_ebpf_wakeup_vs_app_p99.png"
    fig2.savefig(p, dpi=150); plt.close(fig2); print(f"  → {p}")

    # ── Fig 5: delta softirq_count vs wakeup p99 ──
    fig5, ax5 = plt.subplots(figsize=(11, 7))
    scatter_plot(ax5, sx_vals, [d["wakeup_p99"] for d in data], labels, cats,
                 "softirq Events (delta per 20s window)",
                 "eBPF Wakeup Delay p99 (µs)",
                 "Fig 5: softirq Activity vs Wakeup Delay per Experiment\nBlueprint §06.6")
    fig5.tight_layout()
    p = PLOT_DIR / "fig5_softirq_vs_wakeup.png"
    fig5.savefig(p, dpi=150); plt.close(fig5); print(f"  → {p}")

    # ── Fig 9: delta retransmit vs app p99 ──
    fig9, ax9 = plt.subplots(figsize=(11, 7))
    scatter_plot(ax9, rx_vals, y_vals, labels, cats,
                 "TCP Retransmit Events (delta per 20s window)",
                 "Application p99 (ms)",
                 "Fig 9: TCP Retransmit Events vs Application p99\nBlueprint §06.6")
    fig9.tight_layout()
    p = PLOT_DIR / "fig9_retransmit_vs_p99.png"
    fig9.savefig(p, dpi=150); plt.close(fig9); print(f"  → {p}")

    print("\n✓ Figs 2, 5, 9 saved!")
    print(f"\nSummary:")
    print(f"  softirq delta range: {min(sx_vals):.0f} – {max(sx_vals):.0f}")
    print(f"  retransmit delta range: {min(rx_vals):.0f} – {max(rx_vals):.0f}")
    print(f"  app p99 range: {min(y_vals):.1f}ms – {max(y_vals):.1f}ms")

if __name__ == "__main__":
    main()
