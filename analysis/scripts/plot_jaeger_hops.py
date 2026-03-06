#!/usr/bin/env python3
"""
Fig 10: Per-hop stacked latency decomposition from Jaeger traces — Blueprint §06.6
Fetches live traces from Jaeger API and generates stacked bar chart per experiment.
"""
import json, sys, urllib.request
from pathlib import Path
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("ERROR: pip3 install matplotlib")
    sys.exit(1)

JAEGER_URL = "http://localhost:16686"
PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"
PLOT_DIR.mkdir(exist_ok=True)

# Blueprint §2: service order in pipeline
HOP_ORDER = ["auth", "risk", "marketdata", "execution"]
HOP_COLORS = {
    "gateway":    "#2ecc71",
    "auth":       "#3498db",
    "risk":       "#e67e22",
    "marketdata": "#9b59b6",
    "execution":  "#e74c3c",
    "redis":      "#95a5a6",
}

def fetch_traces(service="gateway", limit=200):
    url = f"{JAEGER_URL}/api/traces?service={service}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r).get("data", [])
    except Exception as e:
        print(f"  [WARN] Jaeger fetch failed: {e}")
        return []

def extract_hop_latencies(traces):
    """Extract per-service median latency from spans."""
    service_durations = defaultdict(list)
    for trace in traces:
        # Build service -> spans map
        svc_spans = defaultdict(list)
        for span in trace.get("spans", []):
            op = span.get("operationName", "")
            dur_ms = span["duration"] / 1000  # us -> ms
            # Identify service from operation name
            for svc in ["gateway", "auth", "risk", "marketdata", "execution"]:
                if svc in op.lower():
                    svc_spans[svc].append(dur_ms)
                    break
            if "redis" in op.lower():
                svc_spans["redis"].append(dur_ms)
        # For each service, take the outermost span (max duration)
        for svc, durs in svc_spans.items():
            if durs:
                service_durations[svc].append(max(durs))
    return {svc: np.median(durs) for svc, durs in service_durations.items() if durs}

def main():
    print("Fetching Jaeger traces for baseline (E1)...")
    traces = fetch_traces(limit=200)
    if not traces:
        print("ERROR: No traces found. Ensure Jaeger port-forward is active on :16686")
        sys.exit(1)
    print(f"  Got {len(traces)} traces")

    baseline_hops = extract_hop_latencies(traces)
    print("Baseline per-hop latencies (median):")
    for svc, dur in sorted(baseline_hops.items(), key=lambda x: -x[1]):
        print(f"  {svc}: {dur:.3f}ms")

    # ── Fig 10a: Stacked bar chart of per-hop contribution ──
    fig, ax = plt.subplots(figsize=(10, 6))

    # Services in pipeline order (excluding gateway which is the sum)
    hops = [h for h in ["auth", "risk", "marketdata", "execution", "redis"] if h in baseline_hops]
    hop_labels = [h.capitalize() for h in hops]
    hop_vals = [baseline_hops[h] for h in hops]
    colors = [HOP_COLORS.get(h, "#95a5a6") for h in hops]

    x = [0]  # single experiment for now
    bottom = 0
    for hop, val, color in zip(hop_labels, hop_vals, colors):
        ax.bar(x, [val], bottom=bottom, color=color, label=hop, width=0.4, edgecolor="white")
        ax.text(0, bottom + val/2, f"{val:.2f}ms", ha="center", va="center",
                fontsize=10, fontweight="bold", color="white")
        bottom += val

    # Add gateway total line
    gw_total = baseline_hops.get("gateway", bottom)
    ax.axhline(gw_total, color="black", linestyle="--", alpha=0.5,
               label=f"E2E (gateway): {gw_total:.2f}ms")

    ax.set_xticks([0])
    ax.set_xticklabels(["E1 Baseline"])
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Fig 10: Per-Hop Latency Decomposition (E1 Baseline)\nJaeger trace data — median across 200 traces", fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    path = PLOT_DIR / "fig10b_per_hop_decomposition.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"\n  → {path}")

    # ── Fig 10b: CDF of per-service latency ──
    fig2, ax2 = plt.subplots(figsize=(10, 6))
    svc_all = defaultdict(list)
    for trace in traces:
        for span in trace.get("spans", []):
            op = span.get("operationName", "")
            dur_ms = span["duration"] / 1000
            for svc in ["gateway", "auth", "risk", "marketdata", "execution"]:
                if svc in op.lower():
                    svc_all[svc].append(dur_ms)
                    break

    for svc in ["gateway", "auth", "risk", "marketdata", "execution"]:
        if svc not in svc_all:
            continue
        vals = sorted(svc_all[svc])
        cdf = np.arange(1, len(vals)+1) / len(vals)
        ax2.plot(vals, cdf, label=svc.capitalize(), color=HOP_COLORS.get(svc, "gray"),
                 linewidth=2)

    ax2.set_xlabel("Span Duration (ms)", fontsize=12)
    ax2.set_ylabel("CDF", fontsize=12)
    ax2.set_title("Fig 10c: Per-Service Span Duration CDF (E1 Baseline)", fontsize=13, fontweight="bold")
    ax2.set_xlim(0, 5)
    ax2.axhline(0.99, color="gray", linestyle=":", alpha=0.5, label="p99")
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)
    fig2.tight_layout()
    path2 = PLOT_DIR / "fig10c_per_hop_cdf.png"
    fig2.savefig(path2, dpi=150)
    plt.close(fig2)
    print(f"  → {path2}")
    print("\n✓ Per-hop decomposition plots saved!")
    print("  Note: Run during E7 (full stress) experiment for stressed comparison.")

if __name__ == "__main__":
    main()
