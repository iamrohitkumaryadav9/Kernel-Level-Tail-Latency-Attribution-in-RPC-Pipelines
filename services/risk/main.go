// Risk checks order risk and forwards to MarketData.
package main

import (
	"context"
	"fmt"
	"log"
	"math"
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
	pb.UnimplementedRiskServiceServer
	mdClient pb.MarketDataServiceClient
	delayCfg delay.Config
}

func (s *server) CheckRisk(ctx context.Context, req *pb.OrderRequest) (*pb.RiskResult, error) {
	// Simulated compute (busy-spin)
	delay.Simulate(s.delayCfg)

	// Simple risk scoring
	notional := math.Abs(req.Price * float64(req.Quantity))
	riskScore := notional / 1_000_000.0
	approved := riskScore < 10.0

	// Call downstream: MarketData
	quoteResult, err := s.mdClient.GetQuote(ctx, &pb.QuoteRequest{
		OrderId: req.OrderId,
		Symbol:  req.Symbol,
	})
	if err != nil {
		return nil, fmt.Errorf("risk: marketdata call failed: %w", err)
	}

	return &pb.RiskResult{
		OrderId:     req.OrderId,
		Approved:    approved,
		RiskScore:   riskScore,
		QuoteResult: quoteResult,
	}, nil
}

func main() {
	listenAddr := envOrDefault("LISTEN_ADDR", ":50053")
	downstreamAddr := envOrDefault("DOWNSTREAM_ADDR", "localhost:50054")

	// Initialize OpenTelemetry
	shutdown, err := tracing.Init("risk")
	if err != nil {
		log.Fatalf("risk: failed to init tracing: %v", err)
	}
	defer shutdown(context.Background())

	conn, err := grpc.Dial(downstreamAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
	)
	if err != nil {
		log.Fatalf("risk: failed to connect to marketdata at %s: %v", downstreamAddr, err)
	}
	defer conn.Close()

	delayCfg := delay.LoadFromEnv()
	log.Printf("risk: delay=%v mode=%s", delayCfg.Duration, delayCfg.Mode)

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterRiskServiceServer(srv, &server{
		mdClient: pb.NewMarketDataServiceClient(conn),
		delayCfg: delayCfg,
	})

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("risk: failed to listen on %s: %v", listenAddr, err)
	}

	log.Printf("risk: listening on %s → downstream %s", listenAddr, downstreamAddr)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("risk: serve failed: %v", err)
	}
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
