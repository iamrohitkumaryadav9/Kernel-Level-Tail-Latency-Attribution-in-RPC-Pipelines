#pragma once
// csv_exporter.h — Export time-series eBPF data to CSV

#include <fstream>
#include <string>
#include <vector>
#include "bpf_map_reader.h"
#include "correlation_engine.h"

namespace hft {

class CsvExporter {
public:
    explicit CsvExporter(const std::string& filepath)
        : filepath_(filepath) {}

    void write_header() {
        ofs_.open(filepath_);
        if (!ofs_.is_open()) return;
        ofs_ << "timestamp_s,wakeup_delay_p50_us,wakeup_delay_p99_us,"
             << "wakeup_events,softirq_time_ms,softirq_count,"
             << "net_rx_time_ms,net_tx_time_ms,tcp_retransmits,"
             << "spearman_rho,pearson_r\n";
    }

    void write_row(double timestamp_s, const EbpfSnapshot& snap,
                   const CorrelationResult& corr) {
        if (!ofs_.is_open()) return;
        ofs_ << std::fixed << std::setprecision(3)
             << timestamp_s << ","
             << snap.wakeup_delay_p50_us << ","
             << snap.wakeup_delay_p99_us << ","
             << snap.wakeup_total_events << ","
             << snap.total_softirq_time_ns / 1e6 << ","
             << snap.total_softirq_count << ","
             << snap.softirq_time_ns[3] / 1e6 << ","  // NET_RX
             << snap.softirq_time_ns[2] / 1e6 << ","  // NET_TX
             << snap.tcp_retransmit_total << ","
             << corr.spearman_rho << ","
             << corr.pearson_r << "\n";
        ofs_.flush();
    }

    void close() { if (ofs_.is_open()) ofs_.close(); }

private:
    std::string filepath_;
    std::ofstream ofs_;
};

} // namespace hft
