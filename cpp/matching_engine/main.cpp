// main.cpp — HFT Matching Engine Service
// C++ execution service: drop-in replacement for Go execution service
//
// Usage:
//   hft-execution [--port 50055] [--verbose]

#include <csignal>
#include <cstdlib>
#include <iostream>
#include <string>

#include "grpc_server.h"
#include "matching_engine.h"
#include "order.h"
#include "memory_pool.h"

namespace {
hft::GrpcServer* g_server = nullptr;
void signal_handler(int) {
    if (g_server) g_server->shutdown();
}
}

int main(int argc, char* argv[]) {
    std::string addr = "0.0.0.0:50055";
    bool verbose = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--port" || arg == "-p") && i + 1 < argc) {
            addr = "0.0.0.0:" + std::string(argv[++i]);
        } else if (arg == "--addr" && i + 1 < argc) {
            addr = argv[++i];
        } else if (arg == "--verbose" || arg == "-v") {
            verbose = true;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "hft-execution — C++ Matching Engine Service\n\n"
                      << "Usage: hft-execution [options]\n\n"
                      << "  --port, -p N    Listen port (default: 50055)\n"
                      << "  --addr ADDR     Full listen address (default: 0.0.0.0:50055)\n"
                      << "  --verbose, -v   Verbose logging\n"
                      << "  --help, -h      Show this help\n\n"
                      << "HFT Features:\n"
                      << "  • Lock-free order book (price-time priority)\n"
                      << "  • Slab memory pool (O(1) alloc/free, zero fragmentation)\n"
                      << "  • Cache-line aligned orders (64 bytes exactly)\n"
                      << "  • Drop-in replacement for Go execution service\n";
            return 0;
        }
    }

    // Check for environment variable (K8s compatibility)
    if (const char* env_addr = std::getenv("LISTEN_ADDR")) {
        addr = env_addr;
    }

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    std::cout << "\n"
              << "╔════════════════════════════════════════════════╗\n"
              << "║  hft-execution — C++ Matching Engine (C++20)  ║\n"
              << "╠════════════════════════════════════════════════╣\n"
              << "║  Address:     " << addr << std::string(32 - addr.size(), ' ') << "║\n"
              << "║  Order size:  64 bytes (1 cache line)         ║\n"
              << "║  Pool:        65536 orders (slab allocator)   ║\n"
              << "║  Book:        Price-time priority              ║\n"
              << "╚════════════════════════════════════════════════╝\n\n";

    hft::MatchingEngine engine;
    hft::GrpcServer server(addr, engine);
    g_server = &server;

    server.run();

    std::cout << "\nhft-execution stopped.\n"
              << "  Total orders: " << engine.total_orders() << "\n"
              << "  Total fills:  " << engine.total_fills() << "\n";

    return 0;
}
