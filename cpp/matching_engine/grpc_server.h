#pragma once
// grpc_server.h — Async gRPC server implementing ExecutionService
// Drop-in replacement for Go execution service

#include <memory>
#include <string>
#include <atomic>

#include "matching_engine.h"

namespace hft {

class GrpcServer {
public:
    explicit GrpcServer(const std::string& listen_addr, MatchingEngine& engine);
    ~GrpcServer();

    void run();
    void shutdown();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace hft
