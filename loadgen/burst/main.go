package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	orderpb "github.com/latency-attribution/latency-attribution/proto/orderpb"
)

/*
Burst-mode load generator — Blueprint §2.1 / §3.4

Generates:
  - Base rate: 1000 req/s (steady)
  - Burst:     5000 req/s for 50ms every 2 seconds
  - Duration:  configurable (default 120s)

Output: per-request latency to stdout in CSV format for analysis
*/

var (
	target    = flag.String("target", "localhost:50051", "gateway gRPC address")
	baseRPS   = flag.Int("base-rps", 1000, "base request rate (req/s)")
	burstRPS  = flag.Int("burst-rps", 5000, "burst request rate (req/s)")
	burstDur  = flag.Duration("burst-dur", 50*time.Millisecond, "burst duration")
	period    = flag.Duration("period", 2*time.Second, "burst period (time between bursts)")
	duration  = flag.Duration("duration", 120*time.Second, "total test duration")
	output    = flag.String("output", "", "output CSV file (default: stdout)")
	warmup    = flag.Duration("warmup", 10*time.Second, "warmup duration (not recorded)")
)

type result struct {
	timestamp int64
	latencyUs int64
	ok        bool
	burst     bool
}

func main() {
	flag.Parse()

	conn, err := grpc.Dial(*target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("dial failed: %v", err)
	}
	defer conn.Close()

	client := orderpb.NewGatewayServiceClient(conn)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle signals
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sig
		cancel()
	}()

	// Open output
	var out *os.File
	if *output != "" {
		out, err = os.Create(*output)
		if err != nil {
			log.Fatalf("create output: %v", err)
		}
		defer out.Close()
	} else {
		out = os.Stdout
	}
	fmt.Fprintln(out, "timestamp_ns,latency_us,ok,burst")

	results := make(chan result, 100000)
	var wg sync.WaitGroup
	var totalSent, totalOK, totalErr atomic.Int64

	// Writer goroutine
	go func() {
		for r := range results {
			okStr := "true"
			if !r.ok {
				okStr = "false"
			}
			burstStr := "false"
			if r.burst {
				burstStr = "true"
			}
			fmt.Fprintf(out, "%d,%d,%s,%s\n", r.timestamp, r.latencyUs, okStr, burstStr)
		}
	}()

	sendRequest := func(isBurst bool, recording bool) {
		defer wg.Done()
		orderID := fmt.Sprintf("BURST-%d-%d", time.Now().UnixNano(), rand.Intn(100000))
		req := &orderpb.OrderRequest{
			OrderId:  orderID,
			Symbol:   "AAPL",
			Quantity: 100,
			Price:    150.25,
		}

		start := time.Now()
		_, err := client.SubmitOrder(ctx, req)
		elapsed := time.Since(start)
		totalSent.Add(1)

		ok := err == nil
		if ok {
			totalOK.Add(1)
		} else {
			totalErr.Add(1)
		}

		if recording {
			results <- result{
				timestamp: start.UnixNano(),
				latencyUs: elapsed.Microseconds(),
				ok:        ok,
				burst:     isBurst,
			}
		}
	}

	// Warmup phase
	if *warmup > 0 {
		log.Printf("Warmup: %v at %d rps...", *warmup, *baseRPS)
		warmupEnd := time.Now().Add(*warmup)
		interval := time.Second / time.Duration(*baseRPS)
		warmupTick := time.NewTicker(interval)
		for time.Now().Before(warmupEnd) {
			select {
			case <-warmupTick.C:
				wg.Add(1)
				go sendRequest(false, false)
			case <-ctx.Done():
				warmupTick.Stop()
				goto done
			}
		}
		warmupTick.Stop()
		wg.Wait()
		log.Println("Warmup complete")
	}

	// Main measurement phase
	{
		log.Printf("Measurement: %v, base=%d rps, burst=%d rps for %v every %v",
			*duration, *baseRPS, *burstRPS, *burstDur, *period)

		startTime := time.Now()
		endTime := startTime.Add(*duration)
		lastBurst := startTime

		baseInterval := time.Second / time.Duration(*baseRPS)
		burstInterval := time.Second / time.Duration(*burstRPS)

		ticker := time.NewTicker(baseInterval)
		defer ticker.Stop()

		for time.Now().Before(endTime) {
			select {
			case now := <-ticker.C:
				// Check if we should burst
				timeSinceLastBurst := now.Sub(lastBurst)
				isBursting := timeSinceLastBurst >= *period && timeSinceLastBurst < *period+*burstDur

				if isBursting {
					// During burst: send at burst rate
					ticker.Reset(burstInterval)
					wg.Add(1)
					go sendRequest(true, true)
				} else {
					if timeSinceLastBurst >= *period+*burstDur {
						lastBurst = now
					}
					ticker.Reset(baseInterval)
					wg.Add(1)
					go sendRequest(false, true)
				}
			case <-ctx.Done():
				goto done
			}
		}
	}

done:
	log.Printf("Waiting for in-flight requests...")
	wg.Wait()
	close(results)
	time.Sleep(100 * time.Millisecond) // let writer flush

	log.Printf("Done. Sent=%d  OK=%d  Err=%d",
		totalSent.Load(), totalOK.Load(), totalErr.Load())
}
