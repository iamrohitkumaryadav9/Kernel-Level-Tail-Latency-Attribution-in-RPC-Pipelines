#pragma once
// hdr_histogram.h — Zero-allocation HDR (High Dynamic Range) Histogram
//
// HFT design principles:
//   - Fixed memory footprint: all buckets preallocated at construction
//   - O(1) record, O(bucket_count) percentile query
//   - No heap allocation after construction
//   - Logarithmic bucketing with linear sub-buckets for precision
//   - Thread-safe recording via atomic increments (lock-free)
//   - Supports nanosecond-scale measurements up to ~30 seconds

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace hft {

// Simple log2-linear histogram: 64 log2 buckets, each with 8 sub-buckets
// Total: 512 counters × 8 bytes = 4KB (fits in L1 cache)
// Range: 1ns to 2^63 ns (~292 years) — more than enough
//
// Bucket i covers [2^i, 2^(i+1)) nanoseconds
// Sub-bucket j within bucket i covers [2^i + j*(2^i/8), 2^i + (j+1)*(2^i/8))
class HdrHistogram {
public:
    static constexpr int LOG2_BUCKETS = 40;     // Cover up to 2^40 ns ≈ 1099 seconds
    static constexpr int SUB_BUCKETS = 8;       // 8 sub-buckets per log2 bucket
    static constexpr int TOTAL_BUCKETS = LOG2_BUCKETS * SUB_BUCKETS + SUB_BUCKETS;
    // Extra SUB_BUCKETS for bucket 0 (values 0-7)

    HdrHistogram() noexcept { reset(); }

    // Record a value (nanoseconds). Lock-free via atomic increment.
    void record(uint64_t value_ns) noexcept {
        int idx = value_to_index(value_ns);
        counts_[idx].fetch_add(1, std::memory_order_relaxed);
        total_count_.fetch_add(1, std::memory_order_relaxed);
        // Update min/max
        uint64_t cur_min = min_.load(std::memory_order_relaxed);
        while (value_ns < cur_min &&
               !min_.compare_exchange_weak(cur_min, value_ns,
                                           std::memory_order_relaxed)) {}
        uint64_t cur_max = max_.load(std::memory_order_relaxed);
        while (value_ns > cur_max &&
               !max_.compare_exchange_weak(cur_max, value_ns,
                                           std::memory_order_relaxed)) {}
        // Accumulate for mean
        sum_.fetch_add(value_ns, std::memory_order_relaxed);
    }

    // Query percentile (0.0 to 1.0). NOT thread-safe with concurrent record().
    // Call after stopping measurement.
    [[nodiscard]] uint64_t percentile(double p) const noexcept {
        uint64_t total = total_count_.load(std::memory_order_relaxed);
        if (total == 0) return 0;

        uint64_t target = static_cast<uint64_t>(p * static_cast<double>(total));
        if (target == 0) target = 1;

        uint64_t cumulative = 0;
        for (int i = 0; i < TOTAL_BUCKETS; ++i) {
            cumulative += counts_[i].load(std::memory_order_relaxed);
            if (cumulative >= target) {
                return index_to_value(i);
            }
        }
        return max_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] uint64_t p50()  const noexcept { return percentile(0.50); }
    [[nodiscard]] uint64_t p90()  const noexcept { return percentile(0.90); }
    [[nodiscard]] uint64_t p95()  const noexcept { return percentile(0.95); }
    [[nodiscard]] uint64_t p99()  const noexcept { return percentile(0.99); }
    [[nodiscard]] uint64_t p999() const noexcept { return percentile(0.999); }

    [[nodiscard]] uint64_t count() const noexcept {
        return total_count_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] uint64_t min_value() const noexcept {
        return min_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] uint64_t max_value() const noexcept {
        return max_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] double mean() const noexcept {
        uint64_t c = count();
        if (c == 0) return 0.0;
        return static_cast<double>(sum_.load(std::memory_order_relaxed)) /
               static_cast<double>(c);
    }

    void reset() noexcept {
        for (auto& c : counts_) c.store(0, std::memory_order_relaxed);
        total_count_.store(0, std::memory_order_relaxed);
        min_.store(UINT64_MAX, std::memory_order_relaxed);
        max_.store(0, std::memory_order_relaxed);
        sum_.store(0, std::memory_order_relaxed);
    }

    // Get the latency distribution as an array of {percentage, value_ns} pairs
    // Compatible with ghz JSON format
    struct LatencyPoint {
        double percentage;
        uint64_t latency_ns;
    };

    [[nodiscard]] std::array<LatencyPoint, 9> distribution() const noexcept {
        return {{
            {10.0,  percentile(0.10)},
            {25.0,  percentile(0.25)},
            {50.0,  percentile(0.50)},
            {75.0,  percentile(0.75)},
            {90.0,  percentile(0.90)},
            {95.0,  percentile(0.95)},
            {99.0,  percentile(0.99)},
            {99.9,  percentile(0.999)},
            {99.99, percentile(0.9999)},
        }};
    }

private:
    // Map value to bucket index
    [[nodiscard]] static int value_to_index(uint64_t value) noexcept {
        if (value < SUB_BUCKETS) return static_cast<int>(value);

        // Find log2 bucket
        int log2 = 63 - __builtin_clzll(value);
        if (log2 >= LOG2_BUCKETS) log2 = LOG2_BUCKETS - 1;

        // Sub-bucket within the log2 range
        int sub = static_cast<int>((value >> (log2 > 3 ? log2 - 3 : 0)) & 0x7);

        int idx = SUB_BUCKETS + (log2 * SUB_BUCKETS) + sub;
        if (idx >= TOTAL_BUCKETS) idx = TOTAL_BUCKETS - 1;
        return idx;
    }

    // Map bucket index back to representative value (midpoint)
    [[nodiscard]] static uint64_t index_to_value(int index) noexcept {
        if (index < SUB_BUCKETS) return static_cast<uint64_t>(index);

        int adjusted = index - SUB_BUCKETS;
        int log2 = adjusted / SUB_BUCKETS;
        int sub = adjusted % SUB_BUCKETS;

        uint64_t base = 1ULL << log2;
        uint64_t sub_size = base / SUB_BUCKETS;
        if (sub_size == 0) sub_size = 1;

        return base + sub * sub_size + sub_size / 2;
    }

    std::array<std::atomic<uint64_t>, TOTAL_BUCKETS> counts_;
    std::atomic<uint64_t> total_count_{0};
    std::atomic<uint64_t> min_{UINT64_MAX};
    std::atomic<uint64_t> max_{0};
    std::atomic<uint64_t> sum_{0};
};

} // namespace hft
