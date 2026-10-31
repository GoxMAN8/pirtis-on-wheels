# Pirtis on Wheels receptionist — specification

The living design document for our solution to the Pirtis on Wheels kata (Python). It
says what we build and why, where every rule is enforced, how we measure it, and every
decision we took, with a dated change log. The phase-by-phase record of the build is in
`BUILD_LOG.md`.

---

## 1. Goal and constraints

**Goal:** a phone receptionist that books, moves and cancels mobile-sauna rentals for
known customers. It hands everything else to a person. It must never offer something
the rules forbid, never talk over the caller, never leave them in silence, and sound
like a person on the phone.

**Fixed by the kit (we never edit these):**
- The rental API: `pirtis/api`. Reads never check rules; writes enforce them all and
  raise `ApiError`.
- The LLM client: `pirtis/llm/client.py`. The model is always `gpt-4o-mini`.
- The harness and judge: `pirtis/harness`, `pirtis/cmd`.
- The transcript types: `pirtis/transcript`.
- Two signatures: `decide(t, api, llm) -> list[Action]` and
  `handlers: dict[str, Handler]`.

**What "good" means:**
- every given fixture passes the gate (§7)
- hidden fixtures with other callers, cars and dates pass for the same reasons, which
  is why we test variants (§7)
- most turns use **one model call or none**

---

## 2. Architecture

**The model listens; code talks.** Language understanding is the model's job. Rules,
decisions, writes and the wording of anything factual belong to code. This is the
pattern of "LLM as dialogue understanding plus deterministic flows" used in
production task-oriented agents. It keeps a small model away from every rule and every
write.

```
transcript
   │
   ├─ 1. turn gate (code, 0 model calls)
   │       caller mid-sentence → wait     silence → check in / hang up
   │       asks for a person / angry barge-in → warm transfer
   │
   ├─ 2. understand (1 model call, strict JSON → validated CallState)
   │       intent, name, dates, people, car's towing capacity (with the caller's quote),
   │       power, unit preference, consent flags
   │
   ├─ 3. policy (code)
   │       identify → ask the one next missing detail → search with all filters
   │       → rules mirror (violations) → offer, or write only on read-back consent
   │       → handle 409 / version conflicts → never read other customers' data
   │
   └─ 4. speech (code templates)
           numbers and dates as words, one question, tone from prosody
```

`decide` is stateless: every turn rebuilds the call state from the transcript. Calls to
the API made in earlier turns are visible in the transcript as `function_call` items.

---

## 3. Modules and contracts

All of these live in `python/pirtis/agent/`. The agent imports only the standard
library, the kit's own modules and pydantic (D-32, D-49).

| Module | Contract |
|---|---|
| `turn.py` | `gate(t) -> list[Action] \| None`. `None` means "go on to understanding". |
| `understand.py` | `extract(t, llm, units) -> CallState` (frozen pydantic model, strict schema generated from the class); raises `UnderstandError` if it can't validate |
| `rules.py` | `violations(req, unit, free_days, own_bookings) -> list[str]` mirrors every write rule of the API |
| `policy.py` | `next_step(t, state, api, units) -> Decision`; the offer engine (`apply_weekend_min`, `find_window`, `choose_offer`); `last_offer(t, units)` parses our previous read-back |
| `speech.py` | `render(decision, t) -> list[Action]`; spoken numbers, dates and prices; tone |
| `telemetry.py` | `TurnRecord` (no content) and `to_otel()`; emitted on logger `pirtis.turn` |
| `actions.py` | `handlers` (`say`, `wait`, `transfer`, `end_call`, `sms`, `note`) and helpers |
| `agent.py` | `decide` wires gate → understand → policy → speech; never raises |

Around the agent, never imported by it:
- `pirtis/ops/`: data layer, gate job, variant generator
- `pirtis/playground/`: caller simulator and admin console

---

## 4. Rules and where they are enforced

