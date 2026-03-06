# 5. eBPF Instrumentation Plan

## 5.1 Tooling Architecture

```
┌─────────────────────────────────────────────────┐
│  rqdelay  (flagship tool, libbpf CO-RE)          │
│                                                 │
│  eBPF programs (C)         User-space (Go)      │
│  ┌────────────────┐   ┌──────────────────────┐  │
│  │ sched_wakeup   │──→│ Event consumer       │  │
│  │ sched_switch   │   │ (perf ring buffer)   │  │
│  │ softirq_entry  │   │                      │  │
│  │ softirq_exit   │   │ Histogram aggregator │  │
│  │ tcp_retransmit │   │                      │  │
│  │ kfree_skb      │   │ Prometheus exporter  │  │
│  └────────────────┘   │ (:9090/metrics)      │  │
│                       │                      │  │
│  BPF Maps:            │ CSV/JSON event log   │  │
│  - wakeup_ts (hash)   │ writer               │  │
│  - delay_hist (hash)  │                      │  │
│  - softirq_time (arr) │ cgroup→pod resolver  │  │
│  - retrans_cnt (hash) └──────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Rapid Prototyping (bpftrace)

For initial validation, use bpftrace one-liners before writing CO-RE:

```bash
# Wakeup-to-run delay histogram for all threads
bpftrace -e '
tracepoint:sched:sched_wakeup { @wakeup[args->pid] = nsecs; }
tracepoint:sched:sched_switch /args->next_pid/ {
  $pid = args->next_pid;
  if (@wakeup[$pid]) {
    @delay_us = hist((nsecs - @wakeup[$pid]) / 1000);
    delete(@wakeup[$pid]);
  }
}'

# softirq time per vector
bpftrace -e '
tracepoint:irq:softirq_entry { @start[cpu] = nsecs; }
tracepoint:irq:softirq_exit  /@start[cpu]/ {
  @softirq_us[args->vec] = hist((nsecs - @start[cpu]) / 1000);
  delete(@start[cpu]);
}'
```

---

## 5.2 Tracepoint / Kprobe Hookpoints

| Hook | Type | Fields Captured | Purpose |
|------|------|-----------------|---------|
| `sched:sched_wakeup` | tracepoint | pid, target_cpu, prio, comm | Record wakeup timestamp |
| `sched:sched_wakeup_new` | tracepoint | pid, target_cpu, prio, comm | New thread wakeups |
| `sched:sched_switch` | tracepoint | prev_pid, prev_state, next_pid, next_comm | Compute wakeup-to-run delay |
| `irq:softirq_entry` | tracepoint | vec | Start softirq timing |
| `irq:softirq_exit` | tracepoint | vec | End softirq timing |
| `irq:softirq_raise` | tracepoint | vec | Detect softirq scheduling |
| `tcp:tcp_retransmit_skb` | tracepoint | skaddr, sport, dport | Count retransmissions |
| `skb:kfree_skb` | tracepoint | skbaddr, reason, protocol | Packet drop tracking |
| `tcp:tcp_cong_state_set` | tracepoint | skaddr, newstate | Congestion transitions |
| `cgroup:cgroup_throttle` | tracepoint (if available) | cgroup_id | Direct throttle events |

---

## 5.3 Sampling Strategy

| Event Class | Rate | Justification |
|-------------|------|---------------|
| Wakeup/switch (histogram update) | **100%** — all events update in-kernel histograms | BPF map updates are O(1), negligible overhead |
| Wakeup/switch (raw event export) | **1/10 sampling** — emit every 10th event to ring buffer | Controls ring buffer throughput; ~200 events/s at 2000 req/s |
| softirq entry/exit | **100%** cumulative counters; **1/100** raw events | softirq events are high-frequency (~10k/s under load) |
| TCP retransmit/drop | **100%** — all events | Low frequency (< 100/s typically) |
| cgroup stat polling | **10 Hz** (100ms interval) | Polling, not tracing; minimal overhead |

**Overhead target**: < 2% CPU overhead from eBPF instrumentation.

**Mandatory validation (E0)**:
- Run E1 baseline **without** rqdelay instrumentation → record p50/p99/p999
- Run E1 baseline **with** rqdelay instrumentation → record p50/p99/p999
- Compare: if instrumented p999 exceeds uninstrumented p999 by > 2%, reduce
  sampling rate or optimize BPF programs before proceeding with experiments
- Document E0 results in the final report as "Measurement Overhead" section

---

## 5.4 Pod-Aware Cgroup Mapping

### In-Kernel: cgroup ID Capture

Every eBPF event includes `bpf_get_current_cgroup_id()`, which returns the cgroup v2
inode number of the current task's cgroup.

```c
struct event {
    u64 timestamp_ns;
    u32 pid;
    u32 cpu;
    u64 cgroup_id;
    u64 delay_ns;     // for sched events
    u32 softirq_vec;  // for softirq events
};
```

### User-Space: cgroup ID → Pod Name Resolution

The Go user-space component builds a mapping at startup and refreshes periodically:

```
1. List all pod cgroups:
   /sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod<UID>.slice/

