// main.cpp — HFT Kernel Event Analyzer
// Real-time eBPF metrics analysis with live terminal dashboard
//
// Usage:
//   hft-analyzer [--metrics http://localhost:9090/metrics] [--interval 1]
//                [--csv output.csv] [--duration 120]

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

#include "bpf_map_reader.h"
#include "correlation_engine.h"
#include "csv_exporter.h"
#include "terminal_dashboard.h"

namespace {
std::atomic<bool> g_stop{false};
void signal_handler(int) { g_stop.store(true); }
}

struct Config {
    std::string metrics_url = "http://localhost:9090/metrics";
    int interval_s = 1;
    int duration_s = 0; // 0 = run forever
    std::string csv_output = "";
    bool no_dashboard = false;
};

Config parse_args(int argc, char* argv[]) {
    Config cfg;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&]() { return (i + 1 < argc) ? argv[++i] : ""; };
        if (arg == "--metrics") cfg.metrics_url = next();
        else if (arg == "--interval") cfg.interval_s = std::stoi(next());
        else if (arg == "--duration") cfg.duration_s = std::stoi(next());
        else if (arg == "--csv") cfg.csv_output = next();
        else if (arg == "--no-dashboard") cfg.no_dashboard = true;
        else if (arg == "--help") {
            std::cout << "hft-analyzer — Real-Time Kernel Event Analyzer (C++20)\n\n"
                      << "Usage: hft-analyzer [options]\n\n"
                      << "  --metrics URL    rqdelay Prometheus endpoint (default: http://localhost:9090/metrics)\n"
                      << "  --interval N     Poll interval in seconds (default: 1)\n"
                      << "  --duration N     Run for N seconds (0 = forever, default: 0)\n"
                      << "  --csv FILE       Export time-series to CSV\n"
                      << "  --no-dashboard   Disable live terminal dashboard\n"
                      << "  --help           Show this help\n\n"
                      << "HFT Features:\n"
                      << "  • Raw socket HTTP client (zero library deps)\n"
                      << "  • Lock-free histogram for live percentiles\n"
                      << "  • Spearman rank correlation (wakeup delay ↔ app latency)\n"
                      << "  • Real-time ANSI terminal dashboard with sparklines\n";
            std::exit(0);
        }
    }
    return cfg;
}

int main(int argc, char* argv[]) {
    Config cfg = parse_args(argc, argv);

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // Initialize components
    hft::BpfMapReader reader(cfg.metrics_url);
    hft::CorrelationEngine corr_engine(30, 200.0);
    hft::TerminalDashboard dashboard;

    std::unique_ptr<hft::CsvExporter> csv;
    if (!cfg.csv_output.empty()) {
        csv = std::make_unique<hft::CsvExporter>(cfg.csv_output);
        csv->write_header();
    }

    if (!reader.is_connected()) {
        std::cerr << "WARNING: Cannot connect to eBPF metrics at "
                  << cfg.metrics_url << "\n"
                  << "Make sure rqdelay is running. Continuing anyway...\n\n";
    }

    // Take initial snapshot for delta computation
    auto prev_snap = reader.snapshot();
    auto start_time = std::chrono::steady_clock::now();
    int elapsed_s = 0;

    // Main event loop
    while (!g_stop.load(std::memory_order_relaxed)) {
        std::this_thread::sleep_for(std::chrono::seconds(cfg.interval_s));
        elapsed_s += cfg.interval_s;

        if (cfg.duration_s > 0 && elapsed_s >= cfg.duration_s) break;

        // Take snapshot and compute delta
        auto snap = reader.snapshot();
        auto delta = hft::BpfMapReader::delta(prev_snap, snap);

        // Add to correlation engine
        hft::WindowSample ws{
            .timestamp_ns = snap.timestamp_ns,
            .wakeup_delay_p99_us = static_cast<double>(snap.wakeup_delay_p99_us),
            .softirq_time_ms = snap.total_softirq_time_ns / 1e6,
            .app_p99_ms = 0, // Would need app-level data source
            .tcp_retransmits = snap.tcp_retransmit_total,
        };
        corr_engine.add_sample(ws);
        auto corr = corr_engine.compute();

        // Render dashboard
        if (!cfg.no_dashboard) {
            dashboard.render(snap, delta, corr, elapsed_s);
        } else {
            // Simple text output
            std::cout << "[" << elapsed_s << "s] "
                      << "wakeup_p99=" << snap.wakeup_delay_p99_us << "µs "
                      << "softirq=" << snap.total_softirq_time_ns / 1000000 << "ms "
                      << "retransmit=" << snap.tcp_retransmit_total
                      << "\n";
        }

        // Export to CSV
        if (csv) {
            csv->write_row(static_cast<double>(elapsed_s), snap, corr);
        }

        prev_snap = snap;
    }

    if (csv) csv->close();

    std::cout << "\nhft-analyzer stopped.\n";
    if (!cfg.csv_output.empty()) {
        std::cout << "CSV exported: " << cfg.csv_output << "\n";
    }

    return 0;
}