The API enforces every rule on write. We enforce each one **before** the write, so that
a 422 is impossible by construction. `violations()` is property-tested against the API
itself (D-31).

| Rule | API code | Where we enforce it |
|---|---|---|
| People ≤ unit capacity | 422 `CAPACITY_EXCEEDED` | availability filter `people`; `violations()` |
| Unit weight ≤ the car's braked towing capacity | 422 `TOW_WEIGHT_EXCEEDED` | filter `max_tow_kg`; `violations()`; tow only from a quoted number (D-08) |
| Electric unit needs mains power | 422 `POWER_REQUIRED` | filter `power_available`; ask about power only for electric offers (D-09) |
| One clear day between bookings of a unit | 422 `TURNAROUND_CONFLICT` | availability already removes turnaround days; `violations()` |
| A booking touching Sat/Sun is ≥ 2 days | 422 `MIN_DAYS_WEEKEND` | `apply_weekend_min`, re-applied to every shifted window (D-13) |
| A customer can't double-book themselves | 422 `OVERLAPS_EXISTING` | the caller's own bookings in `violations()`; we tell them (D-14) |
| Unit free and not blocked | 409 `UNAVAILABLE` | availability search; one re-check and retry (D-19) |
| Stale booking version | 409 `VERSION_MISMATCH` | refetch, retry once (D-19) |
| Customer data only after identification | harness invariant | `identified_customer(t)` before any customer call; identify only with ≥ 2 name tokens (D-07) |
| No sign-up by phone | README | a failed identify → transfer to `new_customers` (D-07) |
| Other customers' data stays private | judge fixtures | never `bookings.list` without our caller's id; templates never name a holder (D-11) |
| Only offer what can really be booked | README | write only on consent, a matching read-back and an empty pre-flight (D-10) |

---

## 5. Voice UX

Each rule is enforced in code and has a test (D-24).

| Rule | Enforced by |
|---|---|
| Never speak while the caller is still talking | turn gate: `is_final`/`speech_final`, filler-only lines, "hang on" |
| Never leave dead air | silence ≥ 6 s → "Are you still there?"; a second silence → goodbye + hang up |
| One question per turn | templates; test: at most one "?" per speech |
| Short | at most two sentences before the question |
| Numbers, dates and prices as words | templates + the `say` handler's digit guard |
| Read back before every booking | unit, spoken dates, people, price, then "Shall I book it?" |
| Barge-in recovery | the next reply is rebuilt from the recomputed state, never a repeat of the cut-off line |
| Tone follows the caller | anger or distress ≥ 0.5 → `calm`, speed 0.95 |
| Warm transfer | "I'll put you through to a colleague" before every transfer, with a handoff summary |
| Privacy wording | never confirm or deny who holds a booking |

### 5b. Visual design (caller simulator and admin console)

The subject is the Lithuanian smoke sauna (*dūminė pirtis*): soot-blackened timber, ember
light, drifting steam, then the cold plunge. **Hot and cold is the colour logic:** hot
means live activity, failures and slow turns; cold means calm, passed and healthy.

| Token | Hex | Role |
|---|---|---|
| `soot` | `#1C191B` | dark background |
| `smoke` | `#2E292C` | raised surfaces (dark) |
| `steam` | `#E7ECEE` | light background; text on dark |
| `ash` | `#8E8A8C` | muted text; neutral midpoint |
| `ember` | `#F0743A` | hot: speaking, failures, latency (glow, rarely a flat fill) |
| `plunge` | `#2F8F8B` | cold: passed, healthy, booked |

- **Charts:** diverging `plunge → ash → ember`; sequential `soot → ember`. Every pair is
  checked for WCAG AA before use.
- **Type** (self-hosted, Latin Extended for ė ž š ū ą č ę į ų): **Big Shoulders
  Display** for unit names, headline numbers and view titles only; **Atkinson
  Hyperlegible Next** for body text and transcripts; **IBM Plex Mono** for data.
- **Themes:** *Soot* (dark) and *Steam* (light), following the system setting with a
  switch.
