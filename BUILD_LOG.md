# Build log

One entry per phase, newest last. Each entry records what was built, what was decided,
what was run and what came out, and what is still open. Design decisions themselves
live in `SPEC.md` (decision log); this file is the phase-by-phase record.

---

## Phase 0 — repo, environment, LLM access, baseline

**Date:** 2026-10-31
**Goal:** a reproducible starting point. Version control with the kit as received, a
working Python environment, model access, and the naive agent's score as the baseline
to beat.

### What was done
- Initialised git; the first commit (`chore: kit as received`) is the kit untouched, so
  every later change diffs against it.
- Added a root `.gitignore` covering secrets (`**/.env`), harness traces (`**/runs/`),
  local data (`**/var/`), Terraform state and caches, and the deploy digest file.
- Synced the Python environment with `uv sync`. `uv.lock` is unchanged.
- Set up model access through an OpenAI key in `python/.env`:
  - the file is readable only by its owner and ignored by git
  - a model lookup confirmed the key can use `gpt-4o-mini` (HTTP 200, no tokens spent)
- Ran the kit's own tests and the harness with the naive agent, saving traces.

### Files
- New: `.gitignore`, `BUILD_LOG.md`.
- Local only, not committed: `python/.env`, `python/.venv/`, `python/runs/baseline/`.

### Decisions
- **Model access (D-22):** local development uses the OpenAI API directly (Path B).
  - None of the subscription's Azure AI resources has a `gpt-4o-mini` deployment; the
    model is deprecated on Azure, so creating one may be refused.
  - The Azure attempt moves to Phase 15, where the cloud deployment needs it.
  - Same model either way.
- **New D-49:** pydantic v2 validates our boundaries, chiefly the model's structured
  output. The strict JSON schema is generated from the same model class, so schema and
  validation can't drift. This supersedes D-20 ("no new runtime dependencies"). It
  enters `SPEC.md` in Phase 1, and the dependency is added in Phase 2.

### Commands and results
| Command | Result |
|---|---|
| `uv sync` | environment created; lock unchanged |
| `uv run pytest` | 43 passed |
| `uv run harness --trace runs/baseline` | **4/14 fixtures pass** (naive agent, one run each) |

Tool check: uv 0.9.28, Python 3.12.13, Azure CLI 2.88 (logged in), Terraform 1.5.7,
Docker 28.3. OpenTofu is not installed; it's optional, for the OpenTofu compatibility
check later.

### Baseline (naive agent)
| Fixture | Result | Why it failed |
|---|---|---|
| happy-path | pass | |
| identify-before-reading | pass | |
| interim-speech | fail | spoke while the caller was mid-sentence |
| silence | fail | did not check whether the caller was still there |
| capacity | fail | no confirmation question before booking |
| ask-tow-capacity | fail | **booked with an invented towing capacity** and said so |
| tow-weight | fail | offered Didžioji, which is too heavy for the car |
| power | pass | |
| turnaround | fail | offered other units; never offered Vidutinė from Sunday |
| change-of-plan | fail | **created a second booking** instead of moving the existing one |
| other-customers-data | pass | |
| weekend-minimum | fail | **booked the wrong date** (1 Oct, a single day) without confirming |
| slot-stolen | fail | gave up after the 409; no booking made |
| rising-frustration | fail | kept talking instead of transferring |

- **Model calls:** 25 in total; 0 to 3 per fixture (tool loop).
- **Tokens:** 16,322 in and 1,353 out for the agent.
- **Cost:** about $0.01 including the judge calls.
- No API call returned an error; the failures are all wrong behaviour, not rule
  violations caught by the API.

### Deviations from the plan
- `git init` and the kit commit happened just before the plan was confirmed. They are
  kept as they are.
- The SPEC change-log row for this phase will be written when `SPEC.md` is created in
  Phase 1.

### Open issues and risks
- The OpenAI key was shared in a chat conversation. Rotate it after the take-home.
- Budget: $6 of credit; this phase used about $0.01.

### What the next phase needs from you
- Nothing beyond the go-ahead. Phase 1 writes `SPEC.md`: design, rules, voice UX,
  visual design, the frozen gate, and the decision log D-01 to D-49.

