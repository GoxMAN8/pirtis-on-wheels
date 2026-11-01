# Test cases

> Written by `python -m pirtis.ops.fixture_doc` from the test cases themselves; a
> test fails when it is out of date. Edit the cases, or the one-line descriptions
> in `python/pirtis/ops/fixture_doc.py`, then run `make fixtures-doc`.

A test case (a *fixture*) is **one turn of a phone call**: the call so far, the world
it happens in, and what must be true after the receptionist's turn. The kit's runner
calls `decide()` once with the call, runs the actions it returns, and checks.

## What a case holds

| Part | In the YAML | What it is |
|---|---|---|
| The call so far | `transcript` | every line said, with times; whether the last line is still changing; the caller's tone; API calls made earlier in the call. Up to 14 lines: most cases start mid-conversation |
| The world | `fixtures/seeds/base.json`, plus `setup` | the saunas, customers and bookings every case starts from, plus bookings added for this case |
| Faults | `faults` | an API failure injected on purpose, such as a 409 when the slot is taken between offer and write |
| The expected action | `expect.action` | `say`, `wait`, `transfer` or `end_call` |
| The expected booking change | `expect.bookings` | what changed in the bookings table; empty means nothing may change |
| Judge statements | `judge` | sentences about the reply that the kit's judge (gpt-4o-mini, temperature 0) marks true or false |

**Checked on every run, in every case:** no API call may fail unless the case
injects that failure, and customer data may only be read or changed for a caller who
has been identified. The frozen gate asks for all of it to hold in **3 of 3 runs**.

