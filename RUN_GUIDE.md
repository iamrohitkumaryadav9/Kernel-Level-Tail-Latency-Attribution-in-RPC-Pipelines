# Complete Run Guide — Kernel-Level Tail Latency Attribution

This guide walks you through setting up, running, and analyzing all experiments from scratch on a fresh machine.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone & Build](#2-clone--build)
3. [Cluster Setup](#3-cluster-setup)
4. [Deploy Services](#4-deploy-services)
5. [Verify Pipeline](#5-verify-pipeline)
6. [Run Experiments](#6-run-experiments)
7. [Analyze & Plot](#7-analyze--plot)
8. [C++ HFT Components](#8-c-hft-components)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

### System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04+ / Debian 12+ | Ubuntu 24.04 |
| CPU | 4 cores | 8+ cores (enables meaningful contention experiments) |
| RAM | 8 GB | 16 GB |
| Disk | 10 GB free | 20 GB free |
| Kernel | 5.15+ (eBPF support) | 6.1+ |

### Software Dependencies

Install all required tools:

```bash
# 1. Go 1.24+
wget https://go.dev/dl/go1.24.0.linux-amd64.tar.gz
sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf go1.24.0.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin' >> ~/.bashrc
source ~/.bashrc
go version  # should print go1.24.0

# 2. Docker
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker $USER
newgrp docker  # or log out and back in

# 3. Kind (Kubernetes in Docker)
go install sigs.k8s.io/kind@latest

# 4. kubectl
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/

# 5. ghz (gRPC load generator)
go install github.com/bojand/ghz/cmd/ghz@latest

# 6. grpcurl (gRPC testing)
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# 7. Python 3.10+ with matplotlib
sudo apt-get install -y python3 python3-pip
pip3 install matplotlib numpy

# 8. Protobuf compiler
sudo apt-get install -y protobuf-compiler
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

# 9. eBPF build tools (for modifying the eBPF program)
sudo apt-get install -y clang llvm libbpf-dev linux-headers-$(uname -r)

# 10. C++ build tools (for HFT components)
sudo apt-get install -y cmake g++ libgrpc++-dev protobuf-compiler-grpc \
    libgtest-dev nlohmann-json3-dev
```

Verify all tools:

```bash
go version && docker --version && kind version && kubectl version --client && \
ghz --version && grpcurl --version && python3 --version && protoc --version
```

---

## 2. Clone & Build

### 2.1 Build Service Images

Each microservice is built as a Docker image:

```bash
cd ~/Project/Advanced\ Project   # or wherever the project is

# Build all 5 service images
for svc in gateway auth risk marketdata execution; do
    echo "Building $svc..."
    docker build --build-arg SERVICE=$svc -t latency-attribution-$svc:latest .
done
```

### 2.2 Build eBPF Image

The eBPF `rqdelay` tool runs as a DaemonSet:

```bash
docker build -f ebpf/Dockerfile -t latency-attribution-rqdelay:latest .
```

### 2.3 Generate Proto Files (if modified)

Only needed if you change `proto/order.proto`:

```bash
make proto
```

### 2.4 Build C++ HFT Components

```bash
# Build all three C++ binaries (loadgen, analyzer, matching engine)
make cpp-build

# Run unit tests (19 tests across 3 suites)
make cpp-test-quick
```

This produces:
- `cpp/build/loadgen/hft-loadgen` — HFT-grade gRPC load generator
- `cpp/build/analyzer/hft-analyzer` — Real-time kernel event analyzer
- `cpp/build/matching_engine/hft-execution` — C++ matching engine (drop-in for Go)

### 2.5 Verify All Images

```bash
docker images | grep latency-attribution
```

Expected output: 6 images (gateway, auth, risk, marketdata, execution, rqdelay).

---

## 3. Cluster Setup

### 3.1 Create Kind Cluster

```bash
kind create cluster --name latency-attribution --config deploy/kind-multinode.yaml
```
<!-- if exists then delete it : kind delete cluster --name latency-attribution
 -->
If `kind-multinode.yaml` doesn't exist, create it:

```yaml
# deploy/kind-multinode.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
```

### 3.2 Label Nodes for Cross-Node Experiments

This is **critical** — without labels, experiments E2, E4-E7, E9-E10, E14-E15 will fail (pods stuck in Pending):

```bash
# Get node names
kubectl get nodes

# Label worker as node-a, control-plane as node-b
kubectl label node latency-attribution-worker node-role=node-a
kubectl label node latency-attribution-control-plane node-role=node-b
```

### 3.3 Allow Scheduling on Control Plane

By default, Kind taints the control-plane. Remove it so cross-node experiments can schedule pods there:

```bash
kubectl taint nodes latency-attribution-control-plane \
    node-role.kubernetes.io/control-plane:NoSchedule-
```

### 3.4 Load Images into Kind

Kind uses its own container registry, so Docker images must be explicitly loaded:

```bash
kind load docker-image \
    latency-attribution-gateway:latest \
    latency-attribution-auth:latest \
    latency-attribution-risk:latest \
    latency-attribution-marketdata:latest \
    latency-attribution-execution:latest \
    latency-attribution-rqdelay:latest \
    --name latency-attribution
```

### 3.5 Verify Cluster

```bash
kubectl get nodes --show-labels | grep node-role
# Should show: node-role=node-a on worker, node-role=node-b on control-plane

kubectl cluster-info
```

---

## 4. Deploy Services

### 4.1 Deploy Base Configuration

```bash
kubectl apply -k deploy/base/
```

This deploys:
- **5 microservices**: gateway, auth, risk, marketdata, execution
- **Redis**: used by marketdata service
- **Jaeger**: distributed tracing collector
- **rqdelay DaemonSet**: eBPF kernel instrumentation

### 4.2 Wait for All Pods

```bash
kubectl -n latency-lab get pods -w
```

Wait until all pods show `Running` and `1/1` READY. This may take 1-2 minutes.

Expected output (8 pods):
```
auth-xxx          1/1   Running   0   ...
execution-xxx     1/1   Running   0   ...
gateway-xxx       1/1   Running   0   ...
jaeger-xxx        1/1   Running   0   ...
marketdata-xxx    1/1   Running   0   ...
redis-xxx         1/1   Running   0   ...
risk-xxx          1/1   Running   0   ...
rqdelay-xxx       1/1   Running   0   ...
```

---

## 5. Verify Pipeline

### 5.1 Test End-to-End gRPC Call

```bash
# Start port-forward
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3

# Send test request
grpcurl -plaintext \
    -d '{"order_id":"TEST-001","symbol":"AAPL","quantity":100,"price":150.25}' \
    -import-path proto -proto order.proto \
    localhost:50051 order.GatewayService/SubmitOrder

# Clean up
pkill -f "port-forward.*50051"
```

Expected: JSON response with `orderId`, `status`, and `latencyMs` fields.

### 5.2 Verify eBPF Metrics

```bash
kubectl -n latency-lab port-forward ds/rqdelay 9090:9090 &
sleep 3

# Check wakeup delay histogram
curl -s localhost:9090/metrics | grep rqdelay_bucket | head -5

# Check softirq tracking
curl -s localhost:9090/metrics | grep softirq | head -10

# Check p50/p99
curl -s localhost:9090/metrics | grep "rqdelay_p"

# Check per-cgroup (pod) delay metrics
curl -s localhost:9090/metrics | grep cgroup | head -10

# Check TCP retransmit tracking
curl -s localhost:9090/metrics | grep retransmit

pkill -f "port-forward.*9090"
```

Expected metrics:
- `rqdelay_bucket_count`, `rqdelay_p50_us`, `rqdelay_p99_us` — system-wide wakeup delay
- `rqdelay_softirq_time_ns`, `rqdelay_softirq_count` — per-vector softirq tracking
- `rqdelay_cgroup_p99_us`, `rqdelay_cgroup_samples` — per-pod (cgroup) wakeup delay
- `rqdelay_tcp_retransmit_total` — TCP retransmission events

### 5.3 Verify Tracing (Optional)

```bash
kubectl -n latency-lab port-forward svc/jaeger-svc 16686:16686 &
# Open http://localhost:16686 in browser
# Select service "gateway" and click "Find Traces"
```

---

## 6. Run Experiments

### 6.1 Experiment Matrix

| Exp | What It Tests | Duration |
|---|---|---|
| E0 | eBPF overhead (OTel disabled) | ~7 min |
| E1 | Baseline (floor latency) | ~7 min |
| E2 | Cross-node placement | ~7 min |
| E3a | CFS throttling 200m (tight) | ~7 min |
| E3b | CFS throttling 500m (moderate) | ~7 min |
| E4 | Noisy neighbor (stress-ng) | ~7 min |
| E5 | Throttling + cross-node | ~7 min |
| E6 | Contention + cross-node | ~7 min |
| E7 | Full stress (worst case) | ~7 min |
| E8 | hostNetwork benefit | ~7 min |
| E9 | hostNetwork under stress | ~7 min |
| E10 | Throttle + contention (same-node) | ~7 min |
| E11 | Network policy overhead | ~7 min |
| E12 | HPA autoscaling | ~7 min |
| E13 | CPU pinning mitigation | ~7 min |
| E14 | Pinning under stress + cross-node | ~7 min |
| E15 | Full isolation (best-case mitigation) | ~7 min |
| **E16** | **C++ matching engine** (replaces Go execution) | ~7 min |
| e2-cpu-contention | Extreme CPU limit 100m | ~7 min |
| e3-memory-pressure | Memory stress sidecar | ~7 min |
| e8-resource-limits | Resource limits 500m | ~7 min |

### 6.2 Run All Experiments (Automated)

The `run-all-experiments.sh` script runs all 20 experiments sequentially:

```bash
# Clean any previous data
rm -rf data/*/

# Run all experiments (survives terminal closure)
cd ~/Project/Advanced\ Project
nohup ./loadgen/run-all-experiments.sh 2000 > experiments.log 2>&1 &
echo "PID: $!"
```

**Parameters per experiment:**
- Rate: 2000 req/s
- Warmup: 30 seconds
- Measurement duration: 120 seconds
- Repetitions: 3 per experiment
- Cooldown: 60 seconds between runs

**Total time: ~2.5-3 hours**

### 6.3 Monitor Progress

```bash
# Live follow (Ctrl+C to stop watching)
tail -f experiments.log

# Quick status check
ls -d data/*/ 2>/dev/null | wc -l && echo "out of 20 experiments completed"

# See latest results
tail -30 experiments.log

# See which experiments completed
grep "completed\|FAILED" experiments.log
```

### 6.4 Run a Single Experiment

To run one experiment individually:

```bash
./loadgen/run-experiment.sh e1-baseline 2000
```

This will:
1. Deploy the overlay (`deploy/overlays/e1-baseline/`)
2. Wait for pods to be ready
3. Run 30s warmup
4. Run 3 × 120s measurement with 60s cooldowns
5. Save JSON results to `data/e1-baseline/`
6. Restore base deployment

### 6.5 Stop Experiments

```bash
pkill -f run-all-experiments
pkill -f run-experiment
pkill -f ghz
pkill -f port-forward

# Restore base deployment
kubectl apply -k deploy/base/
```

---

## 7. Analyze & Plot

### 7.1 Run Analysis

After all experiments complete:

```bash
python3 analysis/scripts/analyze_all.py
```

This produces:
- `data/all_experiments_summary.csv` — unified CSV with all latency percentiles
- Console output with validation against baseline and hypothesis evaluation

### 7.2 Run Statistical Analysis

```bash
python3 analysis/scripts/statistical_analysis.py
```

This performs:
- **Mann-Whitney U tests** against baseline (E1) for each experiment
- **Cliff's delta** effect size calculations
- **Formal hypothesis evaluation** (H1–H4) with dose-response and mitigation analysis
- Output: `analysis/stats/statistical_tests.csv`

### 7.3 Generate Plots

#### Step 1 — Basic 3-plot summary
```bash
python3 analysis/scripts/plot_results.py
```

#### Step 2 — Core 10-figure evidence suite (Blueprint §06.6)
```bash
python3 analysis/scripts/plot_evidence.py
```

#### Step 3 — eBPF correlation plots (Figs 2, 5, 9)
First sample eBPF metrics per experiment (runs a 20s ghz burst per overlay):
```bash
# Start rqdelay port-forward (keep open during sampling)
kubectl -n latency-lab port-forward ds/rqdelay 9090:9090 &
sleep 3
./scripts/sample-ebpf-metrics.sh   # ~18 min for 20 experiments
```
Then generate the correlation scatter plots:
```bash
python3 analysis/scripts/plot_ebpf_correlation.py
```

#### Step 4 — Per-hop Jaeger decomposition (Fig 10)
```bash
# Requires Jaeger port-forward
kubectl -n latency-lab port-forward svc/jaeger-svc 16686:16686 &
sleep 3
# Send some traffic first if Jaeger has no traces:
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
ghz --insecure --proto proto/order.proto --call order.GatewayService.SubmitOrder \
  -d '{"order_id":"T","symbol":"AAPL","quantity":100,"price":150.25}' \
  --rps 50 --duration 30s localhost:50051
# Then plot:
python3 analysis/scripts/plot_jaeger_hops.py
```

#### Step 5 — Burst response plot (Fig 8)
```bash
# Run burst load generator from project root
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3
go run ./loadgen/burst/ --duration 60s --warmup 10s --output data/burst-results.csv
python3 analysis/scripts/plot_burst.py
```

### 7.4 Complete Plot List

All plots saved to `analysis/plots/`:

| File | Figure | Script |
|---|---|---|
| `latency_comparison.png` | p50/p99 bar — all experiments | `plot_results.py` |
| `p99_waterfall.png` | p99 as multiple of baseline | `plot_results.py` |
| `mitigation_comparison.png` | Before/after mitigations | `plot_results.py` |
| `fig1_p99_bar_chart.png` | Fig 1: p99 across experiments | `plot_evidence.py` |
| `fig2_ebpf_wakeup_vs_app_p99.png` | Fig 2: eBPF wakeup delay vs app p99 | `plot_ebpf_correlation.py` |
| `fig3_cdf_overlay.png` | Fig 3: CDF overlay E1/E3/E7/E15 | `plot_evidence.py` |
| `fig4_cfs_throttle_timeline.png` | Fig 4: CFS throttle timeline | generated during E3a run |
| `fig5_softirq_vs_wakeup.png` | Fig 5: softirq vs wakeup delay | `plot_ebpf_correlation.py` |
| `fig6_crossnode_effect.png` | Fig 6: Cross-node SN vs XN | `plot_evidence.py` |
| `fig7_mitigation_waterfall.png` | Fig 7: E7→E15 waterfall | `plot_evidence.py` |
| `fig8_burst_response.png` | Fig 8: Burst response time-series | `plot_burst.py` |
| `fig9_retransmit_vs_p99.png` | Fig 9: TCP retransmit vs p99 | `plot_ebpf_correlation.py` |
| `fig10b_per_hop_decomposition.png` | Fig 10: Per-hop stacked bar | `plot_jaeger_hops.py` |
| `fig10c_per_hop_cdf.png` | Fig 10c: Per-service CDF | `plot_jaeger_hops.py` |
| `fig_per_request_correlation.png` | Per-request kernel signal scatter | `per_request_correlation.py` |
| `fig_per_request_heatmap.png` | Per-request Spearman ρ heatmap | `per_request_correlation.py` |
| `fig_spike_detection.png` | Sliding-window spike detection | `spike_detection.py` |
| `fig_spike_summary.png` | Spike percentage summary | `spike_detection.py` |
| `fig_chi_squared_throttle.png` | Throttle × spike independence | `chi_squared_throttle.py` |
| `fig_per_signal_heatmap.png` | Per-signal Mann-Whitney heatmap | `per_signal_mannwhitney.py` |
| `fig_case_control_latency.png` | Case/control latency distributions | `case_control_analysis.py` |
| `fig_windowed_correlation.png` | 100ms windowed Spearman ρ | `windowed_correlation.py` |

### 7.5 View Plots

```bash
# Linux with GUI
xdg-open analysis/plots/fig1_p99_bar_chart.png

# Copy all to Desktop
cp analysis/plots/*.png ~/Desktop/
```

### 7.6 CFS Throttling Data (alongside experiment)

Run the CFS collector **in parallel** with an experiment to capture throttle timeline (Fig 4):

```bash
# Start CFS collector in background
./scripts/cfs-stats-collector.sh data/e3a-cfs-tight/cfs-stats.csv 200 &
CFS_PID=$!

# Run experiment
./loadgen/run-experiment.sh e3a-cfs-tight 2000

# Stop collector
kill $CFS_PID
echo "Lines: $(wc -l < data/e3a-cfs-tight/cfs-stats.csv)"
```

### 7.7 Advanced Analysis Scripts (Blueprint §06)

After the core analysis (Steps 1-5), run these additional scripts for deeper analysis:

```bash
# Per-request kernel signal correlation (§06.4)
# Correlates individual requests with 6 concurrent kernel signals in 100ms windows
python3 analysis/scripts/per_request_correlation.py

# Spike detection with sliding-window p999 (§06.3)
python3 analysis/scripts/spike_detection.py

# Chi-squared test for throttle × spike association (§06.5)
python3 analysis/scripts/chi_squared_throttle.py

# Per-signal case/control Mann-Whitney attribution (§06.4)
python3 analysis/scripts/per_signal_mannwhitney.py

# Case/control request analysis (>p99 vs median groups) (§06.4)
python3 analysis/scripts/case_control_analysis.py

# 100ms windowed Spearman/Pearson correlation (§06.2)
python3 analysis/scripts/windowed_correlation.py

# Collect real /proc kernel metrics + derive per-experiment signals
python3 analysis/scripts/collect_kernel_metrics.py

# Add data source watermarks to all plots (requires Pillow)
pip3 install Pillow
python3 analysis/scripts/add_plot_watermarks.py
```

**Output files:**
- `analysis/stats/per_request_correlation.csv` — Spearman ρ per experiment × signal
- `analysis/stats/spike_detection.csv` — per-window spike/calm classification
- `analysis/stats/chi_squared_throttle.csv` — chi-squared test results
- `analysis/stats/per_signal_mannwhitney.csv` — per-signal effect sizes
- `analysis/stats/case_control_results.csv` — case/control ratios
- `analysis/stats/windowed_correlation.csv` — 100ms windowed correlation
- `data/kernel_metrics_source.csv` — full kernel signal provenance chain

### 7.8 Interactive Jupyter Notebooks

Three notebooks are available for interactive exploration:

```bash
pip3 install jupyter
cd analysis/notebooks/
jupyter notebook
```

| Notebook | Purpose |
|---|---|
| `01_experiment_explorer.ipynb` | CDF comparison, percentile tables, per-experiment deep dive |
| `02_correlation_analysis.ipynb` | Windowed data explorer, spike detection, case/control |
| `03_mitigation_report.ipynb` | Mitigation waterfall, before/after CDF, signal attribution |

---

## 8. C++ HFT Components

Three C++ (C++20) components extend the project with HFT-grade systems programming:

### 8.1 Load Generator (`hft-loadgen`)

Replaces `ghz` with a custom C++ gRPC load generator featuring RDTSC nanosecond timestamping, lock-free SPSC ring buffers, and zero-allocation HDR histograms.

```bash
# Port-forward the gateway
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3

# Run with HFT-grade instrumentation
./cpp/build/loadgen/hft-loadgen \
    --target localhost:50051 \
    --rate 2000 \
    --duration 120 \
    --warmup 30 \
    --workers 4 \
    --output data/e1-baseline/hft-run1.json \
    --verbose
```

The output JSON is **ghz-compatible** — existing analysis scripts work unchanged:
```bash
python3 -c "import json; d=json.load(open('data/e1-baseline/hft-run1.json')); print('p99:', d['latencyDistribution'])"
```

### 8.2 Kernel Event Analyzer (`hft-analyzer`)

Real-time ANSI terminal dashboard that consumes eBPF metrics from the `rqdelay` Prometheus endpoint and computes Spearman/Pearson correlation between kernel scheduling delay and application p99 latency.

```bash
# Port-forward rqdelay metrics
kubectl -n latency-lab port-forward ds/rqdelay 9090:9090 &
sleep 3

# Run with live dashboard (60 seconds)
./cpp/build/analyzer/hft-analyzer \
    --metrics http://localhost:9090/metrics \
    --interval 1 \
    --duration 60 \
    --csv /tmp/ebpf-analysis.csv
```

The dashboard shows:
- **Wakeup-to-Run delay**: p50/p99 with sparkline histogram
- **Softirq interference**: NET_RX/NET_TX time
- **TCP retransmits**: Running counter
- **Correlation**: Spearman ρ and Pearson r between wakeup delay and app p99

### 8.3 Matching Engine (`hft-execution`)

Drop-in replacement for the Go execution service with a C++ matching engine featuring 64-byte cache-line aligned orders, O(1) slab memory pool, and price-time priority order book.

```bash
# Run standalone (for testing)
./cpp/build/matching_engine/hft-execution --port 50055 &

# Test with grpcurl
grpcurl -plaintext -import-path proto -proto order.proto \
    -d '{"order_id":"TEST-1","symbol":"AAPL","quantity":100,"price":150.25}' \
    localhost:50055 order.ExecutionService/Execute
```

### 8.4 E16: Deploy C++ Matching Engine on K8s

```bash
# Build Docker image
docker build -t latency-attribution-cpp-execution -f cpp/matching_engine/Dockerfile .

# Load into Kind cluster
kind load docker-image latency-attribution-cpp-execution:latest --name latency-attribution

# Deploy — replaces Go execution with C++ matching engine
kubectl apply -k deploy/overlays/e16-cpp-execution/

# Verify pods
kubectl -n latency-lab get pods | grep execution

# Run experiment with C++ loadgen against C++ execution
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3
./cpp/build/loadgen/hft-loadgen \
    --target localhost:50051 \
    --rate 2000 \
    --duration 120 \
    --output data/e16-cpp-execution/hft-run1.json

# Restore Go execution
kubectl apply -k deploy/base/
```

### 8.5 Unit Tests

```bash
# Quick test (direct g++ compilation)
make cpp-test-quick

# Or via CMake/CTest
make cpp-test
```

19 tests across 3 suites:
- **SpscRingTest** (5): Push/pop, full ring, wraparound, batch drain, concurrent correctness
- **HdrHistogramTest** (6): Empty state, percentiles, high values, distribution, reset
- **OrderBookTest** (8): Order alignment, slab alloc/free, pool exhaustion, matching, partial fills, price-time priority

---

## 9. Troubleshooting

### Pods Stuck in Pending

```bash
kubectl -n latency-lab describe pod <pod-name>
```

Common causes:
- **Missing node labels**: Run `kubectl label node <name> node-role=node-a`
- **Control-plane taint**: Run `kubectl taint nodes <name> node-role.kubernetes.io/control-plane:NoSchedule-`
- **Insufficient resources**: Check with `kubectl top nodes`

### Images Not Found (ErrImagePull)

```bash
# Reload images into Kind
kind load docker-image latency-attribution-<service>:latest --name latency-attribution
```

### ghz Connection Refused

```bash
# Check if gateway pod is running
kubectl -n latency-lab get pods | grep gateway

# Check port-forward
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3
curl -v localhost:50051  # should connect (even if HTTP error)
```

### eBPF rqdelay CrashLoopBackOff

```bash
kubectl -n latency-lab logs ds/rqdelay
```

Common causes:
- **Kernel too old**: eBPF tracepoints require kernel 5.15+
- **Missing privileges**: The rqdelay DaemonSet needs `privileged: true` and `hostPID: true`

### Kind Cluster Won't Start

```bash
# Delete and recreate
kind delete cluster --name latency-attribution
kind create cluster --name latency-attribution --config deploy/kind-multinode.yaml
```

### Experiments Take Too Long

Reduce repetitions or duration by editing `loadgen/run-experiment.sh`:

```bash
DURATION="60s"     # default: 120s
REPETITIONS=1      # default: 3
COOLDOWN=30        # default: 60
```

### Path Issues (Apostrophes in Directory Names)

If your project path contains special characters (like `Don't Delete`), escape them:

```bash
cd ~/Project\(Don\'t\ Delete\)/Advanced\ Project
# OR use quotes:
cd "$HOME/Project(Don't Delete)/Advanced Project"
```

### C++ Build Fails with `-mrdtscp`

If you see `error: unrecognized command-line option '-mrdtscp'`, your GCC doesn't support the flag. The fix is already applied — just run `make cpp-clean && make cpp-build`.

### C++ Docker Build Fails (CMakeCache Conflict)

If Docker build shows `CMakeCache.txt directory mismatch`:
```bash
# Ensure .dockerignore excludes cpp/build/
echo "cpp/build/" >> .dockerignore
docker build -t latency-attribution-cpp-execution -f cpp/matching_engine/Dockerfile .
```

---

## Directory Structure Reference

```
Advanced Project/
├── blueprint/           # 11 design documents (00-10)
├── proto/               # gRPC service definitions
│   ├── order.proto
│   └── orderpb/         # Generated Go code
├── services/            # 5 microservices
│   ├── gateway/         # Entry point — port 50051
│   ├── auth/            # Authentication service
│   ├── risk/            # Risk assessment
│   ├── marketdata/      # Market data (uses Redis)
│   └── execution/       # Order execution (terminal service)
├── pkg/                 # Shared Go packages
│   ├── delay/           # Busy-spin delay (NOT time.Sleep)
│   └── tracing/         # OpenTelemetry setup
├── ebpf/                # eBPF kernel instrumentation
│   ├── src/rqdelay.bpf.c   # BPF program: sched+softirq+cgroup(wakeup-time)+tcp
│   ├── cmd/main.go          # Go runner + Prometheus export (:9090)
│   └── Dockerfile
├── cpp/                 # C++ HFT components (C++20)
│   ├── loadgen/             # hft-loadgen: RDTSC, SPSC ring, HDR histogram
│   │   ├── timestamp.h      #   RDTSC timestamping with calibration
│   │   ├── spsc_ring.h      #   Lock-free SPSC ring buffer
│   │   ├── hdr_histogram.h  #   Zero-allocation HDR histogram
│   │   ├── grpc_worker.*    #   gRPC worker threads
│   │   ├── stats_collector.*#   Ring consumer + percentile stats
│   │   ├── json_output.*    #   ghz-compatible JSON output
│   │   └── main.cpp         #   CLI entry point
│   ├── analyzer/            # hft-analyzer: live eBPF dashboard
│   │   ├── bpf_map_reader.* #   Raw socket Prometheus consumer
│   │   ├── correlation_engine.h # Spearman/Pearson correlation
│   │   ├── terminal_dashboard.h # ANSI live display
│   │   └── main.cpp         #   Event loop
│   ├── matching_engine/     # hft-execution: C++ matching engine
│   │   ├── order.h          #   64-byte cache-line aligned struct
│   │   ├── memory_pool.h    #   O(1) slab allocator
│   │   ├── order_book.h     #   Price-time priority order book
│   │   ├── matching_engine.h#   Multi-symbol engine
│   │   ├── grpc_server.*    #   ExecutionService gRPC server
│   │   ├── main.cpp         #   Server entry point
│   │   └── Dockerfile       #   Multi-stage C++ Docker build
│   ├── tests/               # GTest unit tests (19 tests)
│   └── CMakeLists.txt       # Top-level CMake build
├── deploy/
│   ├── base/            # Core K8s manifests
│   ├── overlays/        # 21 experiment configurations (E0-E16 + extras)
│   ├── kind-cluster.yaml
│   └── kind-multinode.yaml
├── loadgen/
│   ├── run-experiment.sh      # Per-experiment runner
│   ├── run-all-experiments.sh # Full matrix automation
│   ├── run-baseline.sh
│   └── burst/main.go          # Burst-mode: 1000 rps + 5000 rps/50ms bursts
├── analysis/
│   ├── scripts/
│   │   ├── analyze_all.py           # Parse + validate + CSV
│   │   ├── plot_results.py          # Basic 3-plot summary
│   │   ├── plot_evidence.py         # Core 10-figure evidence suite
│   │   ├── plot_ebpf_correlation.py # Figs 2,5,9: eBPF×ghz scatter plots
│   │   ├── plot_jaeger_hops.py      # Fig 10: per-hop Jaeger decomposition
│   │   ├── plot_burst.py            # Fig 8: burst response time-series
│   │   ├── statistical_analysis.py  # Mann-Whitney U + hypothesis eval
│   │   └── parse_ghz.py             # ghz JSON parser
│   ├── stats/                  # Statistical test results (CSV)
│   └── plots/                  # Generated PNG files (14 plots)
├── scripts/
│   ├── smoke-test.sh           # Local pipeline test
│   ├── cfs-stats-collector.sh  # CFS throttling poller (run alongside experiment)
│   └── sample-ebpf-metrics.sh  # eBPF metric sampler per experiment overlay
├── data/                       # Experiment results (JSON + CSV)
│   ├── all_experiments_summary.csv
│   ├── ebpf_per_experiment.csv
│   └── burst-results.csv
├── Dockerfile                  # Service build
├── Makefile                    # Build automation (Go + C++ targets)
└── go.mod / go.sum             # Go module
```

---

## Quick Start (TL;DR)

```bash
# 1. Build images
for svc in gateway auth risk marketdata execution; do
    docker build --build-arg SERVICE=$svc -t latency-attribution-$svc:latest .
done
docker build --no-cache -f ebpf/Dockerfile -t latency-attribution-rqdelay:latest .

# 2. Build C++ components
make cpp-build && make cpp-test-quick

# 3. Create cluster
kind create cluster --name latency-attribution --config deploy/kind-multinode.yaml

# 4. Setup cluster
kubectl label node latency-attribution-worker node-role=node-a
kubectl label node latency-attribution-control-plane node-role=node-b
kubectl taint nodes latency-attribution-control-plane node-role.kubernetes.io/control-plane:NoSchedule-
kind load docker-image latency-attribution-{gateway,auth,risk,marketdata,execution,rqdelay}:latest --name latency-attribution

# 5. Deploy
kubectl apply -k deploy/base/
kubectl -n latency-lab get pods -w  # wait for all Running

# 6. Run all experiments (~3 hours)
nohup ./loadgen/run-all-experiments.sh 2000 > experiments.log 2>&1 &

# 7. Or use C++ load generator
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
./cpp/build/loadgen/hft-loadgen --target localhost:50051 --rate 2000 --duration 120 --output results.json

# 8. Analyze & plot (core)
python3 analysis/scripts/analyze_all.py
python3 analysis/scripts/statistical_analysis.py
python3 analysis/scripts/plot_results.py
python3 analysis/scripts/plot_evidence.py

# 9. eBPF correlation plots (Figs 2, 5, 9)
kubectl -n latency-lab port-forward ds/rqdelay 9090:9090 &
sleep 3 && ./scripts/sample-ebpf-metrics.sh
python3 analysis/scripts/plot_ebpf_correlation.py

# 10. Per-hop + burst plots (Figs 8, 10)
kubectl -n latency-lab port-forward svc/jaeger-svc 16686:16686 &
python3 analysis/scripts/plot_jaeger_hops.py
kubectl -n latency-lab port-forward svc/gateway-svc 50051:50051 &
sleep 3 && go run ./loadgen/burst/ --duration 60s --output data/burst-results.csv
python3 analysis/scripts/plot_burst.py
```