---

## Phase 1 — SPEC.md: design, decisions, change log

**Date:** 2026-10-31
**Goal:** fix the design and the measuring stick in writing before any agent code
exists, so every later change has a decision to point at.

### What was done
Wrote `SPEC.md`, 14 sections:
1. Goal and constraints
2. Architecture: the model listens, code talks
3. Module contracts
4. Every API rule and where we enforce it before the write
5. Voice UX rules, each mapped to its enforcement
   - 5b. Visual design: tokens, type, signatures, accessibility floor
6. Fixture traceability for all 14 given fixtures
7. The frozen gate
   - 7b. Evaluation results, seeded with the baseline
8. LLM setup for OpenAI and Azure
9. Deployment
   - 9b. Data model with PII classes, retention and a data-flow diagram
10. Telemetry and alerts, each with an owner and an action
11. Improvement loop
12. Known limits and next steps
13. Decision log: 49 entries, D-20 and D-25 marked superseded
14. Change log: rows for Phases 0 and 1

### Files
- New: `SPEC.md`.
- Changed: `BUILD_LOG.md` (this entry).

### Decisions
- No new decisions. D-01 to D-49 were recorded as planned.
- D-48 (spend guards) is worded as "a small prepaid model credit" rather than a dollar
  figure, to keep the repo neutral.

### Commands and results
| Check | Result |
|---|---|
| Forbidden-term and placeholder scan (company names, methodology terms, time spent, TBD/TODO) | clean; the only hits are "until green" (tests) and "the last hour" (alert windows) |
| Decision rows | 49 |
| Model calls | none this phase |

### Deviations from the plan
- None. The gate definition in §7 matches the plan: pass^3 on every suite, judge
  included, reported split, and never lowered.

### Open issues and risks
- §6 traceability and §4 enforcement describe the intended behaviour. They become true
  as Phases 3–9 build the agent, and the replay test in Phase 8 checks §6 line by line.
- §7b only has the baseline so far.

### What the next phase needs from you
- Nothing beyond the go-ahead. Phase 2 adds pydantic plus the pinned ruff and mypy;
  the Makefile; the fixture folders `fixtures/extra/` and the first own fixtures 20–29;
  and the frozen gate target. No model calls, except a load check of the new fixtures
  at zero tokens.

---

## Phase 2 — tooling, own fixtures, frozen gate

**Date:** 2026-10-31
**Goal:** the measuring stick before the agent: pinned tools, a gate command that can
really fail, and our own fixtures for the cases the given 14 don't cover.

### What was done
- **Dependencies:** `pydantic>=2.9,<3` (resolved 2.14.2) at runtime (D-49); dev tools
  pinned exactly, `ruff==0.17.2` and `mypy==2.4.0`. Both pins resolved as written.
- **mypy:** strict on `pirtis/agent` with the pydantic plugin. The kit's packages are
  type-checked as dependencies but their findings are suppressed.
- **Starter agent:** the two bare `dict` annotations got type arguments, so strict mypy
  is green. The file is replaced in Phase 3.
- **`python/Makefile`:**
  - `check` = lint + format check + strict types + tests, no tokens
  - `gate` = every suite at `RUNS`/`NEED` (default 3/3), every suite run even if one
    fails, a closing line with agent model calls, tokens and estimated cost, and a
    non-zero exit on any failure
  - `trace ID=…`
- **`fixtures/extra/`** with `seeds` symlinked to `../seeds`, and ten hand-written
  fixtures:

| # | Fixture | Expected |
|---|---|---|
| 20 | unknown-caller | transfer (identify fails) |
| 21 | first-name-only | ask for the full name, no offer |
| 22 | cancel-booking | `bk_88` (version 2) changed to cancelled |
| 23 | nine-people | nothing fits; no booking |
| 24 | car-too-light | a 700 kg car can't tow any unit; no booking |
| 25 | sunday-only | offer Mažoji Sun 11–Mon 12 with the two-day rule |
| 26 | silence-after-check-in | `end_call` |
| 27 | barge-in-correction | "eight, not six" → offer Didžioji, not Vidutinė |
| 28 | identify-this-turn | identify, then ask how many people |
| 29 | own-booking-overlap | tell Rasa she already has a booking on those dates |

