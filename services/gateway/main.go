// Gateway is the entry point for the RPC pipeline.
// It receives SubmitOrder, calls Auth downstream, and returns the response.
package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/latency-attribution/latency-attribution/pkg/delay"
	"github.com/latency-attribution/latency-attribution/pkg/tracing"
	pb "github.com/latency-attribution/latency-attribution/proto/orderpb"
)

type server struct {
	pb.UnimplementedGatewayServiceServer
	authClient pb.AuthServiceClient
	delayCfg   delay.Config
}

func (s *server) SubmitOrder(ctx context.Context, req *pb.OrderRequest) (*pb.OrderResponse, error) {
	start := time.Now()

	// Simulated compute (busy-spin)
	delay.Simulate(s.delayCfg)

	// Call downstream: Auth
	valResult, err := s.authClient.ValidateOrder(ctx, req)
	if err != nil {
		return nil, fmt.Errorf("gateway: auth call failed: %w", err)
	}

	elapsed := time.Since(start)

	return &pb.OrderResponse{
		OrderId:   req.OrderId,
		Accepted:  valResult.Valid,
		Message:   valResult.Reason,
		ExecPrice: valResult.GetRiskResult().GetQuoteResult().GetExecResult().GetFillPrice(),
		LatencyNs: elapsed.Nanoseconds(),
	}, nil
}

func main() {
	listenAddr := envOrDefault("LISTEN_ADDR", ":50051")
	downstreamAddr := envOrDefault("DOWNSTREAM_ADDR", "localhost:50052")

	// Initialize OpenTelemetry
	shutdown, err := tracing.Init("gateway")
	if err != nil {
		log.Fatalf("gateway: failed to init tracing: %v", err)
	}
	defer shutdown(context.Background())

	// Connect to Auth service with OTel client interceptor
	conn, err := grpc.Dial(downstreamAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4*1024*1024)),
	)
	if err != nil {
		log.Fatalf("gateway: failed to connect to auth at %s: %v", downstreamAddr, err)
	}
	defer conn.Close()

	delayCfg := delay.LoadFromEnv()
	log.Printf("gateway: delay=%v mode=%s", delayCfg.Duration, delayCfg.Mode)

	// Create gRPC server with OTel server interceptor
	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterGatewayServiceServer(srv, &server{
		authClient: pb.NewAuthServiceClient(conn),
		delayCfg:   delayCfg,
	})

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("gateway: failed to listen on %s: %v", listenAddr, err)
	}

	log.Printf("gateway: listening on %s → downstream %s", listenAddr, downstreamAddr)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("gateway: serve failed: %v", err)
	}
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
