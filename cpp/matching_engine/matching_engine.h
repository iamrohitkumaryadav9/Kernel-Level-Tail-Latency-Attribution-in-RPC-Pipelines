#pragma once
// matching_engine.h — Core matching logic wrapper
// Manages multiple order books (one per symbol)

#include <cstdint>
#include <string>
#include <unordered_map>
#include <mutex>

#include "order_book.h"

namespace hft {

struct ExecutionResult {
    uint64_t    order_id;
    bool        filled;
    double      fill_price;
    int64_t     fill_qty;
    std::string status;
};

class MatchingEngine {
public:
    MatchingEngine() = default;

    ExecutionResult execute(uint64_t order_id, const std::string& symbol,
                            Side side, double price, int64_t quantity) {
        // Get or create order book for this symbol
        auto& book = get_book(symbol);

        // Get timestamp
        auto ts = static_cast<uint64_t>(
            std::chrono::steady_clock::now().time_since_epoch().count());

        // Submit to order book
        auto result = book.add_order(order_id, symbol.c_str(), side,
                                     price, quantity, ts);

        ExecutionResult er;
        er.order_id = order_id;
        er.filled = (result.status == OrderStatus::FILLED);
        er.fill_price = result.fill_price;
        er.fill_qty = result.fill_qty;

        switch (result.status) {
            case OrderStatus::FILLED:   er.status = "FILLED"; break;
            case OrderStatus::PARTIAL:  er.status = "PARTIAL"; break;
            case OrderStatus::REJECTED: er.status = "REJECTED"; break;
            default:                    er.status = "NEW"; break;
        }

        total_orders_++;
        if (er.filled) total_fills_++;

        return er;
    }

    // Simple market order: immediately execute at best available price
    ExecutionResult market_execute(uint64_t order_id, const std::string& symbol,
                                   int64_t quantity, double ref_price) {
        // For the pipeline integration, we simulate a fill at ref_price
        // (matching the behavior of the Go execution service)
        ExecutionResult er;
        er.order_id = order_id;
        er.filled = true;
        er.fill_price = ref_price;
        er.fill_qty = quantity;
        er.status = "FILLED";

        total_orders_++;
        total_fills_++;

        return er;
    }

    uint64_t total_orders() const { return total_orders_; }
    uint64_t total_fills() const { return total_fills_; }

private:
    OrderBook& get_book(const std::string& symbol) {
        std::lock_guard<std::mutex> lock(mutex_);
        return books_[symbol];
    }

    std::unordered_map<std::string, OrderBook> books_;
    std::mutex mutex_;
    uint64_t total_orders_ = 0;
    uint64_t total_fills_ = 0;
};

} // namespace hft
