# bpftrace Rapid Prototyping Scripts

Rapid-prototyping bpftrace scripts for kernel-level latency attribution.
These complement the production CO-RE tools in `ebpf/src/` and `ebpf/cmd/`.

## Scripts

| Script | Traces | Blueprint Ref |
|--------|--------|--------------|
| `runqueue_latency.bt` | sched_wakeup → sched_switch (wakeup-to-run delay) | §05, H1 |
| `softirq_latency.bt` | softirq_entry/exit, ksoftirqd activations | §05, H2 |
| `cfs_throttle.bt` | throttle_cfs_rq / unthrottle_cfs_rq | §05, E3/E10 |
| `tcp_retransmit.bt` | tcp_retransmit_skb, tcp_receive_reset | §05, H3 |
| `grpc_latency.bt` | epoll_wait, read/write, futex (gRPC request lifecycle) | §05, §04 |

## Usage

```bash
# Requires root and bpftrace >= 0.16
sudo bpftrace runqueue_latency.bt

# Run during experiment for 30 seconds
sudo timeout 30 bpftrace runqueue_latency.bt > /data/E7/bpftrace_rqlat.txt

# Filter by process name (gateway service)
sudo bpftrace -e 'tracepoint:sched:sched_wakeup /comm == "gateway"/ { ... }'
```

## Notes

- These are **prototyping** scripts — they print to stdout and have no JSON output
- For production use, prefer the CO-RE rqdelay tool which has lower overhead
- The scripts generate per-CPU histograms which require `sudo` access
- Some kprobes (e.g., `throttle_cfs_rq`) are kernel-version dependent
