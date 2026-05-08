BIN   := bin/harness
TESTS := tests/*.yml

.PHONY: build
build:
	go build -o $(BIN) ./cmd/harness

.PHONY: test
test: build
	./$(BIN) test $(TESTS)

.PHONY: clean
clean:
	rm -rf bin/

# fixture-export: capture a fixture from the running demo.
# Usage: make fixture-export QUERY='...' START='...' END='...' OUT='fixtures/my-fixture' [REMAP_START='2025-01-01T00:00:00Z']
.PHONY: fixture-export
fixture-export: build
	./$(BIN) fixture export \
		--query '$(QUERY)' \
		--start '$(START)' \
		--end   '$(END)' \
		--out   '$(OUT)' \
		$(if $(REMAP_START),--remap-start '$(REMAP_START)')

# fixture-load: backfill a fixture into the running demo Prometheus.
# Usage: make fixture-load FIXTURE='fixtures/my-fixture' START='...' END='...'
.PHONY: fixture-load
fixture-load: build
	./$(BIN) fixture load \
		--fixture '$(FIXTURE)' \
		--rules   ../rules/adaptive.yml \
		--rules   ../rules/robust.yml \
		--start   '$(START)' \
		--end     '$(END)'
