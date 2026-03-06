# Experiment Name Mapping

This document maps blueprint experiment IDs to actual experiment names in the `data/` directory.

## Standard Experiments (Match Blueprint)

| Blueprint ID | Blueprint Description | Data Directory | Status |
|:------------|:---------------------|:--------------|:-------|
| E0 | Instrumentation overhead validation | `e0-no-instrumentation` | ✅ Match |
| E1 | Baseline: SN, no CPU limits, no contention | `e1-baseline` | ✅ Match |
| E2 | Cross-node placement | `e2-cross-node` | ✅ Match |
| E3a | CFS tight throttle (200m limit) | `e3a-cfs-tight` | ✅ Match |
| E3b | CFS moderate throttle (500m limit) | `e3b-cfs-moderate` | ✅ Match |
| E4 | Runqueue contention (stress-ng) | `e4-noisy-neighbor` | ✅ Match |
| E5 | CFS throttle + cross-node | `e5-throttle-crossnode` | ✅ Match |
| E6 | Contention + cross-node | `e6-contention-crossnode` | ✅ Match |
| E7 | Full stress (worst case) | `e7-full-stress` | ✅ Match |
| E8 | hostNetwork benefit | `e8-hostnetwork` | ✅ Match |
| E9 | hostNetwork under stress + cross-node | `e9-hostnet-stress-crossnode` | ✅ Match |
| E10 | Throttle + contention (same-node) | `e10-throttle-contention` | ✅ Match |
| E13 | CPU pinning mitigation | `e13-cpu-pinning` | ✅ Match |
| E14 | CPU pinning under stress + cross-node | `e14-pinning-stress-crossnode` | ✅ Match |
| E15 | Full isolation (all mitigations) | `e15-full-isolation` | ✅ Match |

## Replaced Experiments

| Blueprint ID | Blueprint (Original) | Actual Experiment | Reason |
|:------------|:--------------------|:-----------------|:-------|
| E11 | HTTP/1.1 baseline (optional/stretch) | `e11-network-policy` | HTTP/1.1 comparison was dropped as a stretch goal. Replaced with Kubernetes NetworkPolicy overhead experiment to test pod-to-pod firewall impact on latency. |
| E12 | HTTP/1.1 under stress (optional/stretch) | `e12-hpa` | HTTP/1.1 comparison was dropped. Replaced with HPA (Horizontal Pod Autoscaler) experiment to test autoscaling impact on tail latency during scale-up/down events. |

## Additional Experiments (Not in Original Blueprint)

| Data Directory | Purpose | Notes |
|:--------------|:--------|:------|
| `e2-cpu-contention` | Extreme CPU contention experiment | Tests heavy CPU oversubscription. Shows highest p99 (413ms). Naming conflicts with `e2-cross-node`. |
| `e3-memory-pressure` | Memory pressure experiment | Tests cgroup memory pressure impact on scheduling. Naming conflicts with `e3a-cfs-tight`/`e3b-cfs-moderate`. |
| `e8-resource-limits` | Kubernetes resource limits experiment | Tests Guaranteed QoS class (requests=limits). Naming conflicts with `e8-hostnetwork`. |
