# Everyday tasks. `make check` spends no model tokens; `make gate` does.
#
#   make check                 lint, types, tests
#   make gate                  every suite, 3 runs each, all 3 must pass (the frozen gate)
#   make gate RUNS=1 NEED=1    the same wiring, one run each, for a quick look
#   make eval-extract          the model's understanding against gold, 3 runs per fixture
#   make trace ID=silence      one fixture, verbose, traces kept under runs/trace
#   make gate-job              the nightly cloud job, run here: given + red team, saved to var/
#   make heldout               the generated variants, once, scored by transform (never tuned on)
#   make heldout-rules         the same variants at zero tokens: do the rules hold if heard right?
#   make review                a second model re-reads recent calls, queues disagreements
#   make fixtures-doc          rewrite docs/fixtures.md from the test cases
#   make pdfs                  every document as a PDF (needs Chrome)

SHELL := /bin/bash

RUNS ?= 3
NEED ?= 3
SUITES ?= ../fixtures ../fixtures/extra $(wildcard ../fixtures/variants)
ID ?=
OURS := pirtis/agent pirtis/ops pirtis/playground

.PHONY: install lint format typecheck test check gate gate-job eval-extract trace playground variants variants-accept reliability calibrate heldout heldout-rules review fixtures-doc pdfs

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

# Runs every suite even when one fails, then prints the gate report (pass^k overall and
# split into deterministic checks and the judge, cost, latency; see pirtis/ops/gate_report.py)
# and exits non-zero on any failure.
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
	uv run python -m pirtis.ops.gate_report --dir runs/gate --json runs/gate/summary.json; \
	uv run python -m pirtis.ops.record_gate --dir runs/gate; \
	exit $$status

# The same gate in one process, as the cloud job runs it (pirtis/ops/gate_job.py): saves the
# run and its turn records, prunes, prints one JSON line. SUITES_JOB=all for every suite.
SUITES_JOB ?= nightly
gate-job:
	uv run gate-job --suites $(SUITES_JOB)

trace:
	@test -n "$(ID)" || { echo "usage: make trace ID=<fixture id>"; exit 2; }
	uv run harness --id $(ID) --runs 1 -v --trace runs/trace

eval-extract:
	@mkdir -p runs
	uv run python -m pirtis.ops.extract_eval --runs $(RUNS) $(if $(ID),--id $(ID)) --json runs/extract-eval.json

# The caller simulator (/) and the admin console (/admin) on http://127.0.0.1:8000.
# Calls, feedback and gate runs are kept in var/ (PIRTIS_DATA_DIR).
playground:
	uv run playground

# Metamorphic variants of the given cases, checked by the kit's API (pirtis/ops/variants.py).
# They go to fixtures/variants-candidates with a review table; a person reads REVIEW.md and
# runs variants-accept, which moves them into fixtures/variants, where make gate runs them.
variants:
	uv run python -m pirtis.ops.variants --out ../fixtures/variants-candidates

variants-accept:
	@test -f ../fixtures/variants-candidates/REVIEW.md || { echo "make variants first"; exit 2; }
	@mkdir -p ../fixtures/variants && ln -sfn ../seeds ../fixtures/variants/seeds
	mv ../fixtures/variants-candidates/*.yaml ../fixtures/variants/
	mv ../fixtures/variants-candidates/REVIEW.md ../fixtures/variants/REVIEW.md
	@echo "accepted: $$(ls ../fixtures/variants/*.yaml | wc -l) variants now run in make gate"

# The held-out score (pirtis/ops/heldout.py): the 59 generated variants, 3 runs each, all 3
# must pass, reported by transform (persona, shift, noise, tow). Run it once per batch,
# before tuning anything on it; a variant used to fix something is development data after.
# About $0.07 of model calls plus the judge. HELDOUT=../fixtures/variants once accepted.
HELDOUT ?= ../fixtures/variants-candidates
heldout:
	@rm -rf runs/heldout && mkdir -p runs/heldout
	@set -o pipefail; name=$$(basename $(HELDOUT)); \
	uv run harness --fixtures $(HELDOUT) --runs 3 --need 3 --trace runs/heldout/$$name \
		| tee runs/heldout/$$name.txt; \
	uv run python -m pirtis.ops.heldout --dir runs/heldout --json runs/heldout/summary.json

# The rules on the held-out variants with the caller heard right, at zero tokens: the kit's
# checks plus "decides as its source did" (pirtis/ops/variant_replay.py). Also in make check.
heldout-rules:
	uv run python -m pirtis.ops.variant_replay

# pass^k for k = 1..5 on every suite (variant candidates included), from 5 runs of each case.
# A measurement, not the gate: about $0.30 of model calls with the judge.
reliability:
	@rm -rf runs/reliability && mkdir -p runs/reliability
	@for suite in ../fixtures ../fixtures/extra $(wildcard ../fixtures/variants) $(wildcard ../fixtures/variants-candidates); do \
		name=$$(basename $$suite); echo "== $$suite: 5 runs"; \
		uv run harness --fixtures $$suite --runs 5 --need 1 --trace runs/reliability/$$name > runs/reliability/$$name.txt; \
	done; \
	uv run python -m pirtis.ops.reliability --dir runs/reliability --json runs/reliability/curve.json

# The off-path reviewer (pirtis/ops/review.py): another model re-reads the simulator calls of
# the last DAYS and puts every turn it hears differently into the Feedback queue for a
# person. REVIEW_MODEL picks the model (default gpt-4o); DRY=1 reports without queueing.
DAYS ?= 1
review:
	uv run python -m pirtis.ops.review --days $(DAYS) $(if $(DRY),--dry-run)

# docs/fixtures.md is written from the test cases; a test fails when it is stale.
fixtures-doc:
	uv run python -m pirtis.ops.fixture_doc

# Every document as a PDF into docs/pdf/, with SOURCES.json; a test fails when one is stale.
pdfs:
	cd .. && python3 docs/tools/make_pdfs.py

# Judge calibration against the human ratings (tests/human/README.md); spends judge calls.
calibrate:
	uv run python -m pirtis.ops.calibrate
