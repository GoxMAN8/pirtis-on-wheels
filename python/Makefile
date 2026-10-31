# Everyday tasks. `make check` spends no model tokens; `make gate` does.
#
#   make check                 lint, types, tests
#   make gate                  every suite, 3 runs each, all 3 must pass (the frozen gate)
#   make gate RUNS=1 NEED=1    the same wiring, one run each, for a quick look
#   make eval-extract          the model's understanding against gold, 3 runs per fixture
#   make trace ID=silence      one fixture, verbose, traces kept under runs/trace

SHELL := /bin/bash

RUNS ?= 3
NEED ?= 3
SUITES ?= ../fixtures ../fixtures/extra $(wildcard ../fixtures/variants)
ID ?=
OURS := pirtis/agent pirtis/ops

# gpt-4o-mini list prices in USD per token, for the cost line under the gate
PRICE_IN := 0.00000015
PRICE_OUT := 0.0000006

.PHONY: install lint format typecheck test check gate eval-extract trace

install:
	uv sync

lint:
	uv run ruff check $(OURS)
	uv run ruff format --check $(OURS)

format:
	uv run ruff check --fix $(OURS)
	uv run ruff format $(OURS)

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

# Runs every suite even when one fails, then prints what the agent's model calls cost
# (the judge's calls are not in the harness counts) and exits non-zero on any failure.
# pipefail is set inside the recipe because make 3.81 (macOS) ignores .SHELLFLAGS, and
# without it tee would hide the harness's exit code and the gate could never fail.
gate:
	@rm -rf runs/gate && mkdir -p runs/gate
	@set -o pipefail; status=0; \
	for suite in $(SUITES); do \
		name=$$(basename $$suite); \
		echo "== $$suite: $(RUNS) runs, $(NEED) must pass"; \
		uv run harness --fixtures $$suite --runs $(RUNS) --need $(NEED) \
			--trace runs/gate/$$name | tee runs/gate/$$name.txt || status=1; \
	done; \
	awk -v pin=$(PRICE_IN) -v pout=$(PRICE_OUT) \
		'$$3 == "PASS" || $$3 == "FAIL" { split($$5, t, "/"); calls += $$4; tin += t[1]; tout += t[2] } \
		END { printf "\nagent model calls %d, tokens in/out %d/%d, est. $$%.4f (judge calls not counted)\n", \
			calls, tin, tout, tin * pin + tout * pout }' runs/gate/*.txt; \
	exit $$status

trace:
	@test -n "$(ID)" || { echo "usage: make trace ID=<fixture id>"; exit 2; }
	uv run harness --id $(ID) --runs 1 -v --trace runs/trace

eval-extract:
	@mkdir -p runs
	uv run python -m pirtis.ops.extract_eval --runs $(RUNS) $(if $(ID),--id $(ID)) --json runs/extract-eval.json
