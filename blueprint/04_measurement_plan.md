# 4. Measurement Plan

## 4.1 Application-Level Metrics

### End-to-End Latency Distribution

| Metric | Source | Collection Method |
|--------|--------|-------------------|
| p50 / p90 / p99 / p999 latency | gRPC client (ghz) | `ghz --format json` output → parse `latencyDistribution` |
| Throughput (req/s) | ghz | `rps` field in JSON output |
| Error rate / timeouts | ghz | `statusCodeDistribution` + timeout count |

### Per-Hop Span Breakdown

| Metric | Source | Collection Method |
|--------|--------|-------------------|
| Per-service span duration | OTel / Jaeger | Jaeger API: `GET /api/traces?service=gateway&limit=10000` |
| Span start/end timestamps | OTel spans | Extracted from trace JSON; nanosecond precision |
| Redis sub-span | OTel Redis instrumentation | `go.opentelemetry.io/contrib/instrumentation/github.com/redis/go-redis` |

### Burst Sensitivity

| Metric | Definition | Method |
|--------|------------|--------|
| Burst p999 | p999 computed only over 100ms windows containing bursts | Post-hoc: filter traces by timestamp within burst windows |
| Recovery time | Time from burst end until p99 returns to pre-burst level | Sliding-window p99, detect crossing threshold |
| Burst amplification ratio | burst_p999 / steady_p999 | Computed per experiment |

---

## 4.2 OS / Kernel Scheduling Metrics

### 4.2.1 Runqueue Delay / Scheduling Latency (Wakeup-to-Run)

**Definition**: Time between `sched_wakeup` (thread made runnable) and subsequent
`sched_switch` (thread actually gets CPU).

**Tracepoints**:
```
sched:sched_wakeup      → fields: comm, pid, target_cpu, prio
sched:sched_wakeup_new  → fields: comm, pid, target_cpu, prio
sched:sched_switch      → fields: prev_comm, prev_pid, prev_state, next_comm, next_pid
```

**Computation**:
```
On sched_wakeup(pid=P, timestamp=T_w):
    pending_wakeups[P] = T_w
    pid_to_cgroup[P] = bpf_get_current_cgroup_id()  // capture cgroup at wakeup time

On sched_switch(next_pid=P, timestamp=T_s):
    if P in pending_wakeups:
        delay = T_s - pending_wakeups[P]
        cgroup_id = pid_to_cgroup[P]  // use cgroup captured at wakeup, NOT current
        emit(pid=P, cgroup_id=cgroup_id, delay_ns=delay)
        delete pending_wakeups[P]
```

