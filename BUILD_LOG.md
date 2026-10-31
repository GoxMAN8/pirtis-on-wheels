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
