#pragma once
// correlation_engine.h — Windowed correlation computation
// Computes rolling Spearman ρ between wakeup delay and app latency

#include <cstdint>
#include <deque>
#include <cmath>
#include <vector>
#include <algorithm>
#include <numeric>

namespace hft {

struct CorrelationResult {
    double spearman_rho = 0.0;
    double pearson_r = 0.0;
    size_t window_size = 0;
    uint64_t spike_count = 0;      // Windows where p99 > threshold
    uint64_t calm_count = 0;       // Windows where p99 < threshold
};

struct WindowSample {
    uint64_t timestamp_ns;
    double wakeup_delay_p99_us;
    double softirq_time_ms;
    double app_p99_ms;
    uint64_t tcp_retransmits;
};

class CorrelationEngine {
public:
    explicit CorrelationEngine(size_t window_size = 30,
                               double spike_threshold_ms = 200.0)
        : max_window_(window_size), spike_threshold_(spike_threshold_ms) {}

    // Add a new window sample
    void add_sample(const WindowSample& sample) {
        samples_.push_back(sample);
        if (samples_.size() > max_window_) {
            samples_.pop_front();
        }
    }

    // Compute correlation between wakeup delay and app p99
    [[nodiscard]] CorrelationResult compute() const {
        CorrelationResult result;
        result.window_size = samples_.size();

        if (samples_.size() < 3) return result;

        std::vector<double> x, y;
        for (const auto& s : samples_) {
            x.push_back(s.wakeup_delay_p99_us);
            y.push_back(s.app_p99_ms);
            if (s.app_p99_ms > spike_threshold_)
                result.spike_count++;
            else
                result.calm_count++;
        }

        result.spearman_rho = spearman_correlation(x, y);
        result.pearson_r = pearson_correlation(x, y);
        return result;
    }

    void clear() { samples_.clear(); }

private:
    // Spearman rank correlation (non-parametric)
    static double spearman_correlation(const std::vector<double>& x,
                                       const std::vector<double>& y) {
        auto rx = ranks(x);
        auto ry = ranks(y);
        return pearson_correlation(rx, ry);
    }

    // Pearson correlation coefficient
    static double pearson_correlation(const std::vector<double>& x,
                                      const std::vector<double>& y) {
        size_t n = x.size();
        if (n < 2) return 0.0;

        double sum_x = 0, sum_y = 0, sum_xy = 0, sum_x2 = 0, sum_y2 = 0;
        for (size_t i = 0; i < n; ++i) {
            sum_x += x[i];
            sum_y += y[i];
            sum_xy += x[i] * y[i];
            sum_x2 += x[i] * x[i];
            sum_y2 += y[i] * y[i];
        }

        double num = n * sum_xy - sum_x * sum_y;
        double den = std::sqrt((n * sum_x2 - sum_x * sum_x) *
                               (n * sum_y2 - sum_y * sum_y));
        return (den > 1e-10) ? num / den : 0.0;
    }

    // Compute ranks (1-based, averaged for ties)
    static std::vector<double> ranks(const std::vector<double>& values) {
        size_t n = values.size();
        std::vector<size_t> indices(n);
        std::iota(indices.begin(), indices.end(), 0);
        std::sort(indices.begin(), indices.end(),
                  [&](size_t a, size_t b) { return values[a] < values[b]; });

        std::vector<double> result(n);
        for (size_t i = 0; i < n;) {
            size_t j = i;
            while (j < n && values[indices[j]] == values[indices[i]]) ++j;
            double avg_rank = (i + j + 1.0) / 2.0; // 1-based average
            for (size_t k = i; k < j; ++k) result[indices[k]] = avg_rank;
            i = j;
        }
        return result;
    }

    std::deque<WindowSample> samples_;
    size_t max_window_;
    double spike_threshold_;
};

} // namespace hft
