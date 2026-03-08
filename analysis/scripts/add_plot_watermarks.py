#!/usr/bin/env python3
"""
Add Data Source Watermarks to Analysis Plots
============================================
Labels all plots that use derived/estimated kernel data with clear
"Model-derived estimates" watermarks. Plots using only real app-level
data are labeled "Measured data".

Usage: python3 analysis/scripts/add_plot_watermarks.py
"""
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import AnchoredText
    from PIL import Image
except ImportError:
    print("ERROR: pip3 install matplotlib pillow"); sys.exit(1)

PLOT_DIR = Path(__file__).resolve().parent.parent / "plots"

# Classification of plots by data source
DERIVED_DATA_PLOTS = [
    # Plots that use eBPF/kernel-derived data
    "fig2_ebpf_wakeup_vs_app_p99.png",
    "fig5_softirq_vs_wakeup.png",
    "fig9_retransmit_vs_p99.png",
    "fig_per_request_correlation.png",
    "fig_per_request_heatmap.png",
    "fig_per_signal_comparison.png",
    "fig_per_signal_heatmap.png",
    "fig_windowed_correlation.png",
    "fig_windowed_timeseries.png",
    "fig_case_control_kernel.png",
]

MEASURED_DATA_PLOTS = [
    # Plots using only real ghz app-level data
    "fig1_p99_bar_chart.png",
    "fig3_cdf_overlay.png",
    "fig6_crossnode_effect.png",
    "fig7_mitigation_waterfall.png",
    "fig8_burst_response.png",
    "fig10b_per_hop_decomposition.png",
    "fig10c_per_hop_cdf.png",
    "latency_comparison.png",
    "p99_waterfall.png",
    "mitigation_comparison.png",
    "fig_case_control_latency.png",
    "fig_case_control_temporal.png",
    "fig_spike_detection.png",
    "fig_spike_summary.png",
    "fig_spike_heatmap.png",
    "fig_chi_squared_throttle.png",
]


def add_watermark(image_path, text, color):
    """Add a watermark annotation to a plot image."""
    try:
        img = Image.open(image_path)
        fig, ax = plt.subplots(figsize=(img.width / 100, img.height / 100), dpi=100)
        ax.imshow(img)
        ax.axis("off")

        # Add watermark in bottom-right
        anchored = AnchoredText(
            text,
            loc="lower right",
            prop=dict(fontsize=8, color=color, alpha=0.8,
                      fontweight="bold", family="monospace"),
            frameon=True,
            pad=0.3,
            borderpad=0.5,
        )
        anchored.patch.set_boxstyle("round,pad=0.3")
        anchored.patch.set_facecolor("white")
        anchored.patch.set_alpha(0.85)
        anchored.patch.set_edgecolor(color)
        ax.add_artist(anchored)

        fig.savefig(image_path, dpi=100, bbox_inches="tight", pad_inches=0.02)
        plt.close(fig)
        return True
    except Exception as e:
        print(f"    WARN: {image_path.name}: {e}")
        return False


def main():
    print("=" * 60)
    print("ADD DATA SOURCE WATERMARKS TO PLOTS")
    print("=" * 60)

    derived_count = 0
    measured_count = 0

    # Label derived-data plots
    print("\nDerived kernel data plots (adding watermark):")
    for name in DERIVED_DATA_PLOTS:
        path = PLOT_DIR / name
        if path.exists():
            if add_watermark(path, "⚠ Kernel signals: model-derived estimates", "#CC6600"):
                print(f"  ✓ {name}")
                derived_count += 1
        else:
            print(f"  - {name} (not found, skip)")

    # Label measured-data plots
    print("\nMeasured app-level data plots (adding watermark):")
    for name in MEASURED_DATA_PLOTS:
        path = PLOT_DIR / name
        if path.exists():
            if add_watermark(path, "✓ Source: measured ghz data", "#006633"):
                print(f"  ✓ {name}")
                measured_count += 1
        else:
            print(f"  - {name} (not found, skip)")

    print(f"\n✓ Watermarked {derived_count} derived-data + {measured_count} measured-data plots")


if __name__ == "__main__":
    main()
