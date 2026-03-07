#pragma once
// bpf_map_reader.h — Read eBPF maps via sysfs/bpf syscall
// Similar to how market data feed handlers consume kernel ring buffers

#include <cstdint>
#include <string>
#include <vector>
#include <array>

namespace hft {

// Per-experiment snapshot of eBPF metrics
struct EbpfSnapshot {
    uint64_t timestamp_ns;

    // Wakeup delay histogram (64 log2(us) buckets, summed across CPUs)
    std::array<uint64_t, 64> wakeup_delay_hist{};
    uint64_t wakeup_delay_p50_us = 0;
    uint64_t wakeup_delay_p99_us = 0;
    uint64_t wakeup_total_events = 0;

    // Softirq time per vector (10 vectors)
    std::array<uint64_t, 10> softirq_time_ns{};
    std::array<uint64_t, 10> softirq_count{};
    uint64_t total_softirq_time_ns = 0;
    uint64_t total_softirq_count = 0;

    // TCP retransmit
    uint64_t tcp_retransmit_total = 0;
};

// Reads eBPF maps exported by the rqdelay Go loader via Prometheus metrics
// or directly from /sys/fs/bpf pinned maps
class BpfMapReader {
public:
    // Initialize reader — connects to Prometheus metrics endpoint
    explicit BpfMapReader(const std::string& metrics_url = "http://localhost:9090/metrics");

    // Take a snapshot of current eBPF metrics
    EbpfSnapshot snapshot();

    // Compute delta between two snapshots (for rate calculation)
    static EbpfSnapshot delta(const EbpfSnapshot& prev, const EbpfSnapshot& curr);

    bool is_connected() const { return connected_; }

private:
    // Parse Prometheus text format metrics
    EbpfSnapshot parse_prometheus(const std::string& body);

    // Estimate percentile from log2(us) histogram
    static uint64_t estimate_percentile(const std::array<uint64_t, 64>& hist, double q);

    std::string metrics_url_;
    bool connected_ = false;
};

// Softirq vector names (matching kernel names)
constexpr const char* SOFTIRQ_NAMES[] = {
    "HI", "TIMER", "NET_TX", "NET_RX", "BLOCK",
    "IRQ_POLL", "TASKLET", "SCHED", "HRTIMER", "RCU"
};

} // namespace hft
