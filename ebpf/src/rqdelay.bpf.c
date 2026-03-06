// rqdelay — eBPF tool for kernel-level latency attribution
// Blueprint §5: sched_wakeup/switch, softirq, cgroup mapping, tcp retransmit
#include "vmlinux.h"

#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

typedef __u64 u64;
typedef __u32 u32;

char LICENSE[] SEC("license") = "Dual BSD/GPL";

// ─── Maps ─────────────────────────────────────────────────────────────

// Blueprint §4.2.1: store both timestamp AND cgroup_id at sched_wakeup time.
// At sched_switch, bpf_get_current_cgroup_id() returns the PREVIOUS task's
// cgroup (not next_pid's), so we MUST capture it at wakeup time.
struct wakeup_info {
    u64 timestamp_ns;
    u64 cgroup_id;
};

// pid -> {wakeup timestamp, cgroup_id captured at wakeup time}
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);
    __type(value, struct wakeup_info);
} wakeup_start SEC(".maps");

// run_delay_hist: log2(us) -> count  (system-wide)
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 64);
    __type(key, u32);
    __type(value, u64);
} run_delay_hist SEC(".maps");

// ─── Per-cgroup (pod) maps (Blueprint §5.4) ──────────────────────────

// cgroup_id -> log2 histogram bucket -> count
// Using a hash map keyed by (cgroup_id, bucket) compound key
struct cgroup_hist_key {
    u64 cgroup_id;
    u32 bucket;
    u32 _pad;
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, struct cgroup_hist_key);
    __type(value, u64);
} cgroup_delay_hist SEC(".maps");

// Track unique cgroup IDs seen (for userspace enumeration)
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, u64);
    __type(value, u64);  // first seen timestamp
} cgroup_ids SEC(".maps");

// ─── softirq maps (Blueprint §4.2.4) ─────────────────────────────────

// per-CPU softirq entry timestamp
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} softirq_start SEC(".maps");

// softirq cumulative time per vector (10 vectors)
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 10);
    __type(key, u32);
    __type(value, u64);
} softirq_time SEC(".maps");

// softirq event count per vector
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 10);
    __type(key, u32);
    __type(value, u64);
} softirq_count SEC(".maps");

// ─── TCP retransmit maps (Blueprint §4.3) ─────────────────────────────

// Total TCP retransmit count
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} tcp_retransmit_count SEC(".maps");

// Per-cgroup retransmit count
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, u64);  // cgroup_id
    __type(value, u64);
} cgroup_retransmit SEC(".maps");

// ─── Scheduling Tracepoints ───────────────────────────────────────────

SEC("tracepoint/sched/sched_wakeup")
int tp_sched_wakeup(struct trace_event_raw_sched_wakeup_template *ctx)
{
    u32 pid = ctx->pid;
    struct wakeup_info info = {};
    info.timestamp_ns = bpf_ktime_get_ns();
    // Capture cgroup_id NOW (at wakeup time) — not at sched_switch.
    // Blueprint §4.2.1 pitfall: at sched_switch, bpf_get_current_cgroup_id()
    // returns the PREVIOUS task's cgroup, not next_pid's cgroup.
    info.cgroup_id = bpf_get_current_cgroup_id();
    bpf_map_update_elem(&wakeup_start, &pid, &info, BPF_ANY);
    return 0;
}

SEC("tracepoint/sched/sched_wakeup_new")
int tp_sched_wakeup_new(struct trace_event_raw_sched_wakeup_template *ctx)
{
    u32 pid = ctx->pid;
    struct wakeup_info info = {};
    info.timestamp_ns = bpf_ktime_get_ns();
    info.cgroup_id = bpf_get_current_cgroup_id();
    bpf_map_update_elem(&wakeup_start, &pid, &info, BPF_ANY);
    return 0;
}

