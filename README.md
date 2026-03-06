# Kernel-Level Tail Latency Attribution in RPC Pipelines

A comprehensive study of tail latency causes and mitigations in Kubernetes-hosted gRPC microservice pipelines, using eBPF kernel instrumentation.

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
Client → Gateway → Auth → Risk → MarketData (Redis) → Execution
              ↕                    ↕
         Jaeger Tracing      eBPF rqdelay (kernel)
```

- **5 gRPC microservices** (Go 1.24) with OpenTelemetry tracing
- **eBPF instrumentation** (`rqdelay`) tracking `sched_wakeup`, `sched_switch`, `softirq`, `tcp_retransmit`
- **20 experiment configurations** via Kustomize overlays
- **Statistical analysis** with Mann-Whitney U tests and Cliff's delta

## Quick Start

```bash
# Build images
for svc in gateway auth risk marketdata execution; do
    docker build --build-arg SERVICE=$svc -t latency-attribution-$svc:latest .
done
docker build -f ebpf/Dockerfile -t latency-attribution-rqdelay:latest .

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

# Analyze
python3 analysis/scripts/analyze_all.py
python3 analysis/scripts/plot_evidence.py
python3 analysis/scripts/statistical_analysis.py
```

See [RUN_GUIDE.md](RUN_GUIDE.md) for complete instructions.

## Project Structure

```
├── blueprint/           # 10 design documents
├── services/            # 5 gRPC microservices (Go)
├── proto/               # Protocol Buffer definitions
├── pkg/                 # Shared Go packages (delay, tracing)
├── ebpf/                # eBPF kernel instrumentation (rqdelay)
├── deploy/
│   ├── base/            # Core Kubernetes manifests
│   └── overlays/        # 20 experiment configurations
├── loadgen/             # Load generation scripts (ghz)
├── analysis/
│   ├── scripts/         # Python analysis & plotting
│   ├── plots/           # Generated figures (20 plots)
│   └── stats/           # Statistical test results
├── data/                # Experiment results (JSON + CSV)
├── docs/                # Additional documentation
├── FINDINGS.md          # Hypothesis verification results
└── RUN_GUIDE.md         # Complete execution guide
```

## Requirements

- Go 1.24+, Docker, Kind, kubectl, ghz, Python 3.10+
- See [RUN_GUIDE.md](RUN_GUIDE.md) for full prerequisites

## License

Academic project — Kernel-Level Tail Latency Attribution.