2. For each cgroup directory:
   - Read the inode number: stat(cgroup_dir).Ino → cgroup_id
   - Extract pod UID from path
   - Query kubelet API or read /var/lib/kubelet/pods/<UID>/etc-hosts to get pod name

3. Maintain map: cgroup_id → {pod_name, namespace, container_name}

4. Refresh every 10 seconds (handles pod restarts)
```

### Pre-Experiment Mapping Validation (REQUIRED)

Before running any experiments, validate that cgroup ID → pod mapping is working
correctly on your specific cluster:

```
1. Deploy the pipeline (E1 config)
2. Run rqdelay for 10 seconds
3. Verify: every event's cgroup_id resolves to a known pod name
4. If unmapped events > 1%, debug mapping:
   - Check container runtime (containerd vs CRI-O → different cgroup paths)
   - Check cgroup version (v1 vs v2 — different inode semantics)
   - Try alternative mapping strategy (path parsing vs inode vs kubelet API)
5. Document mapping strategy and success rate in experiment metadata
```

> ⚠️ **cgroup paths vary by container runtime.** `containerd` uses
> `cri-containerd-<id>.scope`, `CRI-O` uses `crio-<id>.scope`, and
> `Docker` uses yet another layout. Test on your exact cluster before
> assuming any specific path format.

### Alternative: Container ID via cgroup path

```
cgroup path: .../cri-containerd-<container_id>.scope
             → container_id
             → crictl inspect <container_id> → pod name
```

---

## 5.5 rqdelay Output Formats

### Prometheus Metrics (primary)

```
# Wakeup-to-run delay histogram per pod
rqdelay_wakeup_delay_us_bucket{pod="gateway-xxx", le="10"} 4521
rqdelay_wakeup_delay_us_bucket{pod="gateway-xxx", le="50"} 4890
rqdelay_wakeup_delay_us_bucket{pod="gateway-xxx", le="100"} 4950
rqdelay_wakeup_delay_us_bucket{pod="gateway-xxx", le="500"} 4998
rqdelay_wakeup_delay_us_bucket{pod="gateway-xxx", le="+Inf"} 5000

# softirq time per CPU per vector
rqdelay_softirq_time_us_total{cpu="0", vec="NET_RX"} 123456
rqdelay_softirq_time_us_total{cpu="0", vec="NET_TX"} 78901

# ksoftirqd activations
rqdelay_ksoftirqd_activations_total{cpu="0"} 42

# TCP retransmissions per pod
rqdelay_tcp_retransmit_total{pod="gateway-xxx"} 7
```

### Raw Event Log (CSV)

```csv
timestamp_ns,event_type,pid,cpu,cgroup_id,pod,delay_ns,vec,extra
1708123456789012345,sched_delay,1234,2,98765,gateway-xxx,45000,,
1708123456789112345,softirq,0,2,0,,12000,3,
1708123456789212345,retransmit,1234,2,98765,gateway-xxx,,,sport=50051:dport=38422
```
