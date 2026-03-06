# Kernel-Level Tail Latency Attribution in Network-Heavy RPC Pipelines
## Research Project Blueprint — Complete Overview

> **This project is fundamentally about Linux kernel scheduling, softirq processing,
> and CFS throttling** — the three mechanisms that dominate tail latency in
> network-heavy RPC pipelines. Kubernetes is used **only as an experimental harness**
> to create reproducible cgroup isolation, CPU quota, and placement scenarios.
> The flagship contribution is **`rqdelay`**, a libbpf CO-RE eBPF tool that
> captures per-pod scheduling delay, softirq interference, and throttle events with
> production-grade fidelity.

> **Thesis**: Tail latency inflation in network-heavy RPC pipelines is dominated by
> wakeup-to-run scheduling delay caused by softirq/ksoftirqd CPU competition and
> cgroup CPU throttling, especially under cross-node placement and CPU contention.
> CPU/IRQ isolation and quota-aware tuning can significantly reduce p999 latency.

---

## Document Map

| File | Contents |
|------|----------|
| [01_problem_objectives_outcomes.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/01_problem_objectives_outcomes.md) | Problem statement, objectives, proposed outcomes |
| [02_rpc_pipeline.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/02_rpc_pipeline.md) | RPC pipeline design, call graph, deployment |
| [03_experiment_matrix.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/03_experiment_matrix.md) | Configuration matrix (≥12 experiments) |
| [04_measurement_plan.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/04_measurement_plan.md) | App-level, kernel, and network metrics |
| [05_ebpf_instrumentation.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/05_ebpf_instrumentation.md) | eBPF tooling, tracepoints, pod-aware mapping |
| [06_correlation_analysis.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/06_correlation_analysis.md) | Correlation plan and analysis workflow |
| [07_hypotheses.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/07_hypotheses.md) | Falsifiable hypotheses (≥3) |
| [08_mitigations.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/08_mitigations.md) | Mitigations & optimizations (≥4, HFT-relevant) |
| [09_timeline_risks.md](file:///home/iiitd/Desktop/Advanced%20Project/blueprint/09_timeline_risks.md) | Timeline, MVP, stretch goals, risks |

---

## Project at a Glance

```mermaid
graph LR
    A["Week 1-2: Infra + Pipeline"] --> B["Week 3: eBPF Tools"]
    B --> C["Week 4-5: Experiments"]
    C --> D["Week 6: Analysis"]
    D --> E["Week 7: Mitigations"]
    E --> F["Week 8: Report + Polish"]
```

### One-Line Summary
Build a 5-service gRPC pipeline on Kubernetes, instrument it with CO-RE eBPF probes
that capture per-pod scheduling delay / softirq time / throttle events, validate
instrumentation overhead (E0), run a 16-cell experiment matrix varying placement ×
CPU limits × contention × networking mode, and prove that wakeup-to-run delay drives
p999 inflation — then remove it with isolation/pinning mitigations.

---

## Repository Structure (Target)

```
latency-attribution/
├── services/           # Go gRPC microservices (gateway, auth, risk, mdata, exec)
├── proto/              # Protobuf definitions
├── deploy/
│   ├── base/           # Kustomize base manifests
│   ├── overlays/       # Per-experiment overlays (E1–E15)
│   └── scripts/        # Cluster setup, experiment runner
├── ebpf/
│   ├── src/            # libbpf CO-RE C programs
│   ├── cmd/            # Go loader / exporter
│   └── bpftrace/       # Rapid-prototyping scripts
├── loadgen/            # ghz / custom gRPC load generator wrapper
├── analysis/
│   ├── notebooks/      # Jupyter analysis
│   ├── scripts/        # Data pipeline (parse, align, aggregate)
│   └── plots/          # Generated figures
├── data/               # Raw + processed experiment data
├── docs/               # Final report (LaTeX), README
└── Makefile            # Top-level orchestration
```
