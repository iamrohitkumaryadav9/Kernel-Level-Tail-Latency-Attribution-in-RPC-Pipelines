// test_order_book.cpp — Unit tests for matching engine order book

#include <gtest/gtest.h>
#include "../matching_engine/order.h"
#include "../matching_engine/order_book.h"
#include "../matching_engine/memory_pool.h"

TEST(OrderTest, SizeAndAlignment) {
    EXPECT_EQ(sizeof(hft::Order), 64u);
    EXPECT_EQ(alignof(hft::Order), 64u);
}

TEST(OrderTest, Construction) {
    hft::Order order(1, "AAPL", hft::Side::BID, 150.25, 100, 12345);
    EXPECT_EQ(order.order_id, 1u);
    EXPECT_STREQ(order.symbol, "AAPL");
    EXPECT_TRUE(order.is_bid());
    EXPECT_FALSE(order.is_ask());
    EXPECT_DOUBLE_EQ(order.price, 150.25);
    EXPECT_EQ(order.quantity, 100);
    EXPECT_EQ(order.remaining, 100);
    EXPECT_TRUE(order.is_active());
}

TEST(MemoryPoolTest, AllocateAndFree) {
    hft::MemoryPool<hft::Order, 16> pool;

    EXPECT_EQ(pool.available(), 16u);
    EXPECT_EQ(pool.allocated(), 0u);

    auto* o1 = pool.allocate();
    ASSERT_NE(o1, nullptr);
    EXPECT_EQ(pool.allocated(), 1u);
    EXPECT_EQ(pool.available(), 15u);

    pool.deallocate(o1);
    EXPECT_EQ(pool.allocated(), 0u);
    EXPECT_EQ(pool.available(), 16u);
}

TEST(MemoryPoolTest, Exhaustion) {
    hft::MemoryPool<hft::Order, 4> pool;

    std::vector<hft::Order*> ptrs;
    for (int i = 0; i < 4; ++i) {
        auto* p = pool.allocate();
        ASSERT_NE(p, nullptr);
        ptrs.push_back(p);
    }

    // Pool should be exhausted
    EXPECT_EQ(pool.allocate(), nullptr);

    // Free one and try again
    pool.deallocate(ptrs.back());
    ptrs.pop_back();
    auto* p = pool.allocate();
    EXPECT_NE(p, nullptr);
    pool.deallocate(p);

    for (auto* ptr : ptrs) pool.deallocate(ptr);
}

TEST(OrderBookTest, AddBidAndAsk) {
    hft::OrderBook book;

    book.add_order(1, "AAPL", hft::Side::BID, 150.0, 100, 1000);
    book.add_order(2, "AAPL", hft::Side::ASK, 151.0, 100, 2000);

    EXPECT_EQ(book.bid_depth(), 1u);
    EXPECT_EQ(book.ask_depth(), 1u);
    EXPECT_DOUBLE_EQ(*book.best_bid(), 150.0);
    EXPECT_DOUBLE_EQ(*book.best_ask(), 151.0);
    EXPECT_DOUBLE_EQ(*book.spread(), 1.0);
}

TEST(OrderBookTest, MatchCrossingOrders) {
    hft::OrderBook book;

    // Add resting ask at 150
    book.add_order(1, "AAPL", hft::Side::ASK, 150.0, 100, 1000);
    EXPECT_EQ(book.ask_depth(), 1u);

    // Add matching bid at 150 (crosses spread)
    auto result = book.add_order(2, "AAPL", hft::Side::BID, 150.0, 100, 2000);

    EXPECT_TRUE(result.matched);
    EXPECT_EQ(result.status, hft::OrderStatus::FILLED);
    EXPECT_DOUBLE_EQ(result.fill_price, 150.0);
    EXPECT_EQ(result.fill_qty, 100);

    // Both sides should be empty now
    EXPECT_EQ(book.bid_depth(), 0u);
    EXPECT_EQ(book.ask_depth(), 0u);
}

TEST(OrderBookTest, PartialFill) {
    hft::OrderBook book;

    // Ask for 100 shares
    book.add_order(1, "AAPL", hft::Side::ASK, 150.0, 100, 1000);

    // Bid for only 50 shares
    auto result = book.add_order(2, "AAPL", hft::Side::BID, 150.0, 50, 2000);

    EXPECT_TRUE(result.matched);
    EXPECT_EQ(result.fill_qty, 50);

    // Ask should still be there with 50 remaining
    EXPECT_EQ(book.ask_depth(), 1u);
    EXPECT_EQ(book.bid_depth(), 0u);
}

TEST(OrderBookTest, PriceTimePriority) {
    hft::OrderBook book;

    // Add two asks: first at 150, then at 149
    book.add_order(1, "AAPL", hft::Side::ASK, 150.0, 100, 1000);
    book.add_order(2, "AAPL", hft::Side::ASK, 149.0, 100, 2000);

    // Best ask should be the lower price
    EXPECT_DOUBLE_EQ(*book.best_ask(), 149.0);

    // Aggressive bid should match the best (lowest) ask first
    auto result = book.add_order(3, "AAPL", hft::Side::BID, 151.0, 100, 3000);
    EXPECT_TRUE(result.matched);
    EXPECT_DOUBLE_EQ(result.fill_price, 149.0); // Matched at resting price
}
