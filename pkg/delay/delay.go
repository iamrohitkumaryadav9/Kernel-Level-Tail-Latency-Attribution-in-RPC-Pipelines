// Package delay provides scheduler-safe busy-spin delay for latency benchmarking.
//
// time.Sleep is BANNED for sub-millisecond delays because it injects scheduler
// artifacts (timer granularity, wakeup delay) — exactly the effects we measure.
package delay

import (
	"crypto/sha256"
	"fmt"
	"os"
	"strconv"
	"sync"
	"time"
)

// Mode determines how simulated compute delay is generated.
type Mode string

const (
	// ModeBusySpin loops on time.Now() until the target duration elapses.
	ModeBusySpin Mode = "busyspin"
	// ModeCompute performs real SHA-256 work, calibrated at startup.
	ModeCompute Mode = "compute"
)

// Config holds delay configuration, typically loaded from env vars.
type Config struct {
	Duration time.Duration
	Mode     Mode
}

// LoadFromEnv reads DELAY_US (microseconds) and DELAY_MODE from environment.
// Defaults: 50µs busy-spin.
func LoadFromEnv() Config {
	dur := 50 * time.Microsecond
	if v := os.Getenv("DELAY_US"); v != "" {
		if us, err := strconv.Atoi(v); err == nil && us >= 0 {
			dur = time.Duration(us) * time.Microsecond
		}
	}

	mode := ModeBusySpin
	if v := os.Getenv("DELAY_MODE"); v == "compute" {
		mode = ModeCompute
	}

	return Config{Duration: dur, Mode: mode}
}

// Simulate executes the configured delay.
// It NEVER calls time.Sleep for sub-millisecond durations.
func Simulate(cfg Config) {
	if cfg.Duration <= 0 {
		return
	}
	switch cfg.Mode {
	case ModeCompute:
		computeDelay(cfg.Duration)
	default:
		busySpin(cfg.Duration)
	}
}

// busySpin spins on CLOCK_MONOTONIC (via time.Now) until target elapses.
func busySpin(target time.Duration) {
	start := time.Now()
	for time.Since(start) < target {
		// Intentionally empty — pure spin-wait
	}
}

// calibration state for compute mode
var (
	calibrateOnce    sync.Once
	itersPerMicro    int64 // SHA-256 iterations per microsecond
)

// calibrate measures how many SHA-256 iterations fit in 1ms, then divides.
func calibrate() {
	calibrateOnce.Do(func() {
		data := []byte("calibration-payload-for-latency-benchmark")
		const calibrationRuns = 10000
		start := time.Now()
		for i := 0; i < calibrationRuns; i++ {
			h := sha256.Sum256(data)
			data = h[:] // prevent optimization
		}
		elapsed := time.Since(start)
		if elapsed > 0 {
			itersPerMicro = int64(calibrationRuns) * int64(time.Microsecond) / int64(elapsed)
			if itersPerMicro < 1 {
				itersPerMicro = 1
			}
		} else {
			itersPerMicro = 1
		}
		fmt.Fprintf(os.Stderr, "[delay] calibrated: %d SHA-256 iters/µs\n", itersPerMicro)
	})
}

// computeDelay performs real SHA-256 work for approximately the target duration.
func computeDelay(target time.Duration) {
	calibrate()
	iters := int64(target/time.Microsecond) * itersPerMicro
	data := []byte("compute-delay-payload")
	for i := int64(0); i < iters; i++ {
		h := sha256.Sum256(data)
		data = h[:]
	}
}
