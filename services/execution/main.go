// Execution is the terminal hop in the pipeline.
package main

import (
	"context"
	"log"
	"net"
	"os"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"

	"github.com/latency-attribution/latency-attribution/pkg/delay"
	"github.com/latency-attribution/latency-attribution/pkg/tracing"
	pb "github.com/latency-attribution/latency-attribution/proto/orderpb"
)

type server struct {
	pb.UnimplementedExecutionServiceServer
	delayCfg delay.Config
}

func (s *server) Execute(ctx context.Context, req *pb.ExecRequest) (*pb.ExecResult, error) {
	// Simulated compute (busy-spin)
	delay.Simulate(s.delayCfg)

	return &pb.ExecResult{
		OrderId:   req.OrderId,
		Filled:    true,
		FillPrice: req.Price,
		FillQty:   req.Quantity,
		Status:    "FILLED",
	}, nil
}

func main() {
	listenAddr := envOrDefault("LISTEN_ADDR", ":50055")

	// Initialize OpenTelemetry
	shutdown, err := tracing.Init("execution")
	if err != nil {
		log.Fatalf("execution: failed to init tracing: %v", err)
	}
	defer shutdown(context.Background())

	delayCfg := delay.LoadFromEnv()
	log.Printf("execution: delay=%v mode=%s", delayCfg.Duration, delayCfg.Mode)

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterExecutionServiceServer(srv, &server{
		delayCfg: delayCfg,
	})

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("execution: failed to listen on %s: %v", listenAddr, err)
	}

	log.Printf("execution: listening on %s (terminal hop)", listenAddr)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("execution: serve failed: %v", err)
	}
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