> ⚠️ **cgroup_id attribution pitfall**: At `sched_switch` time,
> `bpf_get_current_cgroup_id()` returns the cgroup of the **previous** task
> (the one being switched away from), not `next_pid`. To get `next_pid`'s
> cgroup correctly, capture it at `sched_wakeup` time (when the woken task IS
> the "current" task's target) or resolve PID → cgroup in user-space via
> `/proc/<pid>/cgroup`. The approach above captures at wakeup time.

**Aggregation**:
- Per-pod histogram (log2 buckets) updated in eBPF map
- Exported every 1 second as Prometheus histogram
- Raw events (sampled at 1/10 for overhead control) written to perf ring buffer

**Target**: wakeup_delay_p99 < 50 µs (baseline), measurable inflation under contention.

### 4.2.2 Context Switch Rate

**Tracepoint**: `sched:sched_switch`

**Metrics**:
- `context_switches_per_sec` per CPU (from `/proc/stat` supplemented by eBPF per-cgroup counter)
- Voluntary vs involuntary (from `prev_state` field: `TASK_RUNNING` → involuntary)

### 4.2.3 CPU Throttling (CFS Quota)

**Source**: cgroup v2 `cpu.stat` file per pod's cgroup

```bash
# Path: /sys/fs/cgroup/kubepods.slice/kubepods-pod<UID>.slice/cpu.stat
cat cpu.stat
# usage_usec 12345678
# nr_periods 5000
# nr_throttled 1234
# throttled_usec 567890
```

**Collection**:
- Poll every 100ms via a DaemonSet sidecar
- Compute deltas: `Δnr_throttled`, `Δthrottled_usec` per 100ms window
- Align timestamps with application latency windows

**Key metric**: `throttled_usec` delta during spike windows vs. calm windows.

### 4.2.4 softirq Backlog / ksoftirqd Behavior

> **Important**: softirq events run in **kernel context**, not in any pod's cgroup.
> softirq metrics are therefore **per-CPU**, not per-pod. Do not attempt to
> attribute softirq time directly to individual pods. Instead, correlate per-CPU
> softirq time with the scheduling delay experienced by pods whose threads run
> on the same CPU.

**Tracepoints**:
```
irq:softirq_entry  → fields: vec (softirq vector number)
irq:softirq_exit   → fields: vec
irq:softirq_raise  → fields: vec
```

**softirq vectors of interest**:
- `NET_RX` = 3 (network receive)
- `NET_TX` = 4 (network transmit)

**Metrics computed in eBPF** (all per-CPU, not per-pod):
```
On softirq_entry(vec=NET_RX, cpu=C, timestamp=T_enter):
    softirq_start[C] = T_enter

On softirq_exit(vec=NET_RX, cpu=C, timestamp=T_exit):
    duration = T_exit - softirq_start[C]
    softirq_time_per_cpu[C] += duration  // cumulative per second
    softirq_histogram.increment(duration_bucket)
```

**ksoftirqd detection**:
- On `sched_switch(next_comm="ksoftirqd/*")`: record ksoftirqd activation timestamp
- On `sched_switch(prev_comm="ksoftirqd/*")`: record ksoftirqd deactivation
- Track: ksoftirqd_active_time_per_cpu, ksoftirqd_activation_count
- Cross-reference with `softirq_raise` events: when `softirq_raise(NET_RX)` count
  exceeds the kernel's per-NAPI budget (typically 64 packets), ksoftirqd activates.

**Key signal**: When ksoftirqd activates, it runs as a normal CFS thread at nice 19,
competing with application threads. This is the mechanism by which network load
creates scheduling interference.

### 4.2.5 Wakeup Latency (Derived)

Computed from `sched_wakeup` + `sched_switch` (same as 4.2.1), but specifically
tracked for three thread classes:

| Thread Class | Identification | Why It Matters |
|-------------|----------------|----------------|
| Application workers | cgroup ID matching pod cgroups | Direct impact on request latency |
| ksoftirqd | `comm == "ksoftirqd/*"` | Delayed packet processing → delayed app wakeup |
| Redis server | cgroup ID of Redis pod | Downstream dependency tail |

---

## 4.3 Network Metrics

| Metric | Hook / Source | Fields |
|--------|-------------|--------|
| TCP retransmissions | `tcp:tcp_retransmit_skb` tracepoint | `skaddr`, `sport`, `dport`, `saddr`, `daddr`, `state` |
| Packet drops | `skb:kfree_skb` tracepoint | `skbaddr`, `protocol`, `reason` (kernel 5.17+) |
| TCP congestion state | `tcp:tcp_cong_state_set` tracepoint | `skaddr`, `newstate` |
| Socket buffer pressure | `/proc/net/sockstat` polling | `TCP: mem` field |
| Retransmit rate | Derived | retrans_count / elapsed_time per connection |

**eBPF collection for network metrics**:
```c
SEC("tracepoint/tcp/tcp_retransmit_skb")
int trace_retransmit(struct trace_event_raw_tcp_event_skb *ctx) {
    struct sock *sk = (struct sock *)ctx->skaddr;
    u64 cgroup_id = bpf_get_current_cgroup_id();
    // Log: timestamp, cgroup_id, sport, dport, state
    // Increment per-cgroup retransmit counter
}
```
