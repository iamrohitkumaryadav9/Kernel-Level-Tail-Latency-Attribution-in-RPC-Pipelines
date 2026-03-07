// test_hdr_histogram.cpp — Unit tests for zero-allocation HDR histogram

#include <gtest/gtest.h>
#include "../loadgen/hdr_histogram.h"

TEST(HdrHistogramTest, EmptyHistogram) {
    hft::HdrHistogram hist;
    EXPECT_EQ(hist.count(), 0u);
    EXPECT_EQ(hist.p50(), 0u);
    EXPECT_EQ(hist.p99(), 0u);
    EXPECT_EQ(hist.mean(), 0.0);
}

TEST(HdrHistogramTest, SingleValue) {
    hft::HdrHistogram hist;
    hist.record(1000); // 1µs

    EXPECT_EQ(hist.count(), 1u);
    EXPECT_EQ(hist.min_value(), 1000u);
    EXPECT_EQ(hist.max_value(), 1000u);
}

TEST(HdrHistogramTest, Percentiles) {
    hft::HdrHistogram hist;

    // Record 100 values from 1µs to 100µs (×1000 = ns)
    for (uint64_t i = 1; i <= 100; ++i) {
        hist.record(i * 1000);
    }

    EXPECT_EQ(hist.count(), 100u);

    // p50 should be around 50µs
    uint64_t p50 = hist.p50();
    EXPECT_GE(p50, 30000u);  // At least 30µs
    EXPECT_LE(p50, 70000u);  // At most 70µs

    // p99 should be around 99µs
    uint64_t p99 = hist.p99();
    EXPECT_GE(p99, 80000u);
    EXPECT_LE(p99, 120000u);
}

TEST(HdrHistogramTest, HighValues) {
    hft::HdrHistogram hist;

    // Record values in the millisecond range
    hist.record(1'000'000);   // 1ms
    hist.record(10'000'000);  // 10ms
    hist.record(100'000'000); // 100ms

    EXPECT_EQ(hist.count(), 3u);
    EXPECT_EQ(hist.min_value(), 1'000'000u);
    EXPECT_EQ(hist.max_value(), 100'000'000u);
}

TEST(HdrHistogramTest, Distribution) {
    hft::HdrHistogram hist;

    for (uint64_t i = 0; i < 10000; ++i) {
        hist.record(i * 100);
    }

    auto dist = hist.distribution();
    EXPECT_EQ(dist.size(), 9u);
    EXPECT_DOUBLE_EQ(dist[0].percentage, 10.0);
    EXPECT_DOUBLE_EQ(dist[4].percentage, 90.0);

    // Each point should be monotonically increasing
    for (size_t i = 1; i < dist.size(); ++i) {
        EXPECT_GE(dist[i].latency_ns, dist[i-1].latency_ns);
    }
}

TEST(HdrHistogramTest, Reset) {
    hft::HdrHistogram hist;
    hist.record(1000);
    hist.record(2000);
    EXPECT_EQ(hist.count(), 2u);

    hist.reset();
    EXPECT_EQ(hist.count(), 0u);
    EXPECT_EQ(hist.p50(), 0u);
}
