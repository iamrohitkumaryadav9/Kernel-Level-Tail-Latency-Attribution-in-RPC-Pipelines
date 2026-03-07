#include "grpc_server.h"

#include <iostream>
#include <chrono>
#include <grpcpp/grpcpp.h>

#include "order.grpc.pb.h"
#include "order.pb.h"
#include "matching_engine.h"

namespace hft {

// gRPC server implementation using sync API
// (Async completion-queue approach would be more HFT-like,
//  but sync is sufficient for the pipeline integration demo)
class ExecutionServiceImpl final : public order::ExecutionService::Service {
public:
    explicit ExecutionServiceImpl(MatchingEngine& engine) : engine_(engine) {}

    grpc::Status Execute(grpc::ServerContext* /*context*/,
                         const order::ExecRequest* request,
                         order::ExecResult* response) override {
        auto start = std::chrono::steady_clock::now();

        // Execute via matching engine
        auto result = engine_.market_execute(
            std::hash<std::string>{}(request->order_id()),
            request->symbol(),
            request->quantity(),
            request->price()
        );

        // Populate response
        response->set_order_id(request->order_id());
        response->set_filled(result.filled);
        response->set_fill_price(result.fill_price);
        response->set_fill_qty(result.fill_qty);
        response->set_status(result.status);

        auto elapsed = std::chrono::steady_clock::now() - start;
        auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count();

        // Log first few requests for visibility
        static uint64_t count = 0;
        if (++count <= 5 || count % 10000 == 0) {
            std::cout << "  [execute] order=" << request->order_id()
                      << " symbol=" << request->symbol()
                      << " price=" << result.fill_price
                      << " status=" << result.status
                      << " latency=" << ns << "ns\n";
        }

        return grpc::Status::OK;
    }

private:
    MatchingEngine& engine_;
};

class GrpcServer::Impl {
public:
    Impl(const std::string& addr, MatchingEngine& engine)
        : service_(engine), listen_addr_(addr) {}

    void run() {
        grpc::ServerBuilder builder;
        builder.AddListeningPort(listen_addr_, grpc::InsecureServerCredentials());
        builder.RegisterService(&service_);

        // Performance tuning
        builder.SetMaxReceiveMessageSize(4 * 1024 * 1024);
        builder.SetMaxSendMessageSize(4 * 1024 * 1024);

        server_ = builder.BuildAndStart();
        std::cout << "hft-execution: listening on " << listen_addr_ << "\n";
        server_->Wait();
    }

    void shutdown() {
        if (server_) {
            server_->Shutdown();
        }
    }

private:
    ExecutionServiceImpl service_;
    std::string listen_addr_;
    std::unique_ptr<grpc::Server> server_;
};

GrpcServer::GrpcServer(const std::string& listen_addr, MatchingEngine& engine)
    : impl_(std::make_unique<Impl>(listen_addr, engine)) {}

GrpcServer::~GrpcServer() = default;

void GrpcServer::run() { impl_->run(); }
void GrpcServer::shutdown() { impl_->shutdown(); }

} // namespace hft
