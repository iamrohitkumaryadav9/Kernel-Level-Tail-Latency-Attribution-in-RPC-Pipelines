#pragma once
// order_book.h — Price-time priority order book
// Uses sorted vectors (cache-friendly for small books) with memory pool allocation

#include <cstdint>
#include <vector>
#include <algorithm>
#include <optional>

#include "order.h"
#include "memory_pool.h"

namespace hft {

struct Fill {
    uint64_t order_id;
    uint64_t match_order_id;
    double   fill_price;
    int64_t  fill_qty;
    bool     is_maker;           // true = resting order, false = incoming order
};

struct MatchResult {
    bool     matched = false;
    double   fill_price = 0.0;
    int64_t  fill_qty = 0;
    OrderStatus status = OrderStatus::NEW;
    std::vector<Fill> fills;
};

class OrderBook {
public:
    OrderBook() = default;

    // Add a new order — O(n) insertion into sorted vector
    // Returns match result if the order crosses the spread
    MatchResult add_order(uint64_t id, const char* symbol, Side side,
                          double price, int64_t qty, uint64_t timestamp_ns) {
        Order* order = pool_.emplace(id, symbol, side, price, qty, timestamp_ns);
        if (!order) {
            MatchResult r;
            r.status = OrderStatus::REJECTED;
            return r;
        }

        // Try to match against opposite side
        MatchResult result = try_match(order);

        // If not fully filled, add to book
        if (order->is_active()) {
            if (order->is_bid()) {
                insert_sorted(bids_, order, BidCompare{});
            } else {
                insert_sorted(asks_, order, AskCompare{});
            }
        } else {
            // Fully matched — return to pool
            pool_.deallocate(order);
        }

        return result;
    }

    // Best bid/offer
    [[nodiscard]] std::optional<double> best_bid() const {
        return bids_.empty() ? std::nullopt : std::optional(bids_.front()->price);
    }

    [[nodiscard]] std::optional<double> best_ask() const {
        return asks_.empty() ? std::nullopt : std::optional(asks_.front()->price);
    }

    [[nodiscard]] std::optional<double> spread() const {
        auto b = best_bid();
        auto a = best_ask();
        if (b && a) return *a - *b;
        return std::nullopt;
    }

    [[nodiscard]] std::size_t bid_depth() const { return bids_.size(); }
    [[nodiscard]] std::size_t ask_depth() const { return asks_.size(); }
    [[nodiscard]] std::size_t total_orders() const { return pool_.allocated(); }

    void clear() {
        for (auto* o : bids_) pool_.deallocate(o);
        for (auto* o : asks_) pool_.deallocate(o);
        bids_.clear();
        asks_.clear();
    }

private:
    MatchResult try_match(Order* incoming) {
        MatchResult result;

        auto& opposite = incoming->is_bid() ? asks_ : bids_;

        while (!opposite.empty() && incoming->remaining > 0) {
            Order* resting = opposite.front();

            // Check if price crosses
            bool crosses = incoming->is_bid()
                ? (incoming->price >= resting->price)
                : (incoming->price <= resting->price);

            if (!crosses) break;

            // Match at resting order's price (price-time priority)
            int64_t match_qty = std::min(incoming->remaining, resting->remaining);
            double match_price = resting->price;

            incoming->remaining -= match_qty;
            resting->remaining -= match_qty;

            result.fills.push_back(Fill{
                .order_id = incoming->order_id,
                .match_order_id = resting->order_id,
                .fill_price = match_price,
                .fill_qty = match_qty,
                .is_maker = false
            });

            result.matched = true;
            result.fill_price = match_price;
            result.fill_qty += match_qty;

            if (resting->remaining == 0) {
                resting->status = OrderStatus::FILLED;
                pool_.deallocate(resting);
                opposite.erase(opposite.begin());
            } else {
                resting->status = OrderStatus::PARTIAL;
            }
        }

        if (incoming->remaining == 0) {
            incoming->status = OrderStatus::FILLED;
            result.status = OrderStatus::FILLED;
        } else if (incoming->remaining < incoming->quantity) {
            incoming->status = OrderStatus::PARTIAL;
            result.status = OrderStatus::PARTIAL;
        } else {
            result.status = OrderStatus::NEW;
        }

        return result;
    }

    template <typename Comp>
    void insert_sorted(std::vector<Order*>& book, Order* order, Comp comp) {
        auto pos = std::lower_bound(book.begin(), book.end(), order, comp);
        book.insert(pos, order);
    }

    std::vector<Order*> bids_;  // Sorted: highest price first
    std::vector<Order*> asks_;  // Sorted: lowest price first

    MemoryPool<Order, 65536> pool_;
};

} // namespace hft
