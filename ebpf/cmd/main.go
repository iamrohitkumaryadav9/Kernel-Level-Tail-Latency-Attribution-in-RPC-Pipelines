package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/cilium/ebpf"
	"github.com/cilium/ebpf/link"
	"github.com/cilium/ebpf/rlimit"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -target amd64 rqdelay ../src/rqdelay.bpf.c -- -I../headers

// softirq vector names (Linux kernel)
var softirqNames = [10]string{
	"HI", "TIMER", "NET_TX", "NET_RX", "BLOCK",
	"IRQ_POLL", "TASKLET", "SCHED", "HRTIMER", "RCU",
}

var (
	// Wakeup delay histogram buckets
	rqdelayBucket = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "rqdelay_bucket_count",
			Help: "Runqueue delay histogram bucket counts (log2(us) buckets).",
		},
		[]string{"bucket"},
	)

	rqdelayP50 = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "rqdelay_p50_us",
		Help: "Approximate p50 wakeup-to-run delay in microseconds.",
	})

	rqdelayP99 = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "rqdelay_p99_us",
		Help: "Approximate p99 wakeup-to-run delay in microseconds.",
	})

	// softirq metrics (Blueprint §4.2.4)
	softirqTimeGauge = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "rqdelay_softirq_time_ns",
			Help: "Cumulative softirq processing time in nanoseconds per vector.",
		},
		[]string{"vector"},
	)

	softirqCountGauge = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "rqdelay_softirq_count",
			Help: "Cumulative softirq event count per vector.",
		},
		[]string{"vector"},
	)

	// Per-cgroup (pod) delay (Blueprint §5.4)
	cgroupDelayP99 = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "rqdelay_cgroup_p99_us",
			Help: "Per-cgroup (pod) approximate p99 wakeup-to-run delay in microseconds.",
		},
		[]string{"cgroup_id", "pod"},
	)

	cgroupDelaySamples = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "rqdelay_cgroup_samples",
			Help: "Total scheduling events observed per cgroup.",
		},
		[]string{"cgroup_id", "pod"},
	)

	// TCP retransmit (Blueprint §4.3)
	tcpRetransmitTotal = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "rqdelay_tcp_retransmit_total",
		Help: "Total TCP retransmit events observed.",
	})
)

func init() {
	prometheus.MustRegister(rqdelayBucket)
	prometheus.MustRegister(rqdelayP50)
	prometheus.MustRegister(rqdelayP99)
	prometheus.MustRegister(softirqTimeGauge)
	prometheus.MustRegister(softirqCountGauge)
	prometheus.MustRegister(cgroupDelayP99)
	prometheus.MustRegister(cgroupDelaySamples)
	prometheus.MustRegister(tcpRetransmitTotal)
}

// cgroupHistKey matches the BPF struct cgroup_hist_key
type cgroupHistKey struct {
	CgroupID uint64
	Bucket   uint32
	Pad      uint32
}

func main() {
	if err := rlimit.RemoveMemlock(); err != nil {
		log.Fatal(err)
	}

	objs := rqdelayObjects{}
	if err := loadRqdelayObjects(&objs, nil); err != nil {
		log.Fatalf("loading objects: %v", err)
	}
	defer objs.Close()

	// Attach scheduling tracepoints
	tp1, err := link.Tracepoint("sched", "sched_wakeup", objs.TpSchedWakeup, nil)
	if err != nil {
		log.Fatalf("attaching sched_wakeup: %v", err)
	}
	defer tp1.Close()

	tp2, err := link.Tracepoint("sched", "sched_wakeup_new", objs.TpSchedWakeupNew, nil)
	if err != nil {
		log.Fatalf("attaching sched_wakeup_new: %v", err)
	}
	defer tp2.Close()

	tp3, err := link.Tracepoint("sched", "sched_switch", objs.TpSchedSwitch, nil)
	if err != nil {
		log.Fatalf("attaching sched_switch: %v", err)
	}
	defer tp3.Close()

	// Attach softirq tracepoints (Blueprint §4.2.4)
	tp4, err := link.Tracepoint("irq", "softirq_entry", objs.TpSoftirqEntry, nil)
	if err != nil {
		log.Printf("WARNING: attaching softirq_entry failed: %v (softirq metrics disabled)", err)
	} else {
		defer tp4.Close()
	}

	tp5, err := link.Tracepoint("irq", "softirq_exit", objs.TpSoftirqExit, nil)
	if err != nil {
		log.Printf("WARNING: attaching softirq_exit failed: %v (softirq metrics disabled)", err)
	} else {
		defer tp5.Close()
	}

	// Attach TCP retransmit tracepoint (Blueprint §4.3)
	tp6, err := link.Tracepoint("tcp", "tcp_retransmit_skb", objs.TpTcpRetransmit, nil)
	if err != nil {
		log.Printf("WARNING: attaching tcp_retransmit_skb failed: %v (retransmit metrics disabled)", err)
	} else {
		defer tp6.Close()
	}

	// Metrics server
	go func() {
		http.Handle("/metrics", promhttp.Handler())
		log.Println("Metrics server listening on :9090")
		if err := http.ListenAndServe(":9090", nil); err != nil {
			log.Fatalf("metrics server failed: %v", err)
		}
	}()

	// Poll maps and export metrics
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		ncpu := runtime.NumCPU()

		for range ticker.C {
			// Read wakeup delay histogram
			buckets, p50, p99, err := readPercpuArray(objs.RunDelayHist, 64, ncpu)
			if err != nil {
				log.Printf("failed reading run_delay_hist: %v", err)
			} else {
				for i := 0; i < 64; i++ {
					rqdelayBucket.WithLabelValues(strconv.Itoa(i)).Set(float64(buckets[i]))
				}
				rqdelayP50.Set(float64(p50))
				rqdelayP99.Set(float64(p99))
			}

			// Read softirq time per vector
			sirqTime, _, _, err := readPercpuArray(objs.SoftirqTime, 10, ncpu)
			if err != nil {
				log.Printf("failed reading softirq_time: %v", err)
			} else {
				for i := 0; i < 10; i++ {
					softirqTimeGauge.WithLabelValues(softirqNames[i]).Set(float64(sirqTime[i]))
				}
			}

			// Read softirq count per vector
			sirqCnt, _, _, err := readPercpuArray(objs.SoftirqCount, 10, ncpu)
			if err != nil {
				log.Printf("failed reading softirq_count: %v", err)
			} else {
				for i := 0; i < 10; i++ {
					softirqCountGauge.WithLabelValues(softirqNames[i]).Set(float64(sirqCnt[i]))
				}
			}

			// Read TCP retransmit count
			readTcpRetransmit(objs.TcpRetransmitCount, ncpu)

			// Read per-cgroup histograms
			readCgroupHistogram(objs.CgroupDelayHist, objs.CgroupIds)
		}
	}()

	log.Println("Rqdelay eBPF tool started (sched + softirq + cgroup + tcp). Press Ctrl+C to exit...")

	stopper := make(chan os.Signal, 1)
	signal.Notify(stopper, os.Interrupt, syscall.SIGTERM)
	<-stopper
}