- **Every premise was checked against the real API before writing:**
  - weekdays
  - identify results (including that "Jonas" alone returns `ok: false`)
  - availability for 9 people and for a 700 kg car (both empty)
  - the 1-day Sunday rejected as `MIN_DAYS_WEEKEND`, while Sun–Mon books for 240
  - Didžioji 10–11 books for 420
  - Rasa's Mažoji 6–7 rejected as `OVERLAPS_EXISTING`
  - cancelling `bk_88` at version 2 works
- **`pirtis/agent/test_extra_fixtures.py`:** our fixtures load as the harness loads
  them; IDs are unique across suites; messages are in time order; and every API result
  a transcript claims (identify, bookings list) equals what the API returns for that
  seed.

### Files
- New: `python/Makefile`, `python/pirtis/agent/test_extra_fixtures.py`,
  `fixtures/extra/20…29_*.yaml`, `fixtures/extra/seeds` (symlink).
- Changed: `python/pyproject.toml`, `python/uv.lock`, `python/pirtis/agent/agent.py`
  (type arguments only), `SPEC.md`, `BUILD_LOG.md`.

### Decisions
- **D-50 (new): documentation standard.**
  - three architecture views plus a deployment view
  - Mermaid with evidence paths and commit stamps
  - a shape checker
  - evaluation graphs in Mermaid
  - Phase 16 of the plan is expanded to match
- **D-51 (new): replies state "not" facts explicitly.** This came from a judge finding
  in this phase (below).

### Commands and results
| Command | Result |
|---|---|
| `make check` | lint clean, strict mypy clean (4 files), **64 tests passed** (43 kit + 21 ours) |
| `make gate RUNS=1 NEED=1` (naive agent) | given **3/14**, extra **3/10**; 47 agent model calls, 29,969 / 2,734 tokens, **≈ $0.006 agent** (+ judge) |
| gate with a failing suite | `make` exits non-zero (failure path proven) |

**Bug found and fixed: the gate could never fail.** macOS ships GNU Make 3.81, which
silently ignores `.SHELLFLAGS`, so `pipefail` never took effect and `tee` hid the
harness's exit code. The first one-run gate reported `exit 0` with 18 failures. Now the
recipe sets `pipefail` itself, and a suite with a failing fixture makes `make gate`
exit non-zero.

**Judge findings** from reading every verdict:
- **`other-customers-data`:** a real judge error. The reply gave no contact details,
  yet "The reply gives no phone number or other contact details" was marked failed,
  with the reason "does not provide any contact details, so it fails". The polarity of
  a negatively worded criterion was flipped (D-51).
- **`turnaround`:** the verdict was correct (the naive reply did offer other units);
  only the reason text was garbled.
- **`nine-people`:** in one sample the naive agent said Didžioji "can accommodate nine
  people". The judge caught it correctly.

**The naive agent varies between samples:** 4/14 in Phase 0, then 3/14. On our suite
it fails 7 of 10, and `cancel-booking` alone triggers one 400 and four 422s. The new
fixtures exercise real failure modes.

### Deviations from the plan
- The `[project.scripts]` entries `playground` and `gate-job`, and the `eval-extract` /
  `playground` Make targets, are deferred to the phases that create those modules, so
  no target or script points at code that doesn't exist yet.
- The gate's **pass path** (exit 0 when everything passes) isn't observed yet. The
  naive agent never passes a whole suite; it will be observed in Phase 3. The failure
  path is proven.

### Spend
About **$0.02** so far across Phases 0–2 (agent and judge calls), out of the $6 credit.

### Open issues and risks
- Judge polarity errors are real, and the gate counts judge verdicts (D-21). Every
  judge failure gets read before acting; Phase 13's calibration will measure how often
  this happens.
- The key shared in chat should be rotated after the take-home.

### What the next phase needs from you
- Nothing beyond the go-ahead. Phase 3 builds the walking skeleton and runs it live:
  - a minimal turn gate, a first `CallState` extraction, the booking path for fixtures
    00/04/05, templates and wiring
  - a live run (targeted, a few cents)
