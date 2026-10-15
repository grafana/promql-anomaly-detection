PROMTOOL_IMAGE := prom/prometheus:v3.11.3@sha256:e4254400b85610324913f0dc4acf92603d9984e7519414c5a12811aa6146acc3

# test: run the rule unit tests under tests/ with promtool.
.PHONY: test
test:
	docker run --rm -v "$(CURDIR):/repo:ro" -w /repo --entrypoint promtool $(PROMTOOL_IMAGE) \
		test rules tests/*.yml
