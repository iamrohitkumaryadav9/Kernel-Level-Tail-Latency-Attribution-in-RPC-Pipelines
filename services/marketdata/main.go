// MarketData fetches a quote from Redis and forwards to Execution.
package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"strconv"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/latency-attribution/latency-attribution/pkg/delay"
	"github.com/latency-attribution/latency-attribution/pkg/tracing"
	pb "github.com/latency-attribution/latency-attribution/proto/orderpb"
)

var tracer = otel.Tracer("marketdata")

type server struct {
	pb.UnimplementedMarketDataServiceServer
	execClient  pb.ExecutionServiceClient
	redisClient *redis.Client
	delayCfg    delay.Config
}

func (s *server) GetQuote(ctx context.Context, req *pb.QuoteRequest) (*pb.QuoteResult, error) {
	// Simulated compute (busy-spin)
	delay.Simulate(s.delayCfg)

	// Redis GET with manual span (external dependency)
	ctx, redisSpan := tracer.Start(ctx, "redis.GET")
	bidStr, err := s.redisClient.Get(ctx, "quote:"+req.Symbol+":bid").Result()
	if err == redis.Nil {
		bidStr = "150.00"
	} else if err != nil {
		log.Printf("marketdata: redis GET failed: %v (using default)", err)
		bidStr = "150.00"
	}
	askStr, err := s.redisClient.Get(ctx, "quote:"+req.Symbol+":ask").Result()
	if err == redis.Nil {
		askStr = "150.05"
	} else if err != nil {
		askStr = "150.05"
	}
	redisSpan.SetAttributes(
		attribute.String("redis.key_prefix", "quote:"+req.Symbol),
		attribute.String("redis.bid", bidStr),
		attribute.String("redis.ask", askStr),
	)
	redisSpan.End()

	bid, _ := strconv.ParseFloat(bidStr, 64)
	ask, _ := strconv.ParseFloat(askStr, 64)

	// Call downstream: Execution
	execResult, err := s.execClient.Execute(ctx, &pb.ExecRequest{
		OrderId:  req.OrderId,
		Symbol:   req.Symbol,
		Quantity: 100,
		Price:    ask,
	})
	if err != nil {
		return nil, fmt.Errorf("marketdata: execution call failed: %w", err)
	}

	return &pb.QuoteResult{
		OrderId:    req.OrderId,
		Symbol:     req.Symbol,
		BidPrice:   bid,
		AskPrice:   ask,
		ExecResult: execResult,
	}, nil
}

func main() {
	listenAddr := envOrDefault("LISTEN_ADDR", ":50054")
	downstreamAddr := envOrDefault("DOWNSTREAM_ADDR", "localhost:50055")
	redisAddr := envOrDefault("REDIS_ADDR", "localhost:6379")

	// Initialize OpenTelemetry
	shutdown, err := tracing.Init("marketdata")
	if err != nil {
		log.Fatalf("marketdata: failed to init tracing: %v", err)
	}
	defer shutdown(context.Background())

	conn, err := grpc.Dial(downstreamAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
	)
	if err != nil {
		log.Fatalf("marketdata: failed to connect to execution at %s: %v", downstreamAddr, err)
	}
	defer conn.Close()

	rdb := redis.NewClient(&redis.Options{
		Addr:     redisAddr,
		PoolSize: 10,
	})

	// Seed default quotes
	ctx := context.Background()
	rdb.SetNX(ctx, "quote:AAPL:bid", "150.00", 0)
	rdb.SetNX(ctx, "quote:AAPL:ask", "150.05", 0)

	delayCfg := delay.LoadFromEnv()
	log.Printf("marketdata: delay=%v mode=%s redis=%s", delayCfg.Duration, delayCfg.Mode, redisAddr)

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterMarketDataServiceServer(srv, &server{
		execClient:  pb.NewExecutionServiceClient(conn),
		redisClient: rdb,
		delayCfg:    delayCfg,
	})

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("marketdata: failed to listen on %s: %v", listenAddr, err)
	}

	log.Printf("marketdata: listening on %s → downstream %s", listenAddr, downstreamAddr)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("marketdata: serve failed: %v", err)
	}
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
