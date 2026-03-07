#pragma once
// spsc_ring.h — Lock-free Single-Producer Single-Consumer ring buffer
//
// HFT design principles:
//   - Power-of-2 size for branchless modulo (bitwise AND)
//   - Cache-line padding between head/tail to prevent false sharing
//   - Only std::memory_order_release (producer) and acquire (consumer)
//   - No CAS loops, no contention, no allocation after construction
//   - Fixed capacity, preallocated at construction

#include <atomic>
#include <array>
#include <cstddef>
#include <cstdint>
#include <new>
#include <optional>
#include <type_traits>

namespace hft {

// Ensure cache line size (typically 64 bytes on x86)
inline constexpr std::size_t CACHE_LINE = 64;

template <typename T, std::size_t Capacity>
    requires (Capacity > 0 && (Capacity & (Capacity - 1)) == 0) // Power of 2
class SpscRing {
public:
    static constexpr std::size_t MASK = Capacity - 1;

    SpscRing() noexcept : head_(0), tail_(0) {
        static_assert(std::is_trivially_copyable_v<T>,
                      "SpscRing requires trivially copyable types for zero-copy semantics");
    }

    // Non-copyable, non-movable (pinned in memory)
    SpscRing(const SpscRing&) = delete;
    SpscRing& operator=(const SpscRing&) = delete;

    // Producer: try to push an element. Returns false if full.
    // Only called from ONE thread.
    [[nodiscard]] bool try_push(const T& item) noexcept {
        const auto head = head_.load(std::memory_order_relaxed);
        const auto next = (head + 1) & MASK;

        // Check if full: next would collide with tail
        if (next == tail_.load(std::memory_order_acquire)) {
            return false; // Full — drop or back-pressure
        }

        buffer_[head] = item;
        head_.store(next, std::memory_order_release);
        return true;
    }

    // Consumer: try to pop an element. Returns nullopt if empty.
    // Only called from ONE thread.
    [[nodiscard]] std::optional<T> try_pop() noexcept {
        const auto tail = tail_.load(std::memory_order_relaxed);

        // Check if empty
        if (tail == head_.load(std::memory_order_acquire)) {
            return std::nullopt; // Empty
        }

        T item = buffer_[tail];
        tail_.store((tail + 1) & MASK, std::memory_order_release);
        return item;
    }

    // Batch pop: drain up to `max_count` elements into output iterator.
    // Returns number of elements drained. Zero-allocation.
    template <typename OutputIt>
    std::size_t drain(OutputIt out, std::size_t max_count) noexcept {
        const auto tail = tail_.load(std::memory_order_relaxed);
        const auto head = head_.load(std::memory_order_acquire);

        std::size_t available = (head - tail) & MASK;
        // head == tail means empty (since we never fill completely)
        if (head >= tail) {
            available = head - tail;
        } else {
            available = Capacity - tail + head;
        }

        std::size_t count = (available < max_count) ? available : max_count;

        auto pos = tail;
        for (std::size_t i = 0; i < count; ++i) {
            *out++ = buffer_[pos];
            pos = (pos + 1) & MASK;
        }

        if (count > 0) {
            tail_.store(pos, std::memory_order_release);
        }
        return count;
    }

    [[nodiscard]] bool empty() const noexcept {
        return head_.load(std::memory_order_acquire) ==
               tail_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::size_t size() const noexcept {
        auto h = head_.load(std::memory_order_acquire);
        auto t = tail_.load(std::memory_order_acquire);
        return (h - t) & MASK;
    }

    static constexpr std::size_t capacity() noexcept { return Capacity - 1; }

private:
    // Cache-line padded to prevent false sharing between producer and consumer
    alignas(CACHE_LINE) std::atomic<std::size_t> head_;
    char pad1_[CACHE_LINE - sizeof(std::atomic<std::size_t>)];

    alignas(CACHE_LINE) std::atomic<std::size_t> tail_;
    char pad2_[CACHE_LINE - sizeof(std::atomic<std::size_t>)];

    alignas(CACHE_LINE) std::array<T, Capacity> buffer_;
};

} // namespace hft
