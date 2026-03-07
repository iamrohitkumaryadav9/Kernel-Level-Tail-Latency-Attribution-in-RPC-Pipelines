#include "grpc_worker.h"

#include <chrono>
#include <thread>

#include <grpcpp/grpcpp.h>
#include "order.grpc.pb.h"
#include "order.pb.h"

namespace hft {

GrpcWorker::GrpcWorker(const WorkerConfig& config, SampleRing& ring,
                       std::atomic<bool>& stop_flag)
    : config_(config), ring_(ring), stop_(stop_flag) {}

void GrpcWorker::start() {
    thread_ = std::thread([this] { run(); });
}

void GrpcWorker::join() {
    if (thread_.joinable()) thread_.join();
}

void GrpcWorker::run() {
    // Create gRPC channel and stub
    auto channel = grpc::CreateChannel(
        config_.target, grpc::InsecureChannelCredentials());
    auto stub = order::GatewayService::NewStub(channel);

    // Pre-build the request (reuse to avoid allocation on hot path)
    order::OrderRequest request;
    request.set_symbol(config_.symbol);
    request.set_price(config_.price);
    request.set_quantity(config_.quantity);

    // Rate limiting: compute inter-request interval
    const uint64_t interval_ns =
        (config_.target_rps > 0)
            ? 1'000'000'000ULL / config_.target_rps
            : 0;

    uint64_t next_send_ns = timestamp_ns();
    uint64_t req_counter = 0;

    while (!stop_.load(std::memory_order_relaxed)) {
        // Rate limiting: wait until next scheduled send time
        if (interval_ns > 0) {
            uint64_t now = timestamp_ns();
            if (now < next_send_ns) {
                // Busy-spin for short waits (<1ms), sleep for longer
                uint64_t wait_ns = next_send_ns - now;
                if (wait_ns > 1'000'000) {
                    std::this_thread::sleep_for(
                        std::chrono::nanoseconds(wait_ns - 500'000));
                } else {
                    // Busy-spin: more accurate for HFT-style tight loops
                    while (timestamp_ns() < next_send_ns) {
                        _mm_pause(); // CPU hint: we're spinning
                    }
                }
            }
            next_send_ns += interval_ns;
        }

        // Set unique order ID
        req_counter++;
        request.set_order_id("HFT-W" + std::to_string(config_.worker_id) +
                             "-" + std::to_string(req_counter));

        // Capture timestamp BEFORE gRPC call
        uint64_t start_ns = timestamp_ns();

        // Make the gRPC call
        order::OrderResponse response;
        grpc::ClientContext context;
        // Set deadline to prevent hanging
        context.set_deadline(std::chrono::system_clock::now() +
                             std::chrono::seconds(5));

        grpc::Status status = stub->SubmitOrder(&context, request, &response);

        // Capture timestamp AFTER gRPC call
        uint64_t end_ns = timestamp_ns();

        requests_sent_++;

        bool ok = status.ok();
        if (ok) {
            requests_ok_++;
        } else {
            requests_err_++;
        }

        // Push sample to SPSC ring (zero contention with stats collector)
        LatencySample sample{
            .start_ns = start_ns,
            .end_ns = end_ns,
            .latency_ns = end_ns - start_ns,
            .ok = ok,
            .worker_id = config_.worker_id,
        };

        if (!ring_.try_push(sample)) {
            ring_drops_++; // Ring full — stats collector not draining fast enough
        }
    }
}

} // namespace hft
