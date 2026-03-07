#pragma once
// order.h — Cache-line aligned order struct for HFT matching engine
//
// Design principles:
//   - Exactly 64 bytes (1 cache line) — no padding waste
//   - Trivially copyable for zero-copy ring buffer transport
//   - Fixed-size symbol field — no heap allocation
//   - Timestamps via RDTSC for nanosecond precision

#include <cstdint>
#include <cstring>

namespace hft {

enum class Side : uint32_t { BID = 0, ASK = 1 };
enum class OrderStatus : uint32_t {
    NEW = 0,
    PARTIAL = 1,
    FILLED = 2,
    CANCELLED = 3,
    REJECTED = 4
};

// Cache-line aligned order — exactly 64 bytes
struct alignas(64) Order {
    uint64_t    order_id;        // 8  — unique ID
    double      price;           // 8  — limit price
    int64_t     quantity;        // 8  — original quantity
    int64_t     remaining;       // 8  — remaining unfilled quantity
    uint64_t    timestamp_ns;    // 8  — insertion time (nanoseconds)
    Side        side;            // 4  — BID or ASK
    OrderStatus status;          // 4  — current status
    char        symbol[16];      // 16 — fixed-size symbol (no heap alloc)
    // Total: 8+8+8+8+8+4+4+16 = 64 bytes ← exactly 1 cache line

    Order() noexcept
        : order_id(0), price(0), quantity(0), remaining(0),
          timestamp_ns(0), side(Side::BID), status(OrderStatus::NEW) {
        std::memset(symbol, 0, sizeof(symbol));
    }

    Order(uint64_t id, const char* sym, Side s, double px, int64_t qty,
          uint64_t ts) noexcept
        : order_id(id), price(px), quantity(qty), remaining(qty),
          timestamp_ns(ts), side(s), status(OrderStatus::NEW) {
        std::memset(symbol, 0, sizeof(symbol));
        std::strncpy(symbol, sym, sizeof(symbol) - 1);
    }

    [[nodiscard]] bool is_bid() const noexcept { return side == Side::BID; }
    [[nodiscard]] bool is_ask() const noexcept { return side == Side::ASK; }
    [[nodiscard]] bool is_active() const noexcept {
        return status == OrderStatus::NEW || status == OrderStatus::PARTIAL;
    }
};

// Verify it's exactly one cache line
static_assert(sizeof(Order) == 64, "Order must be exactly 64 bytes (1 cache line)");
static_assert(alignof(Order) == 64, "Order must be cache-line aligned");

// Order comparison for price-time priority
// Bids: highest price first, then earliest timestamp
// Asks: lowest price first, then earliest timestamp
struct BidCompare {
    bool operator()(const Order* a, const Order* b) const noexcept {
        if (a->price != b->price) return a->price > b->price;
        return a->timestamp_ns < b->timestamp_ns;
    }
};

struct AskCompare {
    bool operator()(const Order* a, const Order* b) const noexcept {
        if (a->price != b->price) return a->price < b->price;
        return a->timestamp_ns < b->timestamp_ns;
    }
};

} // namespace hft
