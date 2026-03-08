# Kernel-Level Tail Latency Attribution in RPC Pipelines

A comprehensive study of tail latency causes and mitigations in Kubernetes-hosted gRPC microservice pipelines, using eBPF kernel instrumentation — with **HFT-grade C++ components** demonstrating low-latency systems programming.

## Overview

This project investigates how Linux kernel scheduling mechanisms — specifically **CFS bandwidth throttling**, **runqueue contention**, and **cross-node network placement** — affect tail latency (p99) in a 5-service gRPC pipeline deployed on Kubernetes.

### Key Findings

| Hypothesis | Result | Evidence |
|:-----------|:-------|:---------|
| H1: CPU contention increases p99 | ✅ Confirmed | 1.75× baseline |
| H2: CFS throttling is dominant | ✅ **Strongly confirmed** | 4.1× with dose-response |
| H3: CPU pinning mitigates latency | ✅ Confirmed | -85% reduction |
| H4: Cross-node amplifies latency | ⚠️ Partial (2/4 pairs) | 3.14× for contention |

## Architecture

```
hft-loadgen (C++) ──→ Gateway → Auth → Risk → MarketData (Redis) → Execution (C++)
         │                ↕                    ↕                       │
         │           Jaeger Tracing      eBPF rqdelay (kernel)         │
         │                                    ↕                        │
         └───────   hft-analyzer (C++)  ←  Prometheus metrics ─────────┘
```

- **5 gRPC microservices** — Go 1.24 pipeline + **C++ matching engine** (drop-in replacement)
- **eBPF instrumentation** (`rqdelay`) tracking `sched_wakeup`, `sched_switch`, `softirq`, `tcp_retransmit`
- **C++ load generator** (`hft-loadgen`) with RDTSC timestamping and lock-free SPSC ring buffers
- **C++ kernel analyzer** (`hft-analyzer`) with live terminal dashboard and Spearman correlation
- **21 experiment configurations** via Kustomize overlays (including E16: C++ execution)
- **Statistical analysis** with Mann-Whitney U tests and Cliff's delta

## Quick Start

```bash
# Build Go service images
for svc in gateway auth risk marketdata execution; do
    docker build --build-arg SERVICE=$svc -t latency-attribution-$svc:latest .
done
docker build -f ebpf/Dockerfile -t latency-attribution-rqdelay:latest .

# Build C++ components
make cpp-build        # Builds hft-loadgen, hft-analyzer, hft-execution
make cpp-test-quick   # Runs all 19 unit tests

# Create Kind cluster
kind create cluster --name latency-attribution --config deploy/kind-multinode.yaml

# Setup & deploy
kubectl label node latency-attribution-worker node-role=node-a
kubectl label node latency-attribution-control-plane node-role=node-b
kubectl taint nodes latency-attribution-control-plane node-role.kubernetes.io/control-plane:NoSchedule-
kind load docker-image latency-attribution-{gateway,auth,risk,marketdata,execution,rqdelay}:latest --name latency-attribution
kubectl apply -k deploy/base/

# Run experiments (~3 hours)
./loadgen/run-all-experiments.sh 2000

# Or use the C++ load generator (HFT-grade)
./cpp/build/loadgen/hft-loadgen --target localhost:50051 --rate 2000 --duration 120 --output results.json

# Analyze
python3 analysis/scripts/analyze_all.py
python3 analysis/scripts/plot_evidence.py
python3 analysis/scripts/statistical_analysis.py
```

See [RUN_GUIDE.md](RUN_GUIDE.md) for complete instructions.

## Project Structure