SEC("tracepoint/sched/sched_switch")
int tp_sched_switch(struct trace_event_raw_sched_switch *ctx)
{
    u32 next_pid = ctx->next_pid;
    u64 ts = bpf_ktime_get_ns();

    struct wakeup_info *info = bpf_map_lookup_elem(&wakeup_start, &next_pid);
    if (!info)
        return 0;

    u64 delta_ns = ts - info->timestamp_ns;
    u64 delta_us = delta_ns / 1000;
    // Use cgroup_id captured at wakeup time (correct per §4.2.1)
    u64 cgid = info->cgroup_id;

    bpf_map_delete_elem(&wakeup_start, &next_pid);

    // Compute log2 histogram bucket
    u64 bucket = 0;
    if (delta_us > 0) {
        bucket = 63 - __builtin_clzll(delta_us);
        if (bucket >= 64)
            bucket = 63;
    }

    // System-wide histogram
    u32 key = (u32)bucket;
    u64 *val = bpf_map_lookup_elem(&run_delay_hist, &key);
    if (val)
        (*val)++;

    // Per-cgroup (pod) histogram — now correctly attributed (Blueprint §5.4)
    if (cgid > 0) {
        // Track cgroup ID
        u64 *existing = bpf_map_lookup_elem(&cgroup_ids, &cgid);
        if (!existing) {
            bpf_map_update_elem(&cgroup_ids, &cgid, &ts, BPF_NOEXIST);
        }
        // Update per-cgroup histogram
        struct cgroup_hist_key ckey = {};
        ckey.cgroup_id = cgid;
        ckey.bucket = key;
        u64 *cval = bpf_map_lookup_elem(&cgroup_delay_hist, &ckey);
        if (cval) {
            (*cval)++;
        } else {
            u64 one = 1;
            bpf_map_update_elem(&cgroup_delay_hist, &ckey, &one, BPF_NOEXIST);
        }
    }

    return 0;
}

// ─── softirq Tracepoints (Blueprint §4.2.4) ──────────────────────────

// softirq_entry tracepoint context
struct softirq_entry_ctx {
    unsigned short common_type;
    unsigned char common_flags;
    unsigned char common_preempt_count;
    int common_pid;
    unsigned int vec;
};

SEC("tracepoint/irq/softirq_entry")
int tp_softirq_entry(struct softirq_entry_ctx *ctx)
{
    u32 zero = 0;
    u64 ts = bpf_ktime_get_ns();
    bpf_map_update_elem(&softirq_start, &zero, &ts, BPF_ANY);

    // Increment softirq event count for this vector
    u32 vec = ctx->vec;
    if (vec >= 10)
        return 0;

    u64 *cnt = bpf_map_lookup_elem(&softirq_count, &vec);
    if (cnt)
        (*cnt)++;

    return 0;
}

// softirq_exit tracepoint context
struct softirq_exit_ctx {
    unsigned short common_type;
    unsigned char common_flags;
    unsigned char common_preempt_count;
    int common_pid;
    unsigned int vec;
};

SEC("tracepoint/irq/softirq_exit")
int tp_softirq_exit(struct softirq_exit_ctx *ctx)
{
    u32 zero = 0;
    u64 *start_ts = bpf_map_lookup_elem(&softirq_start, &zero);
    if (!start_ts)
        return 0;

    u64 duration = bpf_ktime_get_ns() - *start_ts;

    u32 vec = ctx->vec;
    if (vec >= 10)
        return 0;

    u64 *time_val = bpf_map_lookup_elem(&softirq_time, &vec);
    if (time_val)
        (*time_val) += duration;

    return 0;
}

// ─── TCP Retransmit Probe (Blueprint §4.3) ────────────────────────────

SEC("tracepoint/tcp/tcp_retransmit_skb")
int tp_tcp_retransmit(void *ctx)
{
    // Increment global retransmit counter
    u32 zero = 0;
    u64 *cnt = bpf_map_lookup_elem(&tcp_retransmit_count, &zero);
    if (cnt)
        (*cnt)++;

    // Per-cgroup retransmit tracking
    u64 cgid = bpf_get_current_cgroup_id();
    if (cgid > 0) {
        u64 *cval = bpf_map_lookup_elem(&cgroup_retransmit, &cgid);
        if (cval) {
            (*cval)++;
        } else {
            u64 one = 1;
            bpf_map_update_elem(&cgroup_retransmit, &cgid, &one, BPF_NOEXIST);
        }
    }

    return 0;
}
