#include "stats_collector.h"
#include "timestamp.h"

#include <chrono>
#include <thread>

namespace hft {

StatsCollector::StatsCollector(std::vector<SampleRing*> rings,
                               std::atomic<bool>& stop_flag)
    : rings_(std::move(rings)), stop_(stop_flag) {}

void StatsCollector::start() {
    thread_ = std::thread([this] { run(); });
}

void StatsCollector::join() {
    if (thread_.joinable()) thread_.join();
}

void StatsCollector::run() {
    start_time_ns_ = timestamp_ns();

    // Batch drain buffer — avoid per-element overhead
    std::array<LatencySample, 4096> batch;

    while (!stop_.load(std::memory_order_relaxed)) {
        bool any_data = false;

        // Drain all rings
        for (auto* ring : rings_) {
            auto out_it = batch.begin();
            std::size_t count = ring->drain(out_it, batch.size());

            for (std::size_t i = 0; i < count; ++i) {
                const auto& sample = batch[i];
                histogram_.record(sample.latency_ns);
                total_samples_++;
                if (sample.ok) {
                    ok_samples_++;
                } else {
                    err_samples_++;
                }
            }

            if (count > 0) any_data = true;
        }

        if (!any_data) {
            // No data available — sleep briefly to avoid busy-spinning
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    }

    // Final drain after stop signal
    for (auto* ring : rings_) {
        auto out_it = batch.begin();
        std::size_t count;
        do {
            count = ring->drain(out_it, batch.size());
            for (std::size_t i = 0; i < count; ++i) {
                const auto& sample = batch[i];
                histogram_.record(sample.latency_ns);
                total_samples_++;
                if (sample.ok) ok_samples_++;
                else err_samples_++;
            }
        } while (count > 0);
    }

    end_time_ns_ = timestamp_ns();
}

CollectorStats StatsCollector::get_stats() const {
    CollectorStats s;
    s.total_samples = total_samples_;
    s.ok_samples = ok_samples_;
    s.err_samples = err_samples_;
    s.p50_ns = histogram_.p50();
    s.p90_ns = histogram_.p90();
    s.p95_ns = histogram_.p95();
    s.p99_ns = histogram_.p99();
    s.p999_ns = histogram_.p999();
    s.min_ns = histogram_.min_value();
    s.max_ns = histogram_.max_value();
    s.mean_ns = histogram_.mean();

    uint64_t duration_ns = end_time_ns_ - start_time_ns_;
    if (duration_ns > 0) {
        s.rps = static_cast<double>(total_samples_) * 1e9 /
                static_cast<double>(duration_ns);
    }
    return s;
}

} // namespace hft
