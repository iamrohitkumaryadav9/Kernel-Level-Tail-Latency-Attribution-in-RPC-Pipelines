PROJECT  := latency-attribution
MODULE   := github.com/latency-attribution/latency-attribution
SERVICES := gateway auth risk marketdata execution
PROTO_SRC := proto/order.proto
PROTO_OUT := proto/orderpb

.PHONY: all proto build test clean docker-build

all: proto build

# ── Proto codegen ──────────────────────────────────
proto:
	@mkdir -p $(PROTO_OUT)
	protoc \
		--go_out=$(PROTO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(PROTO_OUT) --go-grpc_opt=paths=source_relative \
		-I proto \
		proto/order.proto
	@echo "✓ proto generated → $(PROTO_OUT)/"

# ── Build all services ─────────────────────────────
build:
	@for svc in $(SERVICES); do \
		echo "Building $$svc..."; \
		go build -o bin/$$svc ./services/$$svc/; \
	done
	@echo "✓ all services built → bin/"

# ── Tests ──────────────────────────────────────────
test:
	go test ./pkg/delay/ -v -count=1
	@echo "✓ tests passed"

# ── Sleep ban check ────────────────────────────────
lint-sleep:
	@echo "Checking for banned time.Sleep in services/..."
	@if grep -rn 'time\.Sleep' services/; then \
		echo "✗ BANNED: time.Sleep found in service code"; \
		exit 1; \
	else \
		echo "✓ no time.Sleep in services/"; \
	fi

# ── Docker builds ──────────────────────────────────
docker-build:
	@for svc in $(SERVICES); do \
		echo "Building docker image: $(PROJECT)-$$svc"; \
		docker build --build-arg SERVICE=$$svc -t $(PROJECT)-$$svc:latest .; \
	done
	@echo "✓ all docker images built"

# ── eBPF build ─────────────────────────────────────
docker-build-ebpf:
	docker build -t latency-attribution-rqdelay:latest -f ebpf/Dockerfile ebpf/

# ── Kubernetes deploy ──────────────────────────────
deploy-base:
	kubectl apply -k deploy/base/

deploy-%:
	kubectl apply -k deploy/overlays/$*/

undeploy:
	kubectl delete -k deploy/base/ --ignore-not-found

# ── Clean ──────────────────────────────────────────
clean:
	rm -rf bin/ $(PROTO_OUT)/
	@echo "✓ cleaned"
