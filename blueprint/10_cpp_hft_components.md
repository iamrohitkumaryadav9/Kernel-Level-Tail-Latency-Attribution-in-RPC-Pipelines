# Blueprint 10 — C++ HFT Components

> **Goal**: Extend the project with three C++ (C++20) components that demonstrate HFT-grade systems programming skills relevant to low-latency trading infrastructure.

---

## Motivation

The original project uses Go for all services and `ghz` for load generation. While Go is excellent for microservice development, HFT firms require engineers who can write **zero-allocation**, **cache-aware**, **lock-free** C++ code. These three components bridge the gap:

1. **hft-loadgen** — replaces `ghz` with an HFT-grade load generator
2. **hft-analyzer** — real-time eBPF metric analysis with correlation computation
3. **hft-execution** — replaces the Go execution service with a C++ matching engine

---

## Component A: Low-Latency Load Generator (`hft-loadgen`)

### Design Decisions

| Decision | Rationale |
|:---------|:----------|
| **RDTSC timestamping** | `clock_gettime()` costs ~20ns; RDTSC costs ~3ns via `__rdtsc()` with `lfence` serialization |
| **SPSC ring buffer** | Lock-free producer-consumer between worker threads and stats collector — zero mutex contention |
| **HDR histogram** | Fixed 4KB memory footprint, O(1) record time, log₂-linear bucketing for nanosecond precision |
| **Busy-spin rate limiter** | `_mm_pause()` for sub-millisecond accuracy; `sleep_for()` fallback for longer waits |
| **ghz-compatible output** | JSON format matches existing `analyze_all.py` — zero changes to analysis pipeline |

### File Structure

```
cpp/loadgen/
├── timestamp.h          # RDTSC with clock_gettime calibration
├── spsc_ring.h          # Lock-free Single-Producer Single-Consumer ring
├── hdr_histogram.h      # Zero-allocation HDR histogram
├── grpc_worker.h/cpp    # gRPC worker threads with rate limiting
├── stats_collector.h/cpp# Ring buffer consumer + percentile computation
├── json_output.h/cpp    # ghz-compatible JSON output
├── main.cpp             # CLI entry point (warmup + measurement phases)
└── CMakeLists.txt
```

### Key Implementation Details

**SPSC Ring Buffer** (`spsc_ring.h`):
- Power-of-2 capacity for branchless modulo (`index & (capacity - 1)`)
- Cache-line padding between head and tail indices (prevents false sharing)
- `std::memory_order_release` on push, `std::memory_order_acquire` on pop
- Batch `drain()` method for efficient stats collection

**HDR Histogram** (`hdr_histogram.h`):
- 384 buckets covering 1ns to ~17 seconds
- `std::atomic<uint64_t>` counters for thread-safe recording
- No heap allocation after construction
- `distribution()` method outputs percentile points compatible with ghz JSON

---

## Component B: Kernel Event Analyzer (`hft-analyzer`)

### Design Decisions

| Decision | Rationale |
|:---------|:----------|
| **Raw socket HTTP client** | Zero external dependencies (no libcurl) — demonstrates systems programming |
| **Spearman rank correlation** | Non-parametric — robust to non-normal distributions common in latency data |
| **ANSI terminal dashboard** | Real-time visibility into kernel metrics during experiments |
| **Windowed analysis** | Rolling 30-sample window for online correlation computation |

### File Structure

```
cpp/analyzer/
├── bpf_map_reader.h/cpp    # Prometheus metrics consumer (raw sockets)
├── correlation_engine.h    # Spearman ρ and Pearson r computation
├── terminal_dashboard.h    # ANSI live dashboard with sparklines
├── csv_exporter.h          # Time-series CSV export
├── main.cpp                # Event loop + dashboard orchestration
└── CMakeLists.txt
```

---

## Component C: Matching Engine Service (`hft-execution`)

### Design Decisions

| Decision | Rationale |
|:---------|:----------|
| **64-byte Order struct** | Exactly 1 cache line — no padding waste, optimal for iteration |
| **`static_assert` verification** | Compile-time guarantee of size and alignment |
| **Slab memory pool** | Free-list allocator: O(1) alloc/free, zero fragmentation, no system calls |
| **Sorted vector for order book** | Cache-friendly for small-medium books (~10K orders) vs tree-based structures |
| **Price-time priority** | Industry-standard matching: best price first, then earliest timestamp |
| **Drop-in gRPC replacement** | Same `ExecutionService.Execute` RPC — swappable via K8s overlay |

### File Structure

```
cpp/matching_engine/
├── order.h              # alignas(64) Order struct (exactly 64 bytes)
├── memory_pool.h        # O(1) slab allocator with free-list
├── order_book.h         # Price-time priority matching engine
├── matching_engine.h    # Multi-symbol engine wrapper
├── grpc_server.h/cpp    # ExecutionService gRPC implementation
├── main.cpp             # Server startup (K8s LISTEN_ADDR compatible)
├── CMakeLists.txt
└── Dockerfile           # Multi-stage C++ Docker build
```

### Order Memory Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ order_id (8B) │ price (8B) │ quantity (8B) │ remaining (8B)     │
│ timestamp (8B)│ side (4B)  │ status (4B)   │ symbol[16] (16B)   │
└──────────────────────────────────────────────────────────────────┘
  Total: 64 bytes = 1 cache line                     alignas(64)
```

---

## E16: C++ Execution Experiment

| Parameter | Value |
|:----------|:------|
| **Overlay** | `deploy/overlays/e16-cpp-execution/` |
| **Image** | `latency-attribution-cpp-execution:latest` |
| **What changes** | Go execution → C++ matching engine |
| **What to measure** | Execution latency delta (Go vs C++) |
| **Expected result** | C++ should show lower per-order latency due to zero-allocation design |

---

## Unit Tests

| Test Suite | Tests | What It Validates |
|:-----------|:------|:-----------------|
| `test_spsc_ring` | 5 | Push/pop, full ring, wraparound, batch drain, concurrent correctness |
| `test_hdr_histogram` | 6 | Empty state, percentiles, high values, distribution output, reset |
| `test_order_book` | 8 | 64B alignment, slab alloc/free, pool exhaustion, order matching, partial fills, price-time priority |

All **19 tests passing**.

---

## Build System

- **CMake** top-level build with proto code generation
- **C++20** standard (`-std=c++20`)
- **Dependencies**: gRPC++, Protobuf, GTest, nlohmann-json
- **Optimization**: `-O2 -march=native` for all targets
- **Makefile targets**: `make cpp-build`, `make cpp-test-quick`, `make cpp-clean`
