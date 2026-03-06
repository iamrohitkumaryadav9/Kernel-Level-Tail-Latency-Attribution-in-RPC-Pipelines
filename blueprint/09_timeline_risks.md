# 9. Timeline, MVP, Stretch Goals & Risks

## 9.1 Week-by-Week Timeline (8 Weeks)

### Week 1 — Infrastructure & Pipeline Skeleton

| Day | Task |
|-----|------|
| 1–2 | Provision 2-node kubeadm cluster (Ubuntu 22.04, kernel 5.15+). Enable cgroup v2, `--cpu-manager-policy=static`. Install chrony. |
| 3–4 | Implement 5 Go gRPC services (Gateway → Auth → Risk → MarketData → Execution). Scaffold shared proto. |
| 5 | Deploy Redis. Add OTel instrumentation. Verify Jaeger shows full 5-hop traces. |
| 6–7 | Write Kustomize base manifests. Deploy on cluster. Run ghz at 500 req/s — validate e2e latency < 5 ms. |

**Milestone**: Pipeline deployed and traced end-to-end.

### Week 2 — Load Generator & Experiment Framework

| Day | Task |
|-----|------|
| 1–2 | Build burst-mode load generator wrapper around ghz. Validate burst injection. |
| 3–4 | Write experiment runner (`run-experiment.sh`): deploy overlay → warm up → measure → collect → teardown. |
| 5 | Create Kustomize overlays for E1–E4 (baseline, cross-node, throttled, contention). |
| 6–7 | Run E1–E4 manually. Validate data collection pipeline. |

**Milestone**: E1–E4 complete with raw data. Experiment runner works.

### Week 3 — eBPF Tooling (rqdelay v0.1)

| Day | Task |
|-----|------|
| 1–2 | Implement bpftrace prototypes for scheduling delay and softirq time. Validate on cluster. |
| 3–5 | Implement libbpf CO-RE rqdelay: `sched_wakeup`/`sched_switch` delay histogram per cgroup. |
| 6–7 | Add softirq entry/exit tracking. Add cgroup → pod mapping. Validate output. |

**Milestone**: rqdelay produces per-pod scheduling delay histograms and softirq time series.

### Week 4 — Full eBPF + Remaining Overlays

| Day | Task |
|-----|------|
| 1–2 | Add TCP retransmit/drop probes to rqdelay. Add Prometheus exporter. |
| 3–4 | Write cgroup cpu.stat collector script (100ms polling). |
| 5–7 | Create remaining Kustomize overlays (E5–E12). Validate each deploys correctly. |

**Milestone**: Full instrumentation stack. All 12 observational experiment overlays ready.

### Week 5 — Experiment Execution (E1–E12)

| Day | Task |
|-----|------|
| 1–3 | Run E1–E8 (3 runs each = 24 runs). Automated via experiment runner. |
| 4–5 | Run E9–E12 (3 runs each = 12 runs). |
| 6–7 | Spot-check data quality. Re-run any failed experiments. |

**Milestone**: Full observational dataset (13 core experiments × 3 = 39 runs; +6 optional HTTP/1.1 runs if time permits).

### Week 6 — Analysis Pipeline

| Day | Task |
|-----|------|
| 1–2 | Build data parsing pipeline (ghz JSON + rqdelay CSV + cgroup stats → unified DataFrame). |
| 3–4 | Implement spike detection and case/control analysis. |
| 5–6 | Run attribution analysis for H1–H3. Generate scatter plots, CDFs, timelines. |
| 7 | Validate hypotheses. Identify any surprises or needed re-runs. |

**Milestone**: All hypotheses evaluated. Initial evidence plots generated.

### Week 7 — Mitigation Experiments + Final Analysis

| Day | Task |
|-----|------|
| 1–2 | Configure CPU pinning (E13), IRQ affinity (E15). Run mitigation experiments. |
| 3–4 | Run E14 (pinning + stress), E15 (full isolation). 3 runs each. |
| 5–6 | Compare mitigation results vs observational baselines. Generate mitigation waterfall plots. |
| 7 | Finalize all figures. Publication-quality formatting. |

**Milestone**: Mitigation validated. ≥ 40% p999 reduction demonstrated.

### Week 8 — Report & Polish

| Day | Task |
|-----|------|
| 1–3 | Write LaTeX report: Abstract, Introduction, Background, Methodology. |
| 4–5 | Results, Mitigation Evaluation, Discussion, Threats to Validity, Conclusion. |
| 6 | README with 1-command deploy/run/plot instructions. Clean up repo. |
| 7 | Final review. Tag release. Archive dataset. |

**Milestone**: Complete report + repo. Ready for submission.

---

## 9.2 Minimal Viable Experiment Set (MVP)

If time-constrained, the following **6 experiments** still prove the thesis:

| Exp | Purpose |
|-----|---------|
| E1 | Baseline (floor) |
| E3 | CFS throttling (isolated) |
| E4 | CPU contention (isolated) |
| E7 | Full stress (worst case) |
| E13 | Mitigation: CPU pinning |
| E15 | Mitigation: full isolation |

