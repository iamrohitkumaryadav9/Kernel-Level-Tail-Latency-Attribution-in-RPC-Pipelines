// Package tracing provides shared OpenTelemetry setup for all pipeline services.
package tracing

import (
	"context"
	"log"
	"os"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
)

// Init sets up OpenTelemetry with OTLP gRPC exporter (non-blocking).
// Traces are buffered and sent when the collector becomes available.
//
// Env vars:
//   - OTEL_EXPORTER_OTLP_ENDPOINT: Jaeger OTLP endpoint (default: localhost:4317)
//   - OTEL_SERVICE_NAME: override service name
func Init(serviceName string) (shutdown func(context.Context) error, err error) {
	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "localhost:4317"
	}

	if envName := os.Getenv("OTEL_SERVICE_NAME"); envName != "" {
		serviceName = envName
	}

	ctx := context.Background()

	// Non-blocking: exporter handles reconnection internally.
	// Services start immediately; traces buffer until collector is available.
	exporter, err := otlptracegrpc.New(ctx,
		otlptracegrpc.WithEndpoint(endpoint),
		otlptracegrpc.WithInsecure(),
		otlptracegrpc.WithTimeout(5*time.Second),
	)
	if err != nil {
		log.Printf("[tracing] WARNING: failed to create exporter: %v (tracing disabled)", err)
		return func(ctx context.Context) error { return nil }, nil
	}

	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceNameKey.String(serviceName),
		),
	)
	if err != nil {
		log.Printf("[tracing] WARNING: failed to create resource: %v", err)
		return func(ctx context.Context) error { return nil }, nil
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter,
			sdktrace.WithBatchTimeout(5*time.Second),
		),
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
	)

	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	log.Printf("[tracing] initialized: service=%s endpoint=%s", serviceName, endpoint)

	return tp.Shutdown, nil
}
