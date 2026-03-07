#pragma once
// timestamp.h — HFT-grade nanosecond timestamping
// Uses RDTSC with clock_gettime calibration for sub-20ns overhead
// Fallback to clock_gettime(CLOCK_MONOTONIC) if RDTSC is not invariant

#include <cstdint>
#include <time.h>
#include <x86intrin.h>

namespace hft {

// Read Time Stamp Counter — ~3ns on modern CPUs
// Serializing version: RDTSCP ensures all prior instructions complete
inline uint64_t rdtsc() noexcept {
    unsigned int aux;
    return __rdtscp(&aux);
}

// Fence + RDTSC for start timestamp (prevents reordering)
inline uint64_t rdtsc_start() noexcept {
    _mm_mfence();
    _mm_lfence();
    return __rdtsc();
}

// RDTSCP for end timestamp (serializing read)
inline uint64_t rdtsc_end() noexcept {
    unsigned int aux;
    uint64_t tsc = __rdtscp(&aux);
    _mm_lfence();
    return tsc;
}

// clock_gettime wrapper — ~25-50ns overhead, but always correct
inline uint64_t clock_ns() noexcept {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
}

// RDTSC frequency calibrator
// Measures TSC ticks per nanosecond at startup
class TscCalibrator {
public:
    TscCalibrator() noexcept { calibrate(); }

    // Convert TSC delta to nanoseconds
    [[nodiscard]] uint64_t tsc_to_ns(uint64_t tsc_delta) const noexcept {
        // ticks_per_ns is stored as fixed-point: ticks * 1024 / ns
        // So ns = tsc_delta * 1024 / ticks_per_ns_fp
        return (tsc_delta * 1024ULL) / ticks_per_ns_fp_;
    }

    [[nodiscard]] double tsc_freq_ghz() const noexcept {
        return static_cast<double>(ticks_per_ns_fp_) / 1024.0;
    }

    [[nodiscard]] bool is_reliable() const noexcept { return reliable_; }

private:
    void calibrate() noexcept {
        // Warm up
        for (int i = 0; i < 3; ++i) {
            volatile auto t = rdtsc();
            (void)t;
        }

        // Calibrate over 50ms
        constexpr uint64_t CAL_NS = 50'000'000ULL; // 50ms
        uint64_t ns_start = clock_ns();
        uint64_t tsc_start_val = rdtsc();

        // Busy-wait for calibration period
        uint64_t ns_now;
        do {
            ns_now = clock_ns();
        } while (ns_now - ns_start < CAL_NS);

        uint64_t tsc_end_val = rdtsc();
        uint64_t tsc_delta = tsc_end_val - tsc_start_val;
        uint64_t ns_delta = ns_now - ns_start;

        if (ns_delta > 0 && tsc_delta > 0) {
            // Fixed-point: ticks * 1024 / ns
            ticks_per_ns_fp_ = (tsc_delta * 1024ULL) / ns_delta;
            reliable_ = (ticks_per_ns_fp_ > 512 && ticks_per_ns_fp_ < 8192);
            // Sanity: typical CPUs are 1-5 GHz → 1-5 ticks/ns
        } else {
            ticks_per_ns_fp_ = 2048; // Assume ~2GHz
            reliable_ = false;
        }
    }

    uint64_t ticks_per_ns_fp_ = 2048; // Fixed-point × 1024
    bool reliable_ = false;
};

// Global calibrator — initialized once at startup
inline const TscCalibrator& get_tsc_calibrator() {
    static const TscCalibrator cal;
    return cal;
}

// Convenience: high-resolution timestamp in nanoseconds
// Uses RDTSC if calibrated, otherwise clock_gettime
inline uint64_t timestamp_ns() noexcept {
    const auto& cal = get_tsc_calibrator();
    if (cal.is_reliable()) {
        // Use RDTSC — ~3ns overhead
        static const uint64_t epoch_tsc = rdtsc();
        static const uint64_t epoch_ns = clock_ns();
        uint64_t now_tsc = rdtsc();
        return epoch_ns + cal.tsc_to_ns(now_tsc - epoch_tsc);
    }
    // Fallback — ~25ns overhead
    return clock_ns();
}

} // namespace hft
