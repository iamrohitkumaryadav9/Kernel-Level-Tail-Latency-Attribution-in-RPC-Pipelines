// test_spsc_ring.cpp — Unit tests for lock-free SPSC ring buffer

#include <gtest/gtest.h>
#include <thread>
#include <vector>
#include <atomic>

// Include from loadgen
#include "../loadgen/spsc_ring.h"

struct TestItem {
    uint64_t value;
    uint32_t id;
    uint32_t pad;
};

TEST(SpscRingTest, PushPopBasic) {
    hft::SpscRing<TestItem, 16> ring;
    EXPECT_TRUE(ring.empty());
    EXPECT_EQ(ring.size(), 0u);

    TestItem item{42, 1, 0};
    EXPECT_TRUE(ring.try_push(item));
    EXPECT_FALSE(ring.empty());
    EXPECT_EQ(ring.size(), 1u);

    auto popped = ring.try_pop();
    ASSERT_TRUE(popped.has_value());
    EXPECT_EQ(popped->value, 42u);
    EXPECT_EQ(popped->id, 1u);
    EXPECT_TRUE(ring.empty());
}

TEST(SpscRingTest, FullRing) {
    hft::SpscRing<TestItem, 4> ring; // Capacity = 3 (power-of-2 - 1)

    EXPECT_TRUE(ring.try_push({1, 0, 0}));
    EXPECT_TRUE(ring.try_push({2, 0, 0}));
    EXPECT_TRUE(ring.try_push({3, 0, 0}));
    EXPECT_FALSE(ring.try_push({4, 0, 0})); // Should fail — full
}

TEST(SpscRingTest, Wraparound) {
    hft::SpscRing<TestItem, 4> ring;

    // Fill and empty multiple times to test wraparound
    for (int cycle = 0; cycle < 10; ++cycle) {
        for (uint64_t i = 0; i < 3; ++i) {
            EXPECT_TRUE(ring.try_push({i + cycle * 3, 0, 0}));
        }
        for (uint64_t i = 0; i < 3; ++i) {
            auto val = ring.try_pop();
            ASSERT_TRUE(val.has_value());
            EXPECT_EQ(val->value, i + cycle * 3);
        }
        EXPECT_TRUE(ring.empty());
    }
}

TEST(SpscRingTest, DrainBatch) {
    hft::SpscRing<TestItem, 16> ring;

    for (uint64_t i = 0; i < 10; ++i) {
        ring.try_push({i, 0, 0});
    }

    std::vector<TestItem> batch(16);
    auto count = ring.drain(batch.begin(), 16);
    EXPECT_EQ(count, 10u);
    EXPECT_TRUE(ring.empty());

    for (uint64_t i = 0; i < 10; ++i) {
        EXPECT_EQ(batch[i].value, i);
    }
}

TEST(SpscRingTest, ConcurrentProducerConsumer) {
    hft::SpscRing<TestItem, 65536> ring;
    constexpr uint64_t NUM_ITEMS = 100000;
    std::atomic<bool> done{false};

    // Producer thread
    std::thread producer([&] {
        for (uint64_t i = 0; i < NUM_ITEMS; ++i) {
            while (!ring.try_push({i, 0, 0})) {
                std::this_thread::yield();
            }
        }
        done.store(true, std::memory_order_release);
    });

    // Consumer thread
    uint64_t consumed = 0;
    uint64_t expected = 0;
    std::thread consumer([&] {
        while (!done.load(std::memory_order_acquire) || !ring.empty()) {
            auto item = ring.try_pop();
            if (item.has_value()) {
                EXPECT_EQ(item->value, expected);
                expected++;
                consumed++;
            } else {
                std::this_thread::yield();
            }
        }
    });

    producer.join();
    consumer.join();

    EXPECT_EQ(consumed, NUM_ITEMS);
    EXPECT_EQ(expected, NUM_ITEMS);
}
