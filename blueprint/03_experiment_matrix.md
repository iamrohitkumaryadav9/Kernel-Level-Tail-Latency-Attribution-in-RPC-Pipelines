# 3. Configuration Matrix (15 Experiments)

## 3.1 Factor Space

| Factor | Levels | Mechanism Isolated |
|--------|--------|--------------------|
| **Pod placement** | Same-node (SN) / Cross-node (XN) | Network serialization, extra kernel scheduling hops |
| **CPU limits** | None (unlimited) / Tight (200m) / Moderate (500m) | CFS bandwidth throttling |
| **CPU contention** | None / Background `stress-ng` (4 CPU-bound workers) | Runqueue depth, scheduling delay |
| **Network mode** | Default pod networking / `hostNetwork: true` | veth overhead, CNI processing |
| **Protocol** | gRPC (HTTP/2) / HTTP/1.1 via gRPC-Gateway | Head-of-line blocking, connection reuse |

## 3.2 Experiment Matrix

| Exp | Placement | CPU Limit | Contention | Net Mode | Protocol | Primary Mechanism |
|-----|-----------|-----------|------------|----------|----------|-------------------|
| **E0** | SN | None | None | Pod | gRPC | **Instrumentation overhead** — validates < 2% eBPF impact |
| **E1** | SN | None | None | Pod | gRPC | **Baseline** — floor latency |
| **E2** | XN | None | None | Pod | gRPC | **Cross-node serialization** |
| **E3a** | SN | 200m | None | Pod | gRPC | **CFS throttling — tight** |
| **E3b** | SN | 500m | None | Pod | gRPC | **CFS throttling — moderate** |
| **E4** | SN | None | stress-ng | Pod | gRPC | **Runqueue contention (isolated)** |
| **E5** | XN | 200m | None | Pod | gRPC | **Throttling + cross-node** |
| **E6** | XN | None | stress-ng | Pod | gRPC | **Contention + cross-node** |
| **E7** | XN | 200m | stress-ng | Pod | gRPC | **Full stress** (worst case) |
| **E8** | SN | None | None | Host | gRPC | **hostNetwork benefit** |
| **E9** | XN | None | stress-ng | Host | gRPC | **hostNetwork under stress** |
| **E10** | SN | 200m | stress-ng | Pod | gRPC | **Throttle + contention (same-node)** |
| **E11** *(optional)* | SN | None | None | Pod | HTTP/1.1 | **Protocol comparison baseline** |
| **E12** *(optional)* | XN | 200m | stress-ng | Pod | HTTP/1.1 | **Protocol under full stress** |

> **E11–E12** are optional/stretch. Protocol comparison is not the core mechanism
> under study. Prioritize E1–E10 + E13–E15 first. Cut E11/E12 if time slips.
| **E13** | SN | Pinned | None | Pod | gRPC | **Mitigation: CPU pinning** |
| **E14** | XN | Pinned | stress-ng | Pod | gRPC | **Mitigation: pinning under stress** |
| **E15** | XN | Tuned | stress-ng | Host+IRQ | gRPC | **Mitigation: full isolation** |
| **E16** | SN | None | None | Pod | gRPC | **C++ matching engine** — drop-in replacement for Go execution via `hft-execution` |

> **E13–E15** are mitigation experiments. **E0** is the instrumentation overhead
> control. **E16** replaces the Go execution service with a C++ matching engine
> (64-byte cache-line orders, slab allocator, price-time priority) to measure
> execution path latency improvement.

## 3.3 Per-Experiment Detail Cards

### E0 — Instrumentation Overhead Validation

- **Purpose**: Prove that eBPF instrumentation does not bias results.
- **Method**: Run identical E1 workload twice — once **without** rqdelay, once **with**.
- **Success criterion**: Instrumented p999 ≤ 1.02× uninstrumented p999 (< 2% overhead).
- **Report section**: Document as "Measurement Overhead" in final report. If overhead > 2%, reduce sampling rates before proceeding.
- **Mechanism isolated**: None (control experiment). Must pass before running E1–E15.

### E1 — Baseline (Same-Node, No Limits, No Contention, Pod Network, gRPC)

- **Independent variables**: None varied (control)
- **Dependent metrics**: e2e p50/p90/p99/p999, per-hop span duration, wakeup-to-run delay, softirq time, throttle events
- **Expected result**: Low, stable latency (exact floor depends on hardware — bare metal vs VM). Minimal scheduling delay. Zero throttle events. This run defines the baseline floor against which all other experiments are compared.
- **Mechanism isolated**: Floor measurement. All other experiments compare against this.

### E2 — Cross-Node Placement

- **IV**: Placement (XN: Gateway+Auth on node-a, Risk+MarketData+Execution on node-b)
- **Expected**: p999 increases 1.5–3× due to network serialization across nodes + extra scheduling hops on both nodes.
- **Mechanism**: Network round-trip added at hop 2→3 boundary. Kernel scheduling on both source and destination nodes.

### E3a — CFS Throttling (Tight, 200m)

