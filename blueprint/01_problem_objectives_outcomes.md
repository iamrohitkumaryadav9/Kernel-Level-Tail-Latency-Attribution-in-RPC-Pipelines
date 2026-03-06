# 1. Problem Statement, Objectives & Proposed Outcomes

## 1.1 Problem Statement

In latency-critical distributed systems — exemplified by high-frequency trading order
pipelines — end-to-end tail latency (p99/p999) is the primary performance constraint.
A single slow hop in a multi-service RPC chain amplifies end-to-end delay: even
small per-hop tail probability compounds across hops, making it increasingly likely
that at least one hop hits a tail event on any given request. This "tail at scale"
effect is well-documented (Dean & Barroso, 2013), but the *kernel-level mechanisms*
that produce individual hop tail events remain poorly attributed in containerized
deployments.

We identify three interacting kernel mechanisms that dominate hop-level tail spikes
under network-heavy workloads in cgroup-constrained environments:

1. **Wakeup-to-run scheduling delay.** When a network packet arrives for a sleeping
   application thread, the thread is woken via `sched_wakeup`, but may wait tens to
   hundreds of microseconds on the CPU runqueue before `sched_switch` grants it
   execution. Under CPU contention this delay directly inflates per-hop latency.

2. **softirq/ksoftirqd CPU competition.** Network packet processing (NET_RX/NET_TX)
   executes in softirq context, either inline after the hardware IRQ or deferred to
   the `ksoftirqd` kernel thread when the softirq budget is exceeded. Both paths
   steal CPU cycles from application threads on the same core. When `ksoftirqd`
   activates, it competes with application threads in the CFS scheduler, creating
   scheduling interference that is invisible to application-level tracing.

3. **CFS bandwidth throttling.** Kubernetes CPU limits translate to CFS quota/period
   settings in cgroup v2. When a container exhausts its quota within a period, all
   threads in the cgroup are throttled — blocked from scheduling — until the next
   period boundary. Under bursty network traffic, a thread may wake up during a
   throttled window and suffer millisecond-scale delay that dominates the tail.

These three mechanisms interact: network bursts trigger softirq storms that inflate
runqueue depth; the resulting CPU consumption accelerates CFS quota exhaustion; and
throttling then blocks both application threads and `ksoftirqd`, creating a
positive-feedback loop that produces correlated tail spikes across pipeline hops.
Cross-node pod placement adds network serialization and additional kernel-to-kernel
scheduling hops, further amplifying the effect.

This project designs and executes a controlled experimental study to **quantify the
individual and combined contribution** of these mechanisms to p999 latency inflation
in a 5-hop gRPC pipeline, and to **validate mitigations** (CPU pinning, IRQ
isolation, quota tuning) that break the feedback loop.

---

## 1.2 Objectives

| # | Objective | Metric(s) | Measurement Method | Success Criterion |
|---|-----------|-----------|-------------------|-------------------|
| O1 | Quantify end-to-end and per-hop p99/p999 under baseline vs. contention | p50/p90/p99/p999 latency (µs) | gRPC client histograms + OTel span durations | p999 under contention ≥ 3× baseline; per-hop breakdown accounts for ≥ 90% of e2e |
| O2 | Attribute scheduling delay to tail spikes | wakeup-to-run delay (µs), effect size (Cliff's d) | eBPF `sched_wakeup` → `sched_switch` delta, per-pod; case/control comparison + Spearman correlation | Cliff's d ≥ 0.5 (large effect) between slow-request and fast-request wakeup delay; significant case/control separation (Mann-Whitney p < 0.01) |
| O3 | Attribute softirq/ksoftirqd interference to scheduling delay | NET_RX softirq time (µs/s), ksoftirqd runqueue occupancy | eBPF `softirq_entry`/`softirq_exit`, `sched_wakeup` for ksoftirqd PIDs | Spike windows show ≥ 2× softirq time vs. calm windows; ksoftirqd wakeup delay correlates with app wakeup delay |
| O4 | Attribute CFS throttling to tail spikes | `nr_throttled`, `throttled_usec` | cgroup v2 `cpu.stat` polling (100ms), delta-aligned with p999 windows | Throttle events co-occur with ≥ 70% of p999 spike windows in quota-limited experiments |
| O5 | Demonstrate cross-node amplification | e2e p999 ratio (cross-node / same-node) | Same workload, placement varied | Cross-node p999 ≥ 1.5× same-node p999 under contention |
| O6 | Validate mitigations with measurable improvement | p999 reduction (%) | Before/after comparison with identical load | Best mitigation achieves ≥ 40% p999 reduction vs. contention baseline |
| O7 | Ensure reproducibility | coefficient of variation (CV) across 3 runs | Repeat each experiment 3× | CV ≤ 15% for p99 across runs |

---

## 1.3 Proposed Outcomes (Deliverable Artifacts)

| # | Artifact | Description |
|---|----------|-------------|
| A1 | **Reproducible Kubernetes deployment** | Kustomize overlays for all 15 experiments. `make deploy-E1` style targets. Includes cluster setup scripts (kubeadm/kind), node labeling, CPU manager config. |
| A2 | **5-service gRPC pipeline** | Go services: Gateway → Auth → Risk → MarketData (Redis) → Execution. Instrumented with OTel tracing. Configurable processing delay and payload size. |
| A3 | **`rqdelay`: flagship eBPF tool** | libbpf CO-RE binary. Attaches to `sched_wakeup`, `sched_switch`, `softirq_entry/exit`, cgroup throttle events. Outputs per-pod scheduling delay histograms, softirq time series, throttle event log. Exports Prometheus metrics + raw event log. |
| A4 | **Experiment runner** | Shell/Make orchestration: deploy overlay → warm up → run load → collect traces → tear down. Supports sweep mode for full matrix. |
| A5 | **Pod-aware kernel event correlation pipeline** | Python scripts: parse eBPF event logs + OTel traces + cgroup stats → aligned time-series DataFrame → spike-window extraction → case/control analysis. |
| A6 | **Analysis plots + dashboard** | Matplotlib/Seaborn publication-quality figures: p999 vs. wakeup delay scatter, CDF overlays, heatmaps, throttle timeline. Optional Grafana dashboard (secondary). |
| A7 | **Dataset + traces** | Raw experiment data: gRPC latency histograms, OTel JSON traces, eBPF event logs, cgroup stats CSVs, node metrics. Published with experiment metadata for reproducibility. |
| A8 | **Final report** | LaTeX document structured as: Abstract, Introduction, Background, Methodology, Experimental Setup, Results, Mitigation Evaluation, Discussion, Threats to Validity, Conclusion. Target: 12–15 pages, NSDI/SOSP style. |