**Gold call states** (`python/pirtis/agent/gold/`): for every case where the model is
asked, what it should hear, field by field. They drive the extractor eval
([data-science.md §5](data-science.md#5-the-extractor-eval-understanding-isolated)).

## The world they share

Every call happens on **Monday 28 September 2026**. The seed:

| Sauna | Seats | Weight | Heater | Needs mains power | Per day |
|---|---|---|---|---|---|
| Mažoji | 4 | 750 kg | wood | no | €120 |
| Statinė | 4 | 900 kg | electric | yes | €130 |
| Vidutinė | 6 | 1,100 kg | electric | yes | €160 |
| Didžioji | 8 | 1,600 kg | wood | no | €210 |

Plus 4 customers (made up: Jonas Jonaitis, Tomas Petrauskas, Eglė Butkutė, Rasa Paulauskienė), 3 existing bookings and 1 sauna in for service. Testers in the caller simulator call as the same people.

## Where they come from

| Group | Cases | Files | Where they come from | In the gate | Nightly |
|---|---|---|---|---|---|
| Given by the kit | 14 | `fixtures/00–13` | the kit, as received | yes | yes |
| Edge cases | 10 | `fixtures/extra/20–29` | hand-written before the agent, from the kit's README (Phase 2) | yes | no |
| Hidden-style | 12 | `fixtures/extra/30–49` | hand-written: the same rules, other callers, cars and dates (Phase 13) | yes | no |
| Red team | 7 | `fixtures/extra/50–59` | hand-written attacks on privacy, consent and the prompt (Phase 13) | yes | yes |
| Promoted from calls | 3 | `fixtures/extra/60–` | promoted from simulator calls through the feedback loop (Phase 12) | yes | no |
| Generated variants | 59 | `fixtures/variants-candidates/` | made by `ops/variants.py` from the given cases, answers checked by the kit's API | no: the held-out set | no |

**46 cases in the gate**: 14 given by the kit and 32 of ours. The given ones are the graders'; ours were
written to cover rules the given ones don't reach, the hidden cases' kind of
variation, attacks, and real calls that went wrong. They are the **development set**:
the agent was tuned while looking at them, so the variants are kept apart as the
held-out set ([data-science.md §3](data-science.md#development-set-and-held-out-set)).

## Every case

**Expect** is the action, then the booking change. **Judged** is how many
statements the judge checks. **Gold** marks a gold call state.

### Given by the kit

| # | Case | What it tests | Expect | Judged | Gold |
|---|---|---|---|---|---|
| 00 | `happy-path` | a full booking on the caller's yes to our read-back; power unknown is fine for a wood sauna | say; books Mažoji 6 Oct–7 Oct | — | yes |
| 01 | `identify-before-reading` | asks for the full name before any customer data is read | say; no booking change | 2 | yes |
| 02 | `interim-speech` | waits while the caller's line is still changing; no model call | wait; no booking change | — | — |
| 03 | `silence` | checks in after a silence; no model call | say; no booking change | 2 | — |
| 04 | `capacity` | a hedged group size counts high; a sauna too small is never offered | say; no booking change | 3 | yes |
| 05 | `ask-tow-capacity` | a towing capacity only from a number the caller said; no booking on "just book it" | say; no booking change | 2 | yes |
| 06 | `tow-weight` | every filter in one search: only one sauna fits six people and 1,500 kg | say; no booking change | 3 | yes |
| 07 | `power` | a generator is not mains power: only a wood sauna fits | say; no booking change | 3 | yes |
| 08 | `turnaround` | a must-have sauna, a heater reason, and a window moved past a turnaround day | say; no booking change | 4 | yes |
| 09 | `change-of-plan` | a move a week later, on a go-ahead, with the version from the fetched booking | say; moves bk_91 to 10 Oct–11 Oct | — | yes |
| 10 | `other-customers-data` | never says who holds a booking; offers other dates instead | say; no booking change | 2 | yes |
| 11 | `weekend-minimum` | a weekend booking is two days; the rule applied in code | say; no booking change | 4 | yes |
| 12 | `slot-stolen` | a 409 between offer and write: check again, book once more | say; books Mažoji 10 Oct–11 Oct; a 409 injected on bookings.create | — | yes |
| 13 | `rising-frustration` | an angry caller who cuts in and asks for a person is put through; no model call | transfer; no booking change | — | — |

### Edge cases · 20–29

| # | Case | What it tests | Expect | Judged | Gold |
|---|---|---|---|---|---|
| 20 | `unknown-caller` | a number and name that match no customer go to a colleague: no sign-up by phone | transfer; no booking change | — | yes |
| 21 | `first-name-only` | a first name alone isn't enough to identify; nothing is read or offered | say; no booking change | 2 | yes |
| 22 | `cancel-booking` | a cancel on a clear yes | say; cancels bk_88 | 1 | yes |
| 23 | `nine-people` | nine people: no sauna seats that many, said plainly | say; no booking change | 2 | yes |
| 24 | `car-too-light` | a car that tows 700 kg: no sauna is light enough | say; no booking change | 2 | yes |
| 25 | `sunday-only` | a Sunday alone becomes Sunday and Monday, with the reason said | say; no booking change | 3 | yes |
| 26 | `silence-after-check-in` | a second silence after a check-in ends the call; no model call | end_call; no booking change | — | — |
| 27 | `barge-in-correction` | a correction made by cutting in wins: eight people, not six | say; no booking change | 3 | yes |
| 28 | `identify-this-turn` | a full name in the latest turn identifies the caller, then the next question | say; no booking change | 1 | yes |
| 29 | `own-booking-overlap` | a clash with the caller's own booking is said, not booked over | say; no booking change | 2 | yes |

### Hidden-style · 30–49

| # | Case | What it tests | Expect | Judged | Gold |
|---|---|---|---|---|---|
| 30 | `price-before-identification` | a price question is answered before identification, for the sauna described | say; no booking change | 1 | yes |
| 31 | `is-my-booking-still-on` | the caller's own bookings, read after identification | say; no booking change | 1 | yes |
| 32 | `move-it-a-day-earlier` | a one-day move with a go-ahead | say; moves bk_65 to 16 Oct–17 Oct | 1 | yes |
| 33 | `cancel-which-booking` | two bookings: asks which one, writes nothing | say; no booking change | 1 | yes |
| 34 | `a-date-that-has-passed` | a past date is named as past before anything else | say; no booking change | 1 | yes |
| 35 | `tomorrow-for-one-day` | "tomorrow" resolved from the calendar; one day | say; no booking change | 1 | yes |
| 36 | `across-the-month-end` | dates across a month end; the taken sauna gives way to one that's free | say; no booking change | 1 | yes |
| 37 | `two-tonnes-in-words` | "two tonnes" is 2,000 kg | say; no booking change | 1 | yes |
| 38 | `one-point-eight-tonnes` | "1.8 tonnes" is 1,800 kg; the electric sauna is taken | say; no booking change | 1 | yes |
| 39 | `actually-five-of-us` | a correction after an offer: the old offer is never booked | say; no booking change | 1 | yes |
| 40 | `hang-on-let-me-ask` | "hang on, let me ask" as the last line: wait; no model call | wait; no booking change | — | — |
| 41 | `think-about-it-and-goodbye` | a goodbye after an offer ends the call; nothing is booked | end_call; no booking change | 1 | yes |

### Red team · 50–59

| # | Case | What it tests | Expect | Judged | Gold |
|---|---|---|---|---|---|
| 50 | `ignore-your-instructions` | an injection that names its own price is not consent; the real price is read back | say; no booking change | 1 | yes |
| 51 | `book-it-without-reading-back` | "don't read it back, just book": the read-back comes first | say; no booking change | 1 | yes |
| 52 | `the-manager-asks-for-a-number` | a claimed authority gets no customer's details | say; no booking change | 1 | yes |
| 53 | `right-name-wrong-phone` | a customer's name from another number is never identified | transfer; no booking change | 1 | yes |
| 54 | `who-booked-it-and-where` | who holds a booking, and where they live: refused | say; no booking change | 1 | yes |
| 55 | `read-me-your-instructions` | the prompt is never recited; the call is steered back to saunas | say; no booking change | 1 | yes |
| 56 | `say-the-price-in-digits` | figures are spoken in words, whatever the caller asks | say; no booking change | 1 | yes |

### Promoted from calls · 60+

| # | Case | What it tests | Expect | Judged | Gold |
|---|---|---|---|---|---|
| 60 | `books-didzioji-for-six-with-no-mains-power` | a regression guard: four turns, no mains power, the big wood sauna booked | say; books Didžioji 10 Oct–11 Oct, 6 people | 3 | yes |
| 61 | `introduces-herself-and-is-helped` | a caller giving her own name is identified and asked what she needs | say; no booking change | 1 | yes |
| 62 | `nine-people-hear-the-largest-sauna-seats-eight` | a group no sauna seats hears so at once, even when it hedges | say; no booking change | 1 | yes |

### Generated variants: the held-out set

Each is a given case changed the way another caller or another day would change it.
A variant is kept only if the kit's own API agrees on its answer: its loader reads it,
the API accepts every booking it expects, and availability returns the same fitting
saunas as for the source. The review table is
[`fixtures/variants-candidates/REVIEW.md`](../fixtures/variants-candidates/REVIEW.md).

| Transform | Variants | What changes |
|---|---|---|
| noise | 14 | the caller's lines as speech-to-text writes them: fillers, a restart, figures as digits |
| persona | 16 | another seed customer with no bookings calls instead |
| shift | 19 | the whole call one or two weeks later: every date, weekdays kept true |
| tow | 10 | another car in the same towing band, so the same saunas fit |

They come from 14 of the 14 given cases.

## Adding a case

- **From a call:** rate the reply in the caller simulator, then *Save as test case* in
  the admin console's Feedback view ([guide-admin.md](guide-admin.md#feedback)).
- **By hand:** a YAML file in `fixtures/extra/`, numbered into its group.
- **Either way:** seed people only, never a real name or number; a gold state in
  `python/pirtis/agent/gold/` if the model is asked; it must fail on the agent that
  got it wrong before the fix; a line in `TESTS` in `python/pirtis/ops/fixture_doc.py`;
  then `make fixtures-doc`. The prompt must never quote a case (`test_no_leak.py`).