- **Signatures:**
  - **Caller side, the fogged-glass booking card:** details condense out of fog as the
    receptionist understands them; your own words behind each detail show on hover.
  - **Admin side, the thermal view:** fixtures × runs × versions shown as a
    thermal-camera image, where hot spots are failures you click through to their trace.
- **Floor:** visible keyboard focus; `prefers-reduced-motion` makes the smoke and fog
  static; usable at phone width.

---

## 6. Fixture traceability

Calendar: today is Monday 28 Sep 2026; 3–4 Oct and 10–11 Oct are weekends. Every row
was traced against the real in-memory API.

| Fixture | Mechanism | What happens |
|---|---|---|
| 00 happy-path | read-back consent + pre-flight | creates Mažoji 6–7 Oct; power unknown is fine for a wood unit |
| 01 identify-before-reading | identification gate | asks for the full name; no customer data touched |
| 02 interim-speech | turn gate | `wait`, 0 model calls |
| 03 silence | turn gate | "Are you still there? No rush if you need a moment.", 0 model calls |
| 04 capacity | hedged people → 8; hard limits | Mažoji too small → offers Didžioji, asks to confirm |
| 05 ask-tow-capacity | tow only from a quoted number | asks how much the car can tow, braked; no booking despite "just book it" |
| 06 tow-weight | availability with all filters | only Vidutinė fits 6 people and 1500 kg → offers it |
| 07 power | generator ≠ mains | only Didžioji fits without power → offers it |
| 08 turnaround | must-have unit, window shift, weekend minimum | "Vidutinė isn't free on Saturday the third. I can do Sunday the fourth to Monday the fifth…" |
| 09 change-of-plan | move with `go_ahead`, `shift_days` | `update_booking("bk_91", 1, start 10 Oct, end 11 Oct)` |
| 10 other-customers-data | privacy | refuses to say who holds it; offers other dates |
| 11 weekend-minimum | the model records, code applies the rule | "Weekend rentals are two days minimum, so…" Sat 10–Sun 11; never Friday |
| 12 slot-stolen | 409 → re-check → retry | the injected 409, then a successful create |
| 13 rising-frustration | turn gate | explicit "a person" + anger 0.82 + barge-in → transfer, 0 model calls |

---

## 7. Gate (frozen)

Decided before any agent code was written (D-21). **Never lowered**; a failing gate
blocks deployment.

- **Suites:**
  - `fixtures/` (the 14 given)
  - `fixtures/extra/`: our edge cases 20–29, hidden-style 30–41, red team 50–56
  - `fixtures/variants/`: metamorphic variants, after human review
- **Threshold:** every fixture passes **3 of 3 runs** (pass^3 = 1.0, n = k = 3),
  judge included, exactly as the graders run it.
- **Reported with every gate run:**
  - deterministic pass^3 (action, bookings, invariants) and judge pass^3, separately
  - the extractor's field-level pass^3 against gold call states (D-36)
  - model calls per turn, tokens, estimated cost, and p95 wall time per turn
- **Judge failures:** read the reason. If our reply was wrong, fix the agent. If the
  reason contradicts the reply, make our wording unambiguous (also an agent change).
  Never rerun until green.
- **Cost discipline:** while iterating we use targeted runs (`--id`, `--runs 1`); full
  gates run at the end of a phase (D-48).

### 7b. Evaluation results

| Date | Agent | Suite | Result | Calls | Tokens in/out |
|---|---|---|---|---|---|
| 2026-10-31 | naive starter (baseline) | given, 1 run | 4/14 | 25 | 16,322 / 1,353 |

Our agent's results, the extractor accuracy, the human ratings and the judge–human
agreement are added here as phases complete.

---

## 8. LLM setup

The same model on every path: `gpt-4o-mini`, set in the kit's client. Configuration goes
in `python/.env`, which git ignores.