// readPercpuArray reads a PERCPU_ARRAY map and sums values across CPUs.
func readPercpuArray(m *ebpf.Map, entries int, ncpu int) ([]uint64, uint64, uint64, error) {
	out := make([]uint64, entries)

	for i := 0; i < entries; i++ {
		key := uint32(i)
		values := make([]uint64, ncpu)

		if err := m.Lookup(&key, &values); err != nil {
			return out, 0, 0, err
		}

		var sum uint64
		for _, v := range values {
			sum += v
		}
		out[i] = sum
	}

	p50 := estimateQuantile(out, 0.50)
	p99 := estimateQuantile(out, 0.99)

	return out, p50, p99, nil
}

// estimateQuantile estimates quantiles from log2(us) buckets.
func estimateQuantile(buckets []uint64, q float64) uint64 {
	var total uint64
	for _, c := range buckets {
		total += c
	}
	if total == 0 {
		return 0
	}

	target := uint64(float64(total) * q)
	if target == 0 {
		target = 1
	}

	var cum uint64
	for i, c := range buckets {
		cum += c
		if cum >= target {
			if i == 0 {
				return 1
			}
			low := uint64(1) << uint(i)
			high := uint64(1) << uint(i+1)
			return (low + high) / 2
		}
	}
	return uint64(1) << 63
}

// readTcpRetransmit reads the TCP retransmit counter
func readTcpRetransmit(m *ebpf.Map, ncpu int) {
	key := uint32(0)
	values := make([]uint64, ncpu)
	if err := m.Lookup(&key, &values); err != nil {
		return
	}
	var total uint64
	for _, v := range values {
		total += v
	}
	tcpRetransmitTotal.Set(float64(total))
}

// readCgroupHistogram reads the per-cgroup delay histogram and exports metrics
func readCgroupHistogram(histMap, cgroupIdsMap *ebpf.Map) {
	// First enumerate known cgroup IDs
	var cgID uint64
	var firstSeen uint64
	iter := cgroupIdsMap.Iterate()
	cgroups := make(map[uint64]bool)
	for iter.Next(&cgID, &firstSeen) {
		cgroups[cgID] = true
	}

	// For each cgroup, build per-cgroup histogram
	for cg := range cgroups {
		cgBuckets := make([]uint64, 64)
		for bucket := uint32(0); bucket < 64; bucket++ {
			key := make([]byte, 16) // sizeof(cgroup_hist_key) = 16
			binary.LittleEndian.PutUint64(key[0:8], cg)
			binary.LittleEndian.PutUint32(key[8:12], bucket)
			// pad is 0
			var val uint64
			if err := histMap.Lookup(key, &val); err == nil {
				cgBuckets[bucket] = val
			}
		}
		p99 := estimateQuantile(cgBuckets, 0.99)
		var total uint64
		for _, c := range cgBuckets {
			total += c
		}

		cgStr := fmt.Sprintf("%d", cg)
		podName := resolveCgroupToPod(cg)
		cgroupDelayP99.WithLabelValues(cgStr, podName).Set(float64(p99))
		cgroupDelaySamples.WithLabelValues(cgStr, podName).Set(float64(total))
	}
}

// resolveCgroupToPod attempts to map a cgroup ID to a pod name
// This is a best-effort resolution via /proc/1/cgroup or crictl
func resolveCgroupToPod(cgroupID uint64) string {
	// Try to read from a cached mapping file (populated by a sidecar)
	data, err := os.ReadFile("/tmp/cgroup-pod-map.txt")
	if err == nil {
		cgStr := fmt.Sprintf("%d", cgroupID)
		for _, line := range strings.Split(string(data), "\n") {
			parts := strings.SplitN(line, "=", 2)
			if len(parts) == 2 && parts[0] == cgStr {
				return parts[1]
			}
		}
	}

	// Fallback: try crictl (if available)
	out, err := exec.Command("crictl", "pods", "-o", "json").Output()
	if err == nil {
		_ = out // TODO: parse JSON to match cgroup ID to pod name
	}

	return fmt.Sprintf("cgroup-%d", cgroupID)
}
