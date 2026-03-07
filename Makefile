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

# ── C++ HFT Components ────────────────────────────
cpp-build:
	@mkdir -p cpp/build
	cd cpp/build && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$$(nproc)
	@echo "✓ C++ components built → cpp/build/"

cpp-test:
	cd cpp/build && ctest --output-on-failure
	@echo "✓ C++ tests passed"

cpp-test-quick:
	@echo "Building and running unit tests directly..."
	g++ -std=c++20 -O2 -march=native -o /tmp/test_spsc cpp/tests/test_spsc_ring.cpp -lgtest -lgtest_main -lpthread
	g++ -std=c++20 -O2 -march=native -o /tmp/test_hdr cpp/tests/test_hdr_histogram.cpp -lgtest -lgtest_main -lpthread
	g++ -std=c++20 -O2 -march=native -o /tmp/test_orderbook cpp/tests/test_order_book.cpp -lgtest -lgtest_main -lpthread
	/tmp/test_spsc && /tmp/test_hdr && /tmp/test_orderbook
	@echo "✓ all unit tests passed"

cpp-clean:
	rm -rf cpp/build/
	@echo "✓ C++ build cleaned"