- **OpenAI (in use):** `LLM_API_KEY=<key>`; leave `LLM_BASE_URL` at its default.
- **Azure OpenAI:** `LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1` and
  `LLM_API_KEY=<azure key>`. It needs a deployment named exactly `gpt-4o-mini`, because
  the client sends that as the model name. The kit's client already sends the `api-key`
  header, and the v1 API needs no `api-version`. Note that `gpt-4o-mini` 2024-07-18 is
  deprecated on Azure, so new deployments may be refused.

---

## 9. Deployment

A private deployment on Azure, from one container image with two workloads:
- **the playground:** caller simulator plus admin console, as a Container App scaling
  from 0 to 1
- **the gate:** a nightly Container Apps Job, also startable by hand

Written in Terraform (OpenTofu-compatible) under `deploy/`:
- **Where:** an EU region.
- **Identity:** a managed identity for the registry, Key Vault and storage; the model
  key lives only in Key Vault.
- **Images:** deployed by digest.
- **Access:** an ingress IP allow-list plus a per-minute request cap.
- **Logs:** Log Analytics with 30-day retention.

Commands, idle cost and teardown are in `deploy/README.md`.

### 9b. Data model and flows

We persist only data we own; the rental data stays in the kit's in-memory API (D-38).
All entities are versioned pydantic models (`schema_version = 1`). Each is stored as a
JSON document in Blob storage, with an Azure Table row as its index.

| Entity | Table keys | Indexed fields | Document | PII | Retention |
|---|---|---|---|---|---|
| Session | day / `session_id` | `agent_version`, `caller_phone_hash`, `started`, `turns`, `status` | transcript + bookings snapshot | personal | 30 days |
| Feedback | `agent_version` / `turn_id` | `verdict`, `note`, `promoted_fixture` | transcript snapshot | personal | 30 days |
| HumanRating | `scenario_id` / `session_id`+`rater` | rubric scores, `goal_achieved`, `note` | — | pseudonymous | 1 year |
| GateRun | `agent_version` / `run_ts` | pass^k (deterministic, judge, extractor), calls, tokens, p95 | per-run results | none | 1 year |
| TurnRecord | day / `turn_id` | telemetry fields, `source` (playground or gate) | — | pseudonymous | 90 days |

```mermaid
flowchart LR
  caller([Caller]) -- speech or text --> sim[Caller simulator]
  sim -- transcript --> decide["decide()"]
  decide -- one strict-JSON call --> llm[(gpt-4o-mini)]
  decide -- reads, then writes on consent --> api[(Rental API, in memory)]
  decide -- TurnRecord, no content --> logs[(Log Analytics)]
  sim -- Session, Feedback, HumanRating, TurnRecord --> store[(Blob + Table)]
  gatejob[Gate job] -- GateRun, TurnRecord --> store
  admin[Admin console] -- reads --> store
  classDef untrusted stroke-dasharray: 4 3
  class caller,llm untrusted
```

- **Trust boundaries:** caller speech and the model's output are untrusted; the model's
  output is validated before any decision is made (D-49).
- **Where personal data lives:** only in Session and Feedback documents, kept 30 days
  and never in logs.
- **Fixtures:** a promoted fixture enters git only with seed phones and names (D-39).

---

## 10. Telemetry and monitoring

**One `TurnRecord` per turn** (D-26):
- identity: version, timestamp, call and turn ids, agent version
- what happened: gate outcome, intent, decision, action types
- cost: model calls, input and output tokens, latency
- API calls: method, status, code
- outcome: fallback flag, transfer reason, hashed phone

It holds no transcript text and no names. Field names render to OpenTelemetry GenAI
attributes (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, …) through one
renderer, so a convention change touches one function.

| Alert | Condition | Owner | Action |
|---|---|---|---|
| Gate regression | a gate run with `passed = false`, or the job failed | repo owner | open the admin console's Reliability view and follow the change log to the change that broke it |
| Agent errors | any fallback, or any API status ≥ 400, in the last hour | repo owner | read the turn records, replay the call in the simulator |
| Slow turns | p95 latency > 3 s over the last hour | repo owner | check model latency and prompt size |

