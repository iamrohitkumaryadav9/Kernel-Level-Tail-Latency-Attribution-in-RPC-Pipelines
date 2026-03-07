// main.cpp — HFT Low-Latency gRPC Load Generator
// Demonstrates: RDTSC timestamping, lock-free SPSC ring, HDR histogram
//
// Usage:
//   hft-loadgen --target localhost:50051 --rate 2000 --duration 120
//               --workers 4 --output results.json
//
// Compatible with existing analyze_all.py analysis pipeline.

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "grpc_worker.h"
#include "json_output.h"
#include "stats_collector.h"
#include "timestamp.h"
#include "hdr_histogram.h"

namespace {
std::atomic<bool> g_stop{false};
void signal_handler(int) { g_stop.store(true, std::memory_order_relaxed); }
}

struct Config {
    std::string target    = "localhost:50051";
    uint32_t    rate      = 2000;     // Total target RPS
    uint32_t    duration  = 120;      // Seconds
    uint32_t    warmup    = 30;       // Warmup seconds (data discarded)
    uint32_t    workers   = 4;        // Number of worker threads
    std::string output    = "";       // Output JSON file
    std::string symbol    = "AAPL";
    double      price     = 150.25;
    int64_t     quantity  = 100;
    bool        verbose   = false;
};

Config parse_args(int argc, char* argv[]) {
    Config cfg;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 < argc) return argv[++i];
            std::cerr << "Missing value for " << arg << "\n";
            std::exit(1);
            return "";
        };

        if (arg == "--target" || arg == "-t") cfg.target = next();
        else if (arg == "--rate" || arg == "-r") cfg.rate = std::stoi(next());
        else if (arg == "--duration" || arg == "-d") cfg.duration = std::stoi(next());
        else if (arg == "--warmup" || arg == "-w") cfg.warmup = std::stoi(next());
        else if (arg == "--workers" || arg == "-n") cfg.workers = std::stoi(next());
        else if (arg == "--output" || arg == "-o") cfg.output = next();
        else if (arg == "--symbol") cfg.symbol = next();
        else if (arg == "--price") cfg.price = std::stod(next());
        else if (arg == "--quantity") cfg.quantity = std::stoll(next());
        else if (arg == "--verbose" || arg == "-v") cfg.verbose = true;
        else if (arg == "--help" || arg == "-h") {
            std::cout << "hft-loadgen — HFT-grade gRPC load generator (C++20)\n\n"
                      << "Usage: hft-loadgen [options]\n\n"
                      << "Options:\n"
                      << "  --target, -t    gRPC target (default: localhost:50051)\n"
                      << "  --rate, -r      Target RPS (default: 2000)\n"
                      << "  --duration, -d  Measurement duration in seconds (default: 120)\n"
                      << "  --warmup, -w    Warmup duration in seconds (default: 30)\n"
                      << "  --workers, -n   Number of worker threads (default: 4)\n"
                      << "  --output, -o    Output JSON file\n"
                      << "  --symbol        Order symbol (default: AAPL)\n"
                      << "  --price         Order price (default: 150.25)\n"
                      << "  --quantity      Order quantity (default: 100)\n"
                      << "  --verbose, -v   Verbose output\n"
                      << "  --help, -h      Show this help\n\n"
                      << "HFT Features:\n"
                      << "  • RDTSC nanosecond timestamping (~3ns overhead)\n"
                      << "  • Lock-free SPSC ring buffers (zero contention)\n"
                      << "  • HDR histogram (zero allocation, O(1) record)\n"
                      << "  • Cache-line padded data structures\n"
                      << "  • ghz-compatible JSON output\n";
            std::exit(0);
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            std::exit(1);
        }
    }
    return cfg;
}

void print_banner(const Config& cfg) {
    const auto& cal = hft::get_tsc_calibrator();
    std::cout << "\n"
              << "╔══════════════════════════════════════════════════════════╗\n"
              << "║  hft-loadgen — HFT-grade gRPC Load Generator (C++20)   ║\n"
              << "╠══════════════════════════════════════════════════════════╣\n"
              << "║  Target:     " << cfg.target << std::string(43 - cfg.target.size(), ' ') << "║\n"
              << "║  Rate:       " << cfg.rate << " req/s" << std::string(37 - std::to_string(cfg.rate).size(), ' ') << "║\n"
              << "║  Duration:   " << cfg.duration << "s (+" << cfg.warmup << "s warmup)" << std::string(32 - std::to_string(cfg.duration).size() - std::to_string(cfg.warmup).size(), ' ') << "║\n"
              << "║  Workers:    " << cfg.workers << std::string(43 - std::to_string(cfg.workers).size(), ' ') << "║\n"
              << "║  Timestamp:  " << (cal.is_reliable() ? "RDTSC" : "clock_gettime")
              << " (" << std::fixed << std::setprecision(2) << cal.tsc_freq_ghz() << " GHz)"
              << std::string(cal.is_reliable() ? 26 : 19, ' ')  << "║\n"
              << "║  Ring:       SPSC lock-free (64K entries/worker)        ║\n"
              << "║  Histogram:  HDR zero-allocation (384 buckets)          ║\n"
              << "╚══════════════════════════════════════════════════════════╝\n\n";
}

void print_live_stats(const hft::CollectorStats& stats, int elapsed_s) {
    std::cout << "\r  [" << elapsed_s << "s] "
              << "p50=" << stats.p50_ns / 1'000'000.0 << "ms "
              << "p99=" << stats.p99_ns / 1'000'000.0 << "ms "
              << "p999=" << stats.p999_ns / 1'000'000.0 << "ms "
              << "rps=" << static_cast<int>(stats.rps)
              << "   " << std::flush;
}

