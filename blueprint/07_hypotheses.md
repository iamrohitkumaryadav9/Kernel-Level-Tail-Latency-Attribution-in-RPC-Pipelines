# 7. Falsifiable Hypotheses

## H1: Scheduling Delay Dominates Tail Inflation Under CPU Contention

### Statement
> Under CPU contention (stress-ng), wakeup-to-run scheduling delay for application
> threads is the **dominant contributor** to p999 latency inflation — the strongest
> predictor of excess latency and the signal that explains the most variance in
> tail behavior compared to the uncontested baseline.

### Predicted Observation
- In E4 (contention, same-node) and E6 (contention, cross-node), the median
  wakeup-to-run delay during spike windows will exceed 100 µs, compared to < 20 µs
  in E1 (baseline).
- The per-request scheduling delay (summed across all hops) will correlate with
  end-to-end latency with Spearman ρ ≥ 0.5.

### Validation
1. Compare wakeup_delay_p99 distributions: E1 vs E4 (Mann-Whitney U, p < 0.01).
2. Scatter plot: per-request sum(wakeup_delay) vs e2e latency.
3. Case/control: slow requests have ≥ 3× higher wakeup delay than fast requests.

### Falsification
- If slow requests show no elevated wakeup delay (Cliff's d < 0.2), or if
  wakeup delay is not the strongest predictor among measured kernel signals
  (softirq time, throttle events, retransmissions), the hypothesis is false.
- If E13 (CPU pinning) does not reduce p999 by ≥ 30% vs E4, the mechanism is
  not scheduling delay.

### Experiments That Test It
- E1 (baseline), E4 (contention), E6 (contention + cross-node), E7 (full stress)
- E13 (mitigation: pinning), E14 (pinning + stress)

---

## H2: softirq/ksoftirqd Interference Creates Scheduling Delay

### Statement
> Network packet processing via softirq and ksoftirqd thread activation
> significantly increases runqueue depth and wakeup-to-run delay for application
> threads sharing the same CPU, especially when softirq processing exceeds the
> per-NAPI budget and defers to ksoftirqd.

### Predicted Observation
- During spike windows, NET_RX softirq cumulative time per CPU exceeds 2× the
  calm-window average.
- ksoftirqd activation events correlate temporally with elevated wakeup delay
  (within 1ms window): Spearman ρ ≥ 0.4.
- CPUs running both application threads and ksoftirqd show higher wakeup delay
  than CPUs with only application threads.

### Validation
1. Time-series correlation: ksoftirqd_active_time vs wakeup_delay_p99 per CPU per
   100ms window.
2. Compare softirq time in spike windows vs calm windows (paired t-test within
   each experiment).
3. E8 (hostNetwork) should show reduced softirq overhead vs E1 (fewer veth-related
   softirq events), resulting in lower wakeup delay.

### Falsification
- If softirq time and ksoftirqd activations show no significant difference between
  spike and calm windows (Mann-Whitney p > 0.05), the interference hypothesis fails.
- If E8 (hostNetwork) shows no improvement in wakeup delay vs E1, veth-related
  softirq is not a significant factor.
- If manually pinning IRQs to non-application CPUs (E15) does not reduce wakeup
  delay, the interference is not IRQ-driven.

### Experiments That Test It
- E1, E4, E8 (hostNetwork), E9 (hostNetwork + stress)
- E15 (full isolation: IRQ affinity + CPU pinning)

---

## H3: CFS Bandwidth Throttling Creates Correlated Multi-Hop Tail Spikes

### Statement
> When pods have tight CPU limits (200m), CFS bandwidth throttling produces
> millisecond-scale scheduling delays that appear as correlated tail spikes across
> multiple hops in the pipeline, because throttling affects the entire pod's
> cgroup simultaneously.

### Predicted Observation
- In E3 (throttled, same-node) and E5 (throttled, cross-node), `throttled_usec`
  deltas are > 0 during ≥ 70% of spike windows and = 0 during ≥ 90% of calm windows.
- Per-hop p99 amplification under throttling is super-additive: e2e p99 > N × per-hop
  p99 (where N = 5 hops), because throttling events at one hop delay the entire
  downstream chain.
- E10 (throttle + contention) shows the most severe compound effect because
  contention accelerates quota exhaustion.

### Validation
1. Timeline plot: `throttled_usec` delta overlaid with windowed p999 → visual
   co-occurrence.
2. Chi-squared test: association between (spike window = True) and
   (throttle event = True).
3. E3 p999 improves dramatically when CPU limit is removed (compare E3 vs E1).
4. E15 (tuned quota: set limit to 1000m or remove) should eliminate throttle events
   and reduce p999.

### Falsification
- If throttle events are rare (< 10 per minute) even under tight limits, or if
  spike windows show no association with throttle events, the hypothesis is false.
- If removing CPU limits (E3 → E1) does not improve p999 by ≥ 30%, throttling
  is not a significant driver.

### Experiments That Test It
- E3 (throttled), E5 (throttled + cross-node), E10 (throttled + contention)
- E1 (no throttle, control), E15 (tuned limits)

---

## H4 (Stretch): Cross-Node Placement Amplifies All Mechanisms

### Statement
> Cross-node pod placement amplifies the effect of all three kernel mechanisms
> (scheduling delay, softirq interference, throttling) by adding network
> serialization latency that extends the "vulnerable window" during which each
> mechanism can cause additional delay.

### Predicted Observation
- For every factor pair, the cross-node variant shows higher p999 than the
  same-node variant: E2 > E1, E5 > E3, E6 > E4, E7 > E10.
- The amplification ratio (cross-node p999 / same-node p999) increases with
  the severity of other stressors.

### Validation
- Paired comparisons across all SN/XN experiment pairs.
- Amplification ratio plot: x = stressor severity, y = XN/SN p999 ratio.

### Falsification
- If XN/SN ratio is constant (≈ 1.0 + fixed network RTT) regardless of
  stressor severity, amplification is purely additive (no interaction effect).