Each alert path is fired once at launch to prove it works (D-29). The admin console
also shows **intent-mix drift**: the PSI of the last 7 days against the 7 before; above
0.10 is a warning, above 0.25 an alert (D-43).

---

## 11. Improvement loop

1. **Capture:** 👍/👎 and a note on any receptionist line in the caller simulator.
2. **Review:** the admin console's Feedback view lists the 👎 items per agent version.
3. **Promote:** a person turns a failure into a test case. The form pre-fills the
   transcript; you set the expected action, the booking change and judge statements,
   and it enforces seed personas. Nothing is promoted automatically.
4. **Fix:** the new fixture fails the gate; we fix the agent and log the change here.
5. **Ship:** the gate passes; deploy (the deploy runs the gate first); the nightly gate
   keeps checking.

Every change to the prompt or the policy changes `agent_version`, and needs a gate run.

---

## 12. Known limits and next steps

**Known limits:**
- If speech-to-text drops Lithuanian diacritics from a name, identification fails and
  the caller is transferred.
- A move by one day is checked conservatively.
- The agent package needs pydantic, installed by `uv sync`.
- Terraform state is local.

**Next steps:**
- real telephony: call automation and streaming speech in front of `decide`
- remote Terraform state and CI on every push
- an alert on intent drift
- sign-in for the console
- fixtures sourced from real calls

---

## 13. Decision log

Status is *accepted* unless marked otherwise. "R" marks a fix from the fixture trace;
"A" marks a fix from the adversarial review.