- **IV**: CPU limit = 200m (20% of one core per period)
- **Expected**: p999 spikes strongly correlated with `nr_throttled` events. Throttle delay dominates.
- **Mechanism**: CFS bandwidth control. Threads blocked until next period boundary.

### E3b — CFS Throttling (Moderate, 500m)

- **IV**: CPU limit = 500m (50% of one core per period)
- **Expected**: Fewer throttle events than E3a. Tail still elevated vs E1 but significantly less than E3a.
- **Mechanism**: Same mechanism as E3a but with more headroom. Validates that the effect scales with quota tightness, not just binary on/off.
- **Why two points**: A single extreme quota (200m) invites the criticism "you chose an extreme config." Showing a dose-response with 500m strengthens the causal argument.

### E4 — Runqueue Contention (Isolated)

- **IV**: `stress-ng --cpu 4` on same node as all pods
- **Expected**: Wakeup-to-run delay increases 5–10×. p999 inflates proportionally.
- **Mechanism**: Runqueue depth increase. More preemption. Higher `sched_switch` rate.

### E5 — Throttling + Cross-Node

- **IV**: CPU limit + cross-node
- **Expected**: Additive/super-additive combination of E2 + E3 effects.
- **Mechanism**: Throttling on one node, network delay on hops crossing nodes.

### E6 — Contention + Cross-Node

- **IV**: stress-ng + cross-node
- **Expected**: Worst wakeup delay on stressed node, plus network delay.
- **Mechanism**: Scheduling delay amplified by contention, then serialized with network.

### E7 — Full Stress (Worst Case)

- **IV**: All stressors combined
- **Expected**: Highest p999. All three mechanisms active simultaneously.
- **Mechanism**: Interaction of throttling, contention, and cross-node delay. This is the "compound failure" scenario.

### E8 — hostNetwork Benefit

- **IV**: `hostNetwork: true` (removes veth pair, CNI overhead)
- **Expected**: p99 reduction of 10–30% vs E1 due to eliminated veth/bridge processing.
- **Mechanism**: Fewer softirq events per packet. Less NET_RX processing time.

### E9 — hostNetwork Under Full Stress

- **IV**: hostNetwork + stress-ng + cross-node
- **Expected**: hostNetwork partially mitigates softirq overhead even under stress.
- **Mechanism**: Reduced per-packet kernel processing partially offsets contention.

### E10 — Throttle + Contention (Same-Node)

- **IV**: CPU limit + stress-ng, all same-node
- **Expected**: Severe p999 inflation. Throttling and contention compound.
- **Mechanism**: Contention accelerates quota exhaustion → earlier throttling → longer delays.

### E11 — HTTP/1.1 Protocol Baseline

- **IV**: HTTP/1.1 instead of gRPC
- **Expected**: Higher baseline latency due to connection management overhead. Potentially worse tail due to head-of-line blocking.
- **Mechanism**: Protocol-level serialization and connection reuse differences.

### E12 — HTTP/1.1 Under Full Stress

- **IV**: HTTP/1.1 + all stressors
- **Expected**: HTTP/1.1 p999 worse than gRPC under same stress.
- **Mechanism**: Head-of-line blocking interacts with scheduling delays.

### E13 — Mitigation: CPU Pinning

- **IV**: Kubernetes static CPU manager, guaranteed QoS pods with integer CPU requests
- **Expected**: Dramatic p999 reduction vs E4 (contention). Wakeup delay near-zero for pinned CPUs.
- **Mechanism**: Pinned threads have dedicated CPUs — no runqueue competition.

### E14 — Mitigation: Pinning Under Stress

- **IV**: CPU pinning + stress-ng on non-pinned CPUs + cross-node
- **Expected**: Pinning protects against contention; cross-node delay remains.
- **Mechanism**: Isolation validates that scheduling delay was the primary contributor.

### E15 — Mitigation: Full Isolation

- **IV**: CPU pinning + IRQ affinity to non-app CPUs + hostNetwork + tuned CFS quotas
- **Expected**: Lowest achievable p999 in the experiment matrix. Near-baseline despite cross-node + stressors.
- **Mechanism**: Breaks the softirq→contention→throttling feedback loop entirely.

## 3.4 Experiment Execution Parameters

| Parameter | Value |
|-----------|-------|
| Offered load (steady) | 2000 req/s |
| Offered load (burst mode) | 1000 base + 5000 burst / 50ms every 2s |
| Warm-up duration | 30 seconds |
| Measurement duration | 120 seconds (steady), 180 seconds (burst) |
| Repetitions | 3 per experiment |
| Cool-down between runs | 60 seconds |
| Total experiments | 16 × 3 = 48 runs |

> **Timing estimate**: Each run takes ~4–5 minutes (30s warmup + 120–180s
> measurement + 60s cooldown + deploy/teardown overhead). A full sweep of 48
> runs takes **~6–10 hours** including re-runs and troubleshooting. Plan for a
> dedicated experiment day. Use `tmux` or `screen` for resilience.
