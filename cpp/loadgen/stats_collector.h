#pragma once
// stats_collector.h — Drains SPSC ring buffers and maintains HDR histogram
// Runs in its own thread, consuming latency samples from all workers

#include <atomic>
#include <cstdint>
#include <vector>

#include "hdr_histogram.h"
#include "spsc_ring.h"
#include "grpc_worker.h"

namespace hft {

struct CollectorStats {
    uint64_t total_samples = 0;
    uint64_t ok_samples = 0;
    uint64_t err_samples = 0;
    uint64_t p50_ns = 0;
    uint64_t p90_ns = 0;
    uint64_t p95_ns = 0;
    uint64_t p99_ns = 0;
    uint64_t p999_ns = 0;
    uint64_t min_ns = 0;
    uint64_t max_ns = 0;
    double   mean_ns = 0;
    double   rps = 0;
};

class StatsCollector {
public:
    // Takes references to all worker ring buffers
    explicit StatsCollector(std::vector<SampleRing*> rings,
                            std::atomic<bool>& stop_flag);

    void start();
    void join();

    // Get current stats snapshot (call after stop)
    CollectorStats get_stats() const;

    // Get the histogram reference (for JSON output)
    const HdrHistogram& histogram() const { return histogram_; }

    uint64_t start_time_ns() const { return start_time_ns_; }
    uint64_t end_time_ns() const { return end_time_ns_; }

private:
    void run();

    std::vector<SampleRing*> rings_;
    std::atomic<bool>& stop_;
    HdrHistogram histogram_;
    std::thread thread_;

    uint64_t total_samples_ = 0;
    uint64_t ok_samples_ = 0;
    uint64_t err_samples_ = 0;
    uint64_t start_time_ns_ = 0;
    uint64_t end_time_ns_ = 0;
};

} // namespace hft