| ID | Date | Decision | Why | Rejected | Status |
|---|---|---|---|---|---|
| D-01 | 2026-10-31 | Python implementation | One language as required; strongest tooling for this kit | Go, TS, Rust | accepted |
| D-02 | 2026-10-31 | The model listens, code talks: 0–1 model calls per turn | Rules and writes stay deterministic; cheap and fast | a second phrasing call; a guarded tool loop | accepted |
| D-03 | 2026-10-31 | `gpt-4o-mini` only inside `decide`; stronger models only offline (writing test material) | The kit pins the model; graders run it | our own client inside `decide` | accepted |
| D-04 | 2026-10-31 | Trust `speech_final`; no text-based end-of-turn guessing; a filler-only line → wait | Fixture 01 ends "so, uh" and expects a reply | a semantic end-of-turn heuristic | accepted |
| D-05 | 2026-10-31 | Silence ≥ 6 s → check in; a second silence or one after a goodbye → hang up; a cut-off line with no caller text → wait. Order: interim → person/anger → silence | No dead air, no talking over the caller | — | accepted (A) |
| D-06 | 2026-10-31 | Transfer on an explicit request (strict regex), a model-detected request, or anger ≥ 0.7 with a barge-in; always announce it | A bare "person" matches "the eight person one" | a loose keyword match | accepted (R, A) |
| D-07 | 2026-10-31 | Identify only with ≥ 2 name tokens after normalising (no single letters or honorifics; the caller's own spelling); a failure → transfer to `new_customers` | "it's Jonas" alone would fail and wrongly transfer a known customer | identify on any name | accepted (R, A) |
| D-08 | 2026-10-31 | The model records what was asked and never applies rules; hedged people → higher; hedged tow → lower, braked; generator ≠ mains; the latest statement wins; tow kept only with a fuzzy-matched quote containing a number | A small model invents numbers and "helpfully" applies rules | trusting the extracted number | accepted (R, A) |
| D-09 | 2026-10-31 | One question per turn: dates → people → tow → search → power (only for an electric offer) | Power only matters once the offer is known | asking everything up front | accepted (R) |
| D-10 | 2026-10-31 | Create only when `accepted_offer`, our parsed previous read-back equals the computed offer, and the pre-flight is empty; moves and cancels need `go_ahead` or an accepted change offer | Consent must be to what the caller actually heard | comparing the extraction to itself | accepted (R, A) |
| D-11 | 2026-10-31 | Never read other customers' bookings; never say who holds a unit | Privacy | — | accepted |
| D-12 | 2026-10-31 | Hard limits are never overridden; a fitting named unit is shifted in time if the caller insists or nothing else fits; heater preference sorts, and filters only with a reason | Fixture 08 (asthma); "the wood one, probably" must not empty the list | insisting overrides limits | accepted (R, A) |
| D-13 | 2026-10-31 | The weekend minimum extends forward, and is re-checked on every shifted window | A shift can land a weekday on a weekend | checking only the request | accepted (A) |
| D-14 | 2026-10-31 | A clash with the caller's own booking: say so and offer to change that booking | The API rejects it with 422, which availability can't show | silently shifting | accepted (R, A) |
| D-15 | 2026-10-31 | Moves: new dates computed in code from the stored booking plus a shift; the booking's own days, and its own turnaround day when the day beyond is free, count as free | Availability marks the booking's own days busy | rejecting valid small moves | accepted (R, A) |
| D-16 | 2026-10-31 | A 42-day calendar plus anchors computed in code; invalid or past dates → ask again | Date words resolve reliably | the model doing date arithmetic | accepted (R) |
| D-17 | 2026-10-31 | Speech from code templates; offers end "Shall I book it?" | Numbers as words, deterministic wording | model-written replies | accepted |
| D-18 | 2026-10-31 | `sms` action with a written confirmation after a booking or move | Callers get the details in writing | — | accepted |
| D-19 | 2026-10-31 | 409 → re-check, retry once; version conflict → refetch, retry once; 500 → check own bookings, retry once; two misunderstandings in a row → transfer; `decide` never raises | Faults between read and write happen | failing the turn | accepted (A) |
| D-20 | 2026-10-31 | No new runtime dependencies | — | — | superseded by D-49 |
| D-21 | 2026-10-31 | Gate: pass^3 = 1.0 on all suites, judge included, reported split; frozen; blocks deploy | Reliability means every run passes | pass@k; rerunning until green | accepted (A) |
| D-22 | 2026-10-31 | Model access: OpenAI for local development; an Azure deployment tried for the cloud | No `gpt-4o-mini` deployment exists in our Azure subscription | — | accepted |
| D-23 | 2026-10-31 | `SPEC.md` and `BUILD_LOG.md` ship with the repo; one commit per phase | The reasoning is part of the work | — | accepted |
| D-24 | 2026-10-31 | Voice UX is a first-class section; every rule has a test | A phone agent is judged by how it sounds | — | accepted |
| D-25 | 2026-10-31 | A single playground page | — | — | superseded by D-40 |
| D-26 | 2026-10-31 | One versioned `TurnRecord` per turn, no content, OpenTelemetry names via a renderer, silent by default | Observability without personal data | logging transcripts | accepted |
| D-27 | 2026-10-31 | Azure: one image, a playground app (0–1) and a nightly gate job; Terraform; EU region; managed identity; key only in Key Vault | Private, cheap when idle, no stored secrets | a VM; keys in config | accepted |
| D-28 | 2026-10-31 | Access: an IP allow-list, a per-minute cap, one replica | Every visit costs model calls | sign-in (more moving parts) | accepted |
| D-29 | 2026-10-31 | Alerts: gate regression, agent errors, slow turns; each with an owner and an action; fired once at launch | An untested alert is no alert | — | accepted |
| D-30 | 2026-10-31 | Improvement loop: feedback → human-promoted fixture → gate → fix → change log → deploy | Failures become permanent tests | automatic promotion | accepted |
| D-31 | 2026-10-31 | `violations()` mirrors every write rule, runs before every offer and write, and is property-tested against the API | A 422 becomes impossible by construction | trusting the API to catch it | accepted (A) |
| D-32 | 2026-10-31 | The agent imports only the standard library, kit modules and pydantic; helpers live in `agent/actions.py` | Only `pirtis/agent` is ours | helpers in kit files | accepted (A) |
| D-33 | 2026-10-31 | Schema order: gist → quotes before values → slots → answer → consent last; nullable enums include null | The model reasons before it commits | values before evidence | accepted (A) |
| D-34 | 2026-10-31 | Intents: goodbye → hang up; hold → wait; price from the catalogue; a model-detected request for a person → transfer; free answers limited and filtered | Common turns the booking flow doesn't cover | — | accepted (A) |
| D-35 | 2026-10-31 | The window search tries offsets 0, +1, −1, +2, −2 …, never before today | The natural alternative is often the day before | forward-only search | accepted (A) |
| D-36 | 2026-10-31 | Extractor evaluation against gold call states, 3 runs, field by field | Separates understanding errors from judge noise | end-to-end only | accepted (A) |
| D-37 | 2026-10-31 | A `note` action records the decision, intent and version in each result | Traceable runs; the judge ignores it | — | accepted (A) |
| D-38 | 2026-10-31 | Data layer for our own data only: pydantic entities in Blob, an index in Table, local files for development, no database server | Persistence without a standing cost | Postgres; SQLite over a file share | accepted |
| D-39 | 2026-10-31 | Promoted fixtures use seed personas only | No personal data in git | — | accepted |
| D-40 | 2026-10-31 | Two UIs on one standard-library server: caller simulator (`/`) and admin console (`/admin`); vanilla JS; charts with Observable Plot + d3 vendored; fonts vendored | No build chain; works offline | a React/Vite app; hand-rolled charts | accepted |
| D-41 | 2026-10-31 | Visual identity "smoke sauna": hot/cold colour logic, the type above, one signature per UI, accessibility floor | A distinctive, subject-true design | generic dashboard styling | accepted |
| D-42 | 2026-10-31 | Turn records also stored (90 days) for analytics; traces join them to session or gate documents | The console needs queryable history | — | accepted |
| D-43 | 2026-10-31 | Intent-mix drift (PSI, 7 days vs the previous 7; 0.10 / 0.25) shown in the console | Early warning that callers changed | — | accepted |
| D-44 | 2026-10-31 | Metamorphic variants (persona, calendar shift, speech-to-text noise, tow band), with the kit's API as the oracle; human-reviewed before joining the gate | Hidden fixtures vary exactly this way | testing against our own policy | accepted |
| D-45 | 2026-10-31 | Hidden-style and red-team fixtures written by hand; red team always at 1.0 | Safety and privacy are not negotiable | — | accepted |
| D-46 | 2026-10-31 | Human evaluation: scenario cards, seed personas, five-item rubric; failures become fixtures | Real people find what fixtures miss | — | accepted |
| D-47 | 2026-10-31 | Judge calibration: judge–human and rater–rater agreement (Cohen's κ) reported; the kit's judge unchanged | Know how far to trust judge failures | — | accepted |
| D-48 | 2026-10-31 | Spend guards for a small prepaid model credit: targeted runs, full gates at phase ends, cost printed and shown, a credit check before full gates, a slim nightly suite | Exhausted credit returns 429, which would look like agent bugs | — | accepted |
| D-49 | 2026-10-31 | Pydantic v2 at our boundaries (model output, stored entities, playground requests); the strict schema generated from the same class; dataclasses for internals | One source of truth for schema and validation; untrusted model output is checked at the edge | a hand-written parser | accepted |

---

## 14. Change log

| Date | Phase | Change | Reason | Decisions |
|---|---|---|---|---|
| 2026-10-31 | 0 | Kit committed as received; `.gitignore`; environment; model access; naive baseline 4/14 | A clean starting point and a score to beat | D-22, D-49 |
| 2026-10-31 | 1 | `SPEC.md` written: design, rules, voice UX, visual design, frozen gate, data model, monitoring, decision log | Decisions fixed before code | D-01 – D-49 |