int main(int argc, char* argv[]) {
    Config cfg = parse_args(argc, argv);
    print_banner(cfg);

    // Install signal handler
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // Calculate per-worker RPS
    uint32_t per_worker_rps = cfg.rate / cfg.workers;

    // Create SPSC ring buffers (one per worker)
    std::vector<std::unique_ptr<hft::SampleRing>> rings;
    for (uint32_t i = 0; i < cfg.workers; ++i) {
        rings.push_back(std::make_unique<hft::SampleRing>());
    }

    // ── Phase 1: Warmup ──────────────────────────────
    if (cfg.warmup > 0) {
        std::cout << "  [warmup] Running " << cfg.warmup << "s warmup...\n";

        std::atomic<bool> warmup_stop{false};
        std::vector<std::unique_ptr<hft::SampleRing>> warmup_rings;
        std::vector<std::unique_ptr<hft::GrpcWorker>> warmup_workers;

        for (uint32_t i = 0; i < cfg.workers; ++i) {
            warmup_rings.push_back(std::make_unique<hft::SampleRing>());
            hft::WorkerConfig wcfg{
                .target = cfg.target,
                .worker_id = i,
                .target_rps = per_worker_rps,
                .symbol = cfg.symbol,
                .price = cfg.price,
                .quantity = cfg.quantity,
            };
            warmup_workers.push_back(
                std::make_unique<hft::GrpcWorker>(wcfg, *warmup_rings[i], warmup_stop));
        }

        for (auto& w : warmup_workers) w->start();
        std::this_thread::sleep_for(std::chrono::seconds(cfg.warmup));
        warmup_stop.store(true);
        for (auto& w : warmup_workers) w->join();

        uint64_t warmup_total = 0;
        for (auto& w : warmup_workers) warmup_total += w->requests_sent();
        std::cout << "  [warmup] Done (" << warmup_total << " requests)\n\n";
    }

    // ── Phase 2: Measurement ─────────────────────────
    std::cout << "  [measure] Starting " << cfg.duration << "s measurement at "
              << cfg.rate << " req/s...\n";

    // Create workers
    std::vector<std::unique_ptr<hft::GrpcWorker>> workers;
    for (uint32_t i = 0; i < cfg.workers; ++i) {
        hft::WorkerConfig wcfg{
            .target = cfg.target,
            .worker_id = i,
            .target_rps = per_worker_rps,
            .symbol = cfg.symbol,
            .price = cfg.price,
            .quantity = cfg.quantity,
        };
        workers.push_back(
            std::make_unique<hft::GrpcWorker>(wcfg, *rings[i], g_stop));
    }

    // Create stats collector
    std::vector<hft::SampleRing*> ring_ptrs;
    for (auto& r : rings) ring_ptrs.push_back(r.get());
    hft::StatsCollector collector(ring_ptrs, g_stop);

    // Start everything
    collector.start();
    for (auto& w : workers) w->start();

    // Wait for duration (with periodic stats)
    auto start = std::chrono::steady_clock::now();
    while (!g_stop.load(std::memory_order_relaxed)) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::steady_clock::now() - start).count();

        if (elapsed >= cfg.duration) {
            g_stop.store(true);
            break;
        }

        if (cfg.verbose) {
            auto stats = collector.get_stats();
            print_live_stats(stats, static_cast<int>(elapsed));
        }
    }

    // Stop and join
    g_stop.store(true);
    for (auto& w : workers) w->join();
    collector.join();

    // ── Results ──────────────────────────────────────
    auto stats = collector.get_stats();

    std::cout << "\n\n"
              << "  ┌─────────────────────────────────────────────\n"
              << "  │ Results\n"
              << "  ├─────────────────────────────────────────────\n"
              << "  │ Total requests:  " << stats.total_samples << "\n"
              << "  │ OK:              " << stats.ok_samples << "\n"
              << "  │ Errors:          " << stats.err_samples << "\n"
              << "  │ RPS:             " << std::fixed << std::setprecision(1) << stats.rps << "\n"
              << "  │ Mean:            " << std::setprecision(2) << stats.mean_ns / 1e6 << " ms\n"
              << "  │ p50:             " << stats.p50_ns / 1e6 << " ms\n"
              << "  │ p90:             " << stats.p90_ns / 1e6 << " ms\n"
              << "  │ p99:             " << stats.p99_ns / 1e6 << " ms\n"
              << "  │ p99.9:           " << stats.p999_ns / 1e6 << " ms\n"
              << "  │ Min:             " << stats.min_ns / 1e6 << " ms\n"
              << "  │ Max:             " << stats.max_ns / 1e6 << " ms\n"
              << "  └─────────────────────────────────────────────\n";

    // Worker stats
    uint64_t total_drops = 0;
    for (auto& w : workers) total_drops += w->ring_drops();
    if (total_drops > 0) {
        std::cout << "  ⚠ Ring buffer drops: " << total_drops
                  << " (increase ring size or reduce rate)\n";
    }

    // Write JSON output
    if (!cfg.output.empty()) {
        hft::write_json_output(cfg.output, stats, collector.histogram(),
                               collector.start_time_ns(),
                               collector.end_time_ns(),
                               cfg.rate, cfg.workers);
    }

    return 0;
}
