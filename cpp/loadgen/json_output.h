#pragma once
// json_output.h — ghz-compatible JSON output writer
// Produces JSON that the existing analyze_all.py can parse directly

#include <string>
#include "stats_collector.h"
#include "hdr_histogram.h"

namespace hft {

// Write results in ghz-compatible JSON format
void write_json_output(const std::string& filepath,
                       const CollectorStats& stats,
                       const HdrHistogram& histogram,
                       uint64_t start_ns,
                       uint64_t end_ns,
                       uint64_t target_rps,
                       uint32_t num_workers);

} // namespace hft
