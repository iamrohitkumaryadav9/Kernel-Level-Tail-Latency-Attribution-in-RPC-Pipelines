#pragma once
// grpc_worker.h — gRPC load generation worker thread
// Each worker maintains its own gRPC channel and pushes latency samples
// to a lock-free SPSC ring buffer (zero contention with stats collector)

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>

#include "spsc_ring.h"
#include "timestamp.h"

// Forward declare proto types
namespace order {
class GatewayService;
class OrderRequest;
class OrderResponse;
}

namespace hft {

// Latency sample pushed from worker to stats collector via SPSC ring
struct LatencySample {
    uint64_t start_ns;      // Timestamp before RPC
    uint64_t end_ns;        // Timestamp after RPC
    uint64_t latency_ns;    // end - start
    bool     ok;            // RPC succeeded
    uint32_t worker_id;     // Which worker produced this
};

// Ring buffer type: 64K entries (power of 2), ~1MB per worker
using SampleRing = SpscRing<LatencySample, 65536>;

struct WorkerConfig {
    std::string target;         // gRPC target (e.g., localhost:50051)
    uint32_t    worker_id;
    uint32_t    target_rps;     // Target requests per second for this worker
    std::string symbol;         // Order symbol
    double      price;          // Order price
    int64_t     quantity;       // Order quantity
};

class GrpcWorker {
public:
    GrpcWorker(const WorkerConfig& config, SampleRing& ring,
               std::atomic<bool>& stop_flag);

    // Start the worker thread
    void start();

    // Wait for thread to finish
    void join();

    // Stats
    uint64_t requests_sent() const noexcept { return requests_sent_; }
    uint64_t requests_ok() const noexcept { return requests_ok_; }
    uint64_t requests_err() const noexcept { return requests_err_; }
    uint64_t ring_drops() const noexcept { return ring_drops_; }

private:
    void run();

    WorkerConfig config_;
    SampleRing& ring_;
    std::atomic<bool>& stop_;
    std::thread thread_;

    uint64_t requests_sent_ = 0;
    uint64_t requests_ok_ = 0;
    uint64_t requests_err_ = 0;
    uint64_t ring_drops_ = 0;
};

} // namespace hft