```
├── blueprint/             # 11 design documents
├── services/              # 5 gRPC microservices (Go)
├── proto/                 # Protocol Buffer definitions
├── pkg/                   # Shared Go packages (delay, tracing)
├── ebpf/                  # eBPF kernel instrumentation (rqdelay)
│   └── bpftrace/          # Rapid-prototyping bpftrace scripts (5 scripts)
├── cpp/                   # C++ HFT components
│   ├── loadgen/           #   hft-loadgen: RDTSC, SPSC ring, HDR histogram
│   ├── analyzer/          #   hft-analyzer: eBPF map consumer, live dashboard
│   ├── matching_engine/   #   hft-execution: lock-free order book, slab allocator
│   ├── tests/             #   GTest unit tests (19 tests)
│   └── CMakeLists.txt     #   Top-level CMake build
├── deploy/
│   ├── base/              # Core Kubernetes manifests
│   └── overlays/          # 24 experiment configurations (E0-E16 + extras + mitigations)
├── loadgen/               # Load generation scripts (ghz)
├── analysis/
│   ├── scripts/           # 16 Python analysis & plotting scripts
│   │   ├── analyze_all.py               # Parse + validate + CSV
│   │   ├── statistical_analysis.py      # Mann-Whitney U + hypothesis eval
│   │   ├── per_request_correlation.py   # Per-request kernel signal correlation (§06.4)
│   │   ├── spike_detection.py           # Sliding-window spike/calm classifier (§06.3)
│   │   ├── chi_squared_throttle.py      # Chi-squared throttle × spike test (§06.5)
│   │   ├── per_signal_mannwhitney.py    # Per-signal case/control attribution (§06.4)
│   │   ├── case_control_analysis.py     # Case/control request split (§06.4)
│   │   ├── windowed_correlation.py      # 100ms windowed correlation (§06.2)
│   │   ├── collect_kernel_metrics.py    # Real /proc + derived kernel metrics
│   │   ├── add_plot_watermarks.py       # Data source watermarks on plots
│   │   ├── plot_evidence.py             # Core 10-figure evidence suite
│   │   ├── plot_results.py              # Basic 3-plot summary
│   │   └── ...                          # Burst, Jaeger, eBPF plots
│   ├── notebooks/         # 3 Jupyter notebooks for interactive analysis
│   ├── plots/             # Generated figures (25+ plots)
│   └── stats/             # Statistical test results (10+ CSV files)
├── data/                  # Experiment results (JSON + CSV)
│   ├── kernel_metrics_source.csv        # Kernel signal provenance chain
│   └── ebpf_per_experiment.csv          # Per-experiment kernel metrics
├── FINDINGS.md            # Hypothesis verification + limitations
└── RUN_GUIDE.md           # Complete execution guide
```

## C++ HFT Components

Three C++ (C++20) components demonstrate HFT-grade systems programming:

| Component | Binary | HFT Skills Demonstrated |
|:----------|:-------|:------------------------|
| **Load Generator** | `hft-loadgen` | RDTSC nanosecond timestamping, lock-free SPSC ring buffer, zero-allocation HDR histogram, busy-spin rate limiting |
| **Kernel Analyzer** | `hft-analyzer` | Raw socket HTTP client (zero deps), Spearman/Pearson correlation, ANSI live dashboard with sparklines, CSV export |
| **Matching Engine** | `hft-execution` | 64-byte cache-line aligned orders, O(1) slab memory pool, price-time priority order book, drop-in gRPC replacement |

**Build & test:**
```bash
make cpp-build       # cmake + make
make cpp-test-quick  # 19 unit tests (SPSC ring, HDR histogram, order book)
make cpp-clean       # clean build
```

See [blueprint/10_cpp_hft_components.md](blueprint/10_cpp_hft_components.md) for full design rationale.

## Requirements

- **Go pipeline**: Go 1.24+, Docker, Kind, kubectl, ghz, Python 3.10+
- **C++ components**: CMake 3.20+, g++ 13+ (C++20), libgrpc++-dev, protobuf-compiler-grpc, libgtest-dev, nlohmann-json3-dev
- See [RUN_GUIDE.md](RUN_GUIDE.md) for full prerequisites

## License

Academic project — Kernel-Level Tail Latency Attribution.
