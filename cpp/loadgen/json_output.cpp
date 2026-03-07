#include "json_output.h"

#include <fstream>
#include <iomanip>
#include <iostream>
#include <nlohmann/json.hpp>

namespace hft {

using json = nlohmann::json;

void write_json_output(const std::string& filepath,
                       const CollectorStats& stats,
                       const HdrHistogram& histogram,
                       uint64_t start_ns,
                       uint64_t end_ns,
                       uint64_t target_rps,
                       uint32_t num_workers) {
    json j;

    // Top-level fields (ghz-compatible)
    j["count"] = stats.total_samples;
    j["total"] = end_ns - start_ns;  // Total duration in nanoseconds
    j["average"] = static_cast<uint64_t>(stats.mean_ns);
    j["fastest"] = stats.min_ns;
    j["slowest"] = stats.max_ns;
    j["rps"] = stats.rps;

    // Latency distribution (ghz format)
    auto dist = histogram.distribution();
    json lat_dist = json::array();
    for (const auto& pt : dist) {
        lat_dist.push_back({
            {"percentage", pt.percentage},
            {"latency", pt.latency_ns}
        });
    }
    j["latencyDistribution"] = lat_dist;

    // Status code distribution (ghz format)
    json scd;
    scd["OK"] = stats.ok_samples;
    if (stats.err_samples > 0) {
        scd["Unavailable"] = stats.err_samples;
    }
    j["statusCodeDistribution"] = scd;

    // Error distribution
    json ed;
    if (stats.err_samples > 0) {
        ed["rpc error: code = Unavailable"] = stats.err_samples;
    }
    j["errorDistribution"] = ed;

    // HFT-specific metadata (not in ghz, but useful)
    json meta;
    meta["generator"] = "hft-loadgen (C++)";
    meta["timestamp_method"] = get_tsc_calibrator().is_reliable() ? "RDTSC" : "clock_gettime";
    meta["tsc_freq_ghz"] = get_tsc_calibrator().tsc_freq_ghz();
    meta["target_rps"] = target_rps;
    meta["num_workers"] = num_workers;
    meta["ring_buffer_type"] = "SPSC lock-free";
    meta["histogram_type"] = "HDR (zero-allocation)";
    j["hft_metadata"] = meta;

    // Tags for ghz compatibility
    json tags;
    tags["tool"] = "hft-loadgen";
    j["tags"] = tags;

    // Write to file
    std::ofstream ofs(filepath);
    if (!ofs.is_open()) {
        std::cerr << "ERROR: Cannot open output file: " << filepath << "\n";
        return;
    }
    ofs << std::setw(2) << j << std::endl;
    ofs.close();

    std::cout << "  ✓ JSON output written: " << filepath << "\n";
}

} // namespace hft
