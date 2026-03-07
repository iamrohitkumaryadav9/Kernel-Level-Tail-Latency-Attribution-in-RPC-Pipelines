#pragma once
// terminal_dashboard.h — ANSI terminal live dashboard for eBPF metrics

#include <cstdint>
#include <cstdio>
#include <string>
#include "bpf_map_reader.h"
#include "correlation_engine.h"

namespace hft {

class TerminalDashboard {
public:
    void render(const EbpfSnapshot& snap, const EbpfSnapshot& delta,
                const CorrelationResult& corr, int elapsed_s) {
        // Clear screen and move cursor to top
        printf("\033[2J\033[H");

        printf("╔══════════════════════════════════════════════════════════════╗\n");
        printf("║  hft-analyzer — Real-Time Kernel Event Analyzer (C++20)     ║\n");
        printf("║  Elapsed: %4ds  |  Poll interval: 1s                       ║\n", elapsed_s);
        printf("╠══════════════════════════════════════════════════════════════╣\n");

        // Wakeup delay section
        printf("║  ┌─ Wakeup-to-Run Delay ────────────────────────────────┐  ║\n");
        printf("║  │  p50: %5lu µs   p99: %5lu µs   events: %10lu │  ║\n",
               snap.wakeup_delay_p50_us, snap.wakeup_delay_p99_us,
               snap.wakeup_total_events);
        printf("║  │  Δevents/s: ~%lu                                      ",
               delta.wakeup_total_events);
        printf("│  ║\n");

        // Histogram sparkline (simplified)
        printf("║  │  Histogram: ");
        render_sparkline(snap.wakeup_delay_hist);
        printf("  │  ║\n");
        printf("║  └──────────────────────────────────────────────────────┘  ║\n");

        // Softirq section
        printf("║  ┌─ Softirq Interference ───────────────────────────────┐  ║\n");
        printf("║  │  NET_RX: %8.2f ms   NET_TX: %8.2f ms            │  ║\n",
               snap.softirq_time_ns[3] / 1e6, snap.softirq_time_ns[2] / 1e6);
        printf("║  │  Total:  %8.2f ms   Events: %10lu              │  ║\n",
               snap.total_softirq_time_ns / 1e6, snap.total_softirq_count);
        printf("║  └──────────────────────────────────────────────────────┘  ║\n");

        // TCP section
        printf("║  ┌─ TCP ────────────────────────────────────────────────┐  ║\n");
        printf("║  │  Retransmits: %10lu                               │  ║\n",
               snap.tcp_retransmit_total);
        printf("║  └──────────────────────────────────────────────────────┘  ║\n");

        // Correlation section
        printf("║  ┌─ Correlation (wakeup_delay ↔ app_p99) ──────────────┐  ║\n");
        printf("║  │  Spearman ρ:  %+.4f   Pearson r: %+.4f             │  ║\n",
               corr.spearman_rho, corr.pearson_r);
        printf("║  │  Window: %zu samples  Spikes: %lu  Calm: %lu         ",
               corr.window_size, corr.spike_count, corr.calm_count);
        printf("│  ║\n");
        printf("║  └──────────────────────────────────────────────────────┘  ║\n");

        printf("╚══════════════════════════════════════════════════════════════╝\n");
        printf("  Press Ctrl+C to stop\n");

        fflush(stdout);
    }

private:
    void render_sparkline(const std::array<uint64_t, 64>& hist) {
        // Show first 20 buckets as sparkline (0-19 = 1µs to ~1ms)
        const char* blocks[] = {"▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"};
        uint64_t max_val = 1;
        for (int i = 0; i < 20; ++i) {
            if (hist[i] > max_val) max_val = hist[i];
        }
        for (int i = 0; i < 20; ++i) {
            int level = static_cast<int>(hist[i] * 7 / max_val);
            if (level > 7) level = 7;
            printf("%s", blocks[level]);
        }
        printf(" (0-20 log₂µs)");
    }
};

} // namespace hft
