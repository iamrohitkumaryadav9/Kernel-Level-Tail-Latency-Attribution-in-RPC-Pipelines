# 8. Mitigations & Optimizations

## M1: CPU Pinning via Kubernetes Static CPU Manager

### System Change
```yaml
# kubelet config (on latency-critical nodes)
cpuManagerPolicy: static
reservedSystemCPUs: "0-1"   # Reserve CPUs 0-1 for system daemons

# Pod spec (Guaranteed QoS)
resources:
  requests:
    cpu: "1"      # Integer request → pinning
    memory: "256Mi"
  limits:
    cpu: "1"
    memory: "256Mi"
```

Each pipeline service receives a dedicated physical CPU core. No other **userspace**
threads are scheduled on that core. However, kernel threads (ksoftirqd, RCU,
timers, workqueues) may still execute on pinned CPUs — the pinning guarantees
exclusive CFS scheduling, not exclusive hardware access.

### Expected Metric Improvement
- wakeup_delay_p99: < 10 µs (from > 100 µs under contention)
- p999: ≥ 40% reduction vs E4 (contention baseline)
- Context switches on pinned CPUs: near-zero involuntary switches

### Verification
- E13 vs E4: wakeup delay and p999 comparison
- `cat /sys/fs/cgroup/.../cpuset.cpus.effective` confirms dedicated CPU assignment
  (exclusive from other userspace pods, not from kernel threads)
- `mpstat -P ALL 1` shows pinned CPUs at ~0% idle only when requests arrive

### Experiments: E13, E14

---

## M2: IRQ Affinity + ksoftirqd Isolation

### System Change
```bash
# Disable irqbalance
systemctl stop irqbalance
systemctl disable irqbalance

# Pin all NIC IRQs to CPUs 0-1 (reserved system CPUs)
NIC="eth0"  # or ens3, etc.
for irq in $(grep "$NIC" /proc/interrupts | awk '{print $1}' | tr -d ':'); do
    echo "0-1" > /proc/irq/$irq/smp_affinity_list
done

# Verify ksoftirqd/0 and ksoftirqd/1 handle network softirqs
# This biases IRQ handling away from application CPUs (2-7), though
# softirq may still execute on other CPUs depending on RPS config
# and driver behavior. Verify with measured per-CPU softirq time.
```

### Expected Metric Improvement
- softirq_time on application CPUs: near-zero (from 10s of ms/s)
- ksoftirqd activations on application CPUs: 0
- wakeup_delay_p99 reduction: 20–40% (softirq no longer competes with app threads)
- p999 reduction: 15–25% vs E4 when combined with CPU pinning

### Verification
- `cat /proc/irq/<N>/smp_affinity_list` confirms NIC IRQs on CPUs 0-1
- rqdelay softirq_time per CPU: application CPUs show ~0 NET_RX time
- Compare E15 vs E14 (E14 has pinning but not IRQ affinity)

### Experiments: E15

---

## M3: CPU Limit Tuning (Avoiding Throttling Cliffs)

### System Change

Instead of tight limits (200m) that trigger aggressive CFS throttling, use one of:

**Option A**: Remove CPU limits entirely (keep requests for scheduling)
```yaml
resources:
  requests:
    cpu: "200m"    # Scheduler hint
  # No limits → Burstable QoS, no throttling
```

**Option B**: Set limits to 2× request (headroom for bursts)
```yaml
resources:
  requests:
    cpu: "200m"
  limits:
    cpu: "500m"    # 2.5× headroom; throttling only under sustained overload
```

**Option C**: Use `cpuset` cgroups (static CPU manager) instead of CFS quotas

### Expected Metric Improvement
- nr_throttled: 0 (Option A) or reduced > 90% (Option B)
- throttled_usec: 0 or near-zero
- p999 reduction: 30–60% vs E3 (tight limits)
- Per-hop p99 tail spikes eliminated

### Verification
- `cat /sys/fs/cgroup/.../cpu.stat` → nr_throttled = 0
- Compare E3 (200m limit) vs E1 (no limit) p999
- Time-series plot: throttle events ↔ p999 spikes disappear

### Experiments: E3 vs E1 comparison, E15 (tuned limits)

---

## M4: hostNetwork Mode (Bypass CNI Overhead)

### System Change
```yaml
spec:
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet
  containers:
  - name: gateway
    ports:
    - containerPort: 50051
      hostPort: 50051
```

Pods use the host's network namespace directly. No veth pairs, no bridge, no CNI
plugin processing. Packets go directly to/from the host's NIC.

### Expected Metric Improvement
- Per-packet softirq processing time: reduced (no veth traversal)
- p99 reduction: 10–30% vs pod networking (E1) at same load
- TCP retransmissions: potentially reduced (shorter network path)

### Verification
- E8 vs E1: p99/p999 comparison
- E9 vs E6: hostNetwork benefit under stress
- `ip link show` in pod → sees host interfaces, not veth

### Experiments: E8, E9, E15

---

## M5 (Stretch): RPS/XPS Tuning

### System Change
```bash
# Enable Receive Packet Steering — distribute softirq across CPUs
echo "ff" > /sys/class/net/eth0/queues/rx-0/rps_cpus
echo 4096 > /proc/sys/net/core/rps_sock_flow_entries
echo 2048 > /sys/class/net/eth0/queues/rx-0/rps_flow_cnt

# Transmit Packet Steering
echo "ff" > /sys/class/net/eth0/queues/tx-0/xps_cpus
```

### Expected Improvement
- Distribute NET_RX softirq load across multiple CPUs
- Reduce per-CPU softirq time
- Potentially reduce ksoftirqd activations on hot CPUs

### Verification
- Compare per-CPU softirq time before/after RPS
- Only relevant for single-queue NICs

---

## M6 (Stretch): TCP busy_poll / busy_read

### System Change
```bash
# Enable busy polling (spin-wait for packets instead of sleeping)
sysctl -w net.core.busy_read=50        # 50 µs busy-read
sysctl -w net.core.busy_poll=50        # 50 µs busy-poll

# Per-socket (in Go service code)
conn.SyscallConn().Control(func(fd uintptr) {
    syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_BUSY_POLL, 50)
})
```

### Expected Improvement
- Eliminate wakeup delay for polling threads (no sleep → no wakeup needed)
- p99 reduction: 10–20% for latency-sensitive hops
- Tradeoff: higher CPU utilization (spinning)

### Verification
- Compare wakeup_delay_p99 with and without busy_poll
- Monitor CPU utilization increase (acceptable for HFT-style workloads)

---

## M7 (Stretch): Socket Buffer Tuning

### System Change
```bash
sysctl -w net.core.rmem_max=16777216
sysctl -w net.core.wmem_max=16777216
sysctl -w net.ipv4.tcp_rmem="4096 131072 16777216"
sysctl -w net.ipv4.tcp_wmem="4096 65536 16777216"
sysctl -w net.core.netdev_max_backlog=5000
```

### Expected Improvement
- Reduced packet drops under burst load
- Lower retransmission rate
- More stable tail latency during microbursts

### Verification
- Compare retransmit rate and packet drops before/after
- Monitor `/proc/net/sockstat` memory pressure
