// Auth validates orders and forwards to Risk.
package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/latency-attribution/latency-attribution/pkg/delay"
	"github.com/latency-attribution/latency-attribution/pkg/tracing"
	pb "github.com/latency-attribution/latency-attribution/proto/orderpb"
)

type server struct {
	pb.UnimplementedAuthServiceServer
	riskClient pb.RiskServiceClient
	delayCfg   delay.Config
}

func (s *server) ValidateOrder(ctx context.Context, req *pb.OrderRequest) (*pb.ValidationResult, error) {
	// Simulated compute (busy-spin)
	delay.Simulate(s.delayCfg)

	// Simple validation logic
	valid := req.Symbol != "" && req.Quantity > 0 && req.Price > 0

	// Call downstream: Risk
	riskResult, err := s.riskClient.CheckRisk(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("auth: risk call failed: %w", err)
	}

	reason := "validated"
	if !valid {
		reason = "invalid order fields"
	}

	return &pb.ValidationResult{
		OrderId:    req.OrderId,
		Valid:      valid && riskResult.Approved,
		Reason:     reason,
		RiskResult: riskResult,
	}, nil
}

func main() {
	listenAddr := envOrDefault("LISTEN_ADDR", ":50052")
	downstreamAddr := envOrDefault("DOWNSTREAM_ADDR", "localhost:50053")

	// Initialize OpenTelemetry
	shutdown, err := tracing.Init("auth")
	if err != nil {
		log.Fatalf("auth: failed to init tracing: %v", err)
	}
	defer shutdown(context.Background())

	conn, err := grpc.Dial(downstreamAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
	)
	if err != nil {
		log.Fatalf("auth: failed to connect to risk at %s: %v", downstreamAddr, err)
	}
	defer conn.Close()

	delayCfg := delay.LoadFromEnv()
	log.Printf("auth: delay=%v mode=%s", delayCfg.Duration, delayCfg.Mode)

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterAuthServiceServer(srv, &server{
		riskClient: pb.NewRiskServiceClient(conn),
		delayCfg:   delayCfg,
	})

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("auth: failed to listen on %s: %v", listenAddr, err)
	}

	log.Printf("auth: listening on %s → downstream %s", listenAddr, downstreamAddr)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("auth: serve failed: %v", err)
	}
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