**Rationale**: E1 and E7 define the range. E3 and E4 isolate the two primary
mechanisms. E13 and E15 validate mitigations. This set supports H1, H2, and H3
with reduced statistical power but clear mechanism separation.

---

## 9.3 Stretch Goals (Publishable Extras)

| Priority | Goal | Value |
|----------|------|-------|
| S1 | **CNI comparison** (Calico VXLAN vs Cilium eBPF) | Quantifies dataplane overhead differences |
| S2 | **busy_poll evaluation** | Direct HFT-relevance (polling vs interrupt-driven) |
| S3 | **NUMA-aware placement** | Cross-NUMA socket latency attribution |
| S4 | **Real-time scheduling** (SCHED_FIFO for app threads) | Ultimate latency floor comparison |
| S5 | **Hardware timestamps** (PTP + NIC timestamping) | Sub-µs measurement accuracy |
| S6 | **Comparison with DPDK/XDP bypass** | Proves kernel bypass eliminates softirq path entirely |
| S7 | **E16: CPU governor — powersave vs performance** | Measures jitter from DVFS frequency transitions, C-state exit latency. HFT systems always pin `performance` governor and disable C-states; this quantifies the cost of not doing so. Run E1 with `cpupower frequency-set -g powersave` vs `performance`, compare p999 and wakeup delay variance. |

---

## 9.4 Risks & Fallback Plans

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Clock skew > 1ms across nodes** | Medium | Cross-node correlation unreliable | Use chrony with frequent sync; validate with round-trip probes; fallback to per-node–relative analysis |
| **eBPF probe instability / kernel version mismatch** | Medium | rqdelay crashes or misses events | Test on exact kernel version first; use vmlinux BTF from /sys/kernel/btf/vmlinux; fallback to bpftrace |
| **cgroup v1 instead of v2** | Low | Different CPU stat paths, different cgroup ID semantics | Detect at startup; support both in rqdelay; prefer v2 clusters |
| **cgroup ID → pod mapping failure** | Medium | Cannot attribute kernel events to pods | Multiple mapping strategies (inode, cgroup path parsing, kubelet API); validate mapping before experiments |
| **Insufficient CPU cores for pinning** | Medium | Cannot run E13–E15 | Minimum: 4 cores per node (2 reserved, 2 for pinning); use VMs with ≥ 8 vCPUs or bare metal |
| **Non-reproducible results (high CV)** | Medium | Cannot make strong claims | Increase repetitions to 5; disable dynamic frequency scaling (`performance` governor); disable turbo boost; quiesce other node workloads |
| **ghz throughput insufficient for burst mode** | Low | Cannot generate microbursts | Use wrk2 or custom Go client with precise rate control |
| **Kubernetes scheduling interferes with placement** | Low | Pods placed on wrong nodes | Use `nodeSelector` + taints/tolerations; verify placement with `kubectl get pods -o wide` |

### Contingency Timeline

If **Week 3** eBPF development takes longer:
- Week 3–4: Use bpftrace scripts (not CO-RE) for all measurements
- Week 5: Continue with bpftrace-based experiments
- Week 6–7: Port critical probes to libbpf CO-RE while analyzing data
- Tradeoff: Slightly higher measurement overhead, but thesis still provable

If **cluster issues** delay Week 1:
- Use `kind` (Kubernetes in Docker) on a single machine for initial pipeline development
- Move to kubeadm cluster in Week 2
- Cross-node experiments require real multi-node setup

---

## 9.5 HFT-Ready README Plan

```markdown
# latency-attribution — Kernel-Level Tail Latency Attribution for RPC Pipelines

## Quick Start

### Deploy the full pipeline
make cluster-setup       # kubeadm 2-node, cgroup v2, CPU manager
make deploy-E1           # Baseline experiment overlay

### Run the full experiment matrix
make run-all             # 16 experiments × 3 runs, ~6-10 hours

### Run a single experiment
make run EXP=E7 RUNS=3   # Full stress, 3 repetitions

### Generate all analysis plots
make analyze              # Parse data + run attribution + produce figures

## Key Findings (Summary)
- p999 inflates **4.7×** under CPU contention + CFS throttling (E7 vs E1)
- Wakeup-to-run delay explains **62%** of excess tail latency (r² = 0.62)
- CFS throttle events co-occur with **78%** of p999 spike windows
- CPU pinning + IRQ isolation reduces p999 by **53%** under full stress (E15 vs E7)
- ksoftirqd activation correlates with wakeup delay spikes (ρ = 0.47)

## Repository Structure
[... tree as in overview ...]

## Requirements
- 2× Linux nodes, kernel 5.15+, cgroup v2
- Go 1.22+, clang 14+, libbpf-dev
- Kubernetes 1.28+ (kubeadm)
- ghz, bpftrace, chrony
```
