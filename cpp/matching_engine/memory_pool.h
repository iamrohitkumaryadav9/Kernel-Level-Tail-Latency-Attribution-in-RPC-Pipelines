#pragma once
// memory_pool.h — Fixed-size slab allocator for Order objects
//
// HFT design principles:
//   - O(1) alloc and free (free-list based)
//   - Zero heap allocation after construction
//   - No fragmentation (fixed-size blocks)
//   - Cache-line aligned blocks
//   - Deterministic latency: no system calls on alloc/free

#include <cstddef>
#include <cstdint>
#include <memory>
#include <new>
#include <stdexcept>
#include <vector>

namespace hft {

template <typename T, std::size_t PoolSize = 65536>
class MemoryPool {
    static_assert(sizeof(T) >= sizeof(void*),
                  "Block size must be at least pointer size for free list");

public:
    MemoryPool() {
        // Allocate aligned memory for all blocks
        pool_.reset(static_cast<char*>(
            ::operator new(PoolSize * sizeof(T), std::align_val_t{alignof(T)})));

        // Build free list
        for (std::size_t i = 0; i < PoolSize; ++i) {
            auto* block = reinterpret_cast<FreeBlock*>(pool_.get() + i * sizeof(T));
            block->next = (i + 1 < PoolSize)
                ? reinterpret_cast<FreeBlock*>(pool_.get() + (i + 1) * sizeof(T))
                : nullptr;
        }
        free_head_ = reinterpret_cast<FreeBlock*>(pool_.get());
        allocated_ = 0;
    }

    ~MemoryPool() {
        if (pool_) {
            ::operator delete(pool_.release(), std::align_val_t{alignof(T)});
        }
    }

    // Non-copyable, non-movable
    MemoryPool(const MemoryPool&) = delete;
    MemoryPool& operator=(const MemoryPool&) = delete;

    // Allocate a block — O(1), no system call
    [[nodiscard]] T* allocate() {
        if (!free_head_) return nullptr; // Pool exhausted

        auto* block = free_head_;
        free_head_ = block->next;
        allocated_++;

        // Construct T in-place
        return new (block) T();
    }

    // Allocate with constructor args — O(1)
    template <typename... Args>
    [[nodiscard]] T* emplace(Args&&... args) {
        if (!free_head_) return nullptr;

        auto* block = free_head_;
        free_head_ = block->next;
        allocated_++;

        return new (block) T(std::forward<Args>(args)...);
    }

    // Free a block — O(1), no system call
    void deallocate(T* ptr) {
        if (!ptr) return;

        // Call destructor
        ptr->~T();

        // Add to free list
        auto* block = reinterpret_cast<FreeBlock*>(ptr);
        block->next = free_head_;
        free_head_ = block;
        allocated_--;
    }

    [[nodiscard]] std::size_t allocated() const noexcept { return allocated_; }
    [[nodiscard]] std::size_t available() const noexcept { return PoolSize - allocated_; }
    [[nodiscard]] static constexpr std::size_t capacity() noexcept { return PoolSize; }

private:
    struct FreeBlock {
        FreeBlock* next;
    };

    struct PoolDeleter {
        void operator()(char* p) { /* custom delete handled in destructor */ }
    };

    std::unique_ptr<char, PoolDeleter> pool_;
    FreeBlock* free_head_ = nullptr;
    std::size_t allocated_ = 0;
};

} // namespace hft
