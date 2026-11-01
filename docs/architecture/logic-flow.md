> Verified against `14e773f54eac`'s source on 2026-11-01.

# Logic flow

What the receptionist decides on one turn, and in what order. [`sequences.md`](sequences.md)
follows the two writes across the containers; this page follows the decisions inside
`decide()`. Every box names the code that does it; `D-nn` points to the
[decision log](../../SPEC.md#13-decision-log).

The agent is **stateless**: each turn it receives the whole call so far and rebuilds
what it knows from it. The kit calls `decide()` once per caller turn, so a conversation
of any length is this flow, run again on a longer transcript.

## How the receptionist remembers

**The transcript is the memory.** Nothing is stored between turns: each turn, `decide()`
reads the whole call so far and rebuilds three kinds of memory from it, the same way
every time. A memory rebuilt from the record can't drift or go stale, and a call survives
a restart by replaying its transcript.

| Memory | What it holds | Rebuilt by | Used for |
|---|---|---|---|
| **Facts** | name, dates, people, the car's limit, power, the sauna, consent, each with the caller's words behind it | the model reads them from both sides of the call; code checks each against the caller's own words (§3) | what to ask, what to offer, whether to book |
| **What has happened** | who the caller is; their bookings; what was booked, moved or cancelled in this call | the API calls recorded in the transcript (`identified_customer`, `own_bookings`) | never asking for a name twice; never reading anyone else's data |
| **Where the conversation is** | our last line: the question pending, the offer just read back (`last_offer`), a refusal already given, a check-in, a finished task | code, from our own lines (D-98) | picking the thread up after a pause, taking "book it" as a yes to the offer, not repeating a refusal, closing a call that's done |

The model sees the facts and the whole call; it never has to remember anything itself.
The cost is a prompt that grows with the call, about 2.1k tokens a turn for booking calls;
a much longer call would need older turns summarised (SPEC §12).

## 1 · One turn, end to end

```mermaid
flowchart TD
  t["Transcript: the whole call so far"] --> gate{"1 · Turn gate<br/>code, no model call"}
  gate -->|"settled: wait, check in, hang up, transfer"| out["Actions"]
  gate -->|"go on"| und["2 · Understand<br/>one strict-JSON call to gpt-4o-mini"]
  und --> val{"Validates?"}
  val -->|"no, first time"| sorry["'Sorry, could you say that again?'"]
  val -->|"no, second time in a row"| hand1["Hand-off to a colleague"]
  val -->|"yes: a CallState"| pol["3 · Policy<br/>code: rules, offers, writes"]
  pol --> sp["4 · Speech<br/>code templates, numbers as words"]
  pol -.->|"API error no branch handles"| hand2["Apology, hand-off to bookings support"]
  sorry --> out
  hand1 --> out
  hand2 --> out
  sp --> out
  out --> note["Every turn ends with a note and one TurnRecord"]
```

| Step | Code | Model calls |
|---|---|---|
| 1 · Turn gate | `agent/turn.py` `gate()` | 0 |
| 2 · Understand | `agent/understand.py` `extract()` | 1 |
| 3 · Policy | `agent/policy.py` `next_step()` | 0 |
| 4 · Speech | `agent/speech.py` `render()`, then the `say` handler's digit guard in `agent/actions.py` | 0 |
| Wiring, fallbacks | `agent/agent.py` `decide()`, `_turn()`: never raises (D-19) | — |

## 2 · The turn gate

Runs before anything else, because speaking while the caller is still talking is the one
mistake a phone receptionist can't take back (D-04 to D-06).

```mermaid
flowchart TD
  start{"Who spoke last?"} -->|"the caller"| fin{"Line still changing,<br/>or only a filler like 'uh'?"}
  fin -->|"yes"| w1["Wait 1.5 s"]
  fin -->|"no"| hold{"'Hang on', 'one sec'?<br/>short, no question"}
  hold -->|"yes"| w8["Wait 8 s"]
  hold -->|"no"| human{"Asks for a person?<br/>strict pattern"}
  human -->|"yes"| tr1["Announce a colleague, transfer"]
  human -->|"no"| angry{"Anger ≥ 0.7<br/>and cut us off?"}
  angry -->|"yes"| tr2["Apologise, transfer"]
  angry -->|"no"| thread{"After our 'anything else?' a short<br/>'no, that's all'? After our check-in<br/>a short 'yes, I'm here'?"}
  thread -->|"closing"| bye2["Goodbye, end the call"]
  thread -->|"still here"| resume["'Great.' and our last question again"]
  thread -->|"neither"| go["Go on to understanding"]
  start -->|"we did"| cut{"We were cut off, or<br/>under 6 s of silence?"}
  cut -->|"yes"| w2["Wait 1.5 s"]
  cut -->|"no"| bye{"Our last line<br/>was a goodbye?"}
  bye -->|"yes"| end1["End the call"]
  bye -->|"no"| again{"Our last line<br/>was a check-in?"}
  again -->|"yes"| end2["'I'll let you go', end the call"]
  again -->|"no"| check["'Are you still there?'"]
```

The last two checks keep the thread of the call (D-98): they read our own last line, so
a short reply that adds nothing new is answered at once, with no model call.

A paraphrase the patterns miss ("could a human help me?") still reaches the model,
whose intents include `human`, `hold` and `goodbye`: a miss costs a model call, not a
wrong answer.

## 3 · Understanding

One call, temperature 0, with a strict JSON schema generated from the `CallState` class
(D-33, D-49). The prompt holds the rules of reading, the sauna catalogue from the API,
a 42-day calendar computed in code (D-16), and the call so far, both sides.

**The model fills, in this order:** a gist; the intent; the caller's name; dates; people;
each fact's quote **before** the fact (tow, sauna, heater reason, consent); power; the
consent flags last. Evidence before value is what fixed the asthma case (D-69).

**Code then checks what came back** (`CallState._facts_need_the_callers_words`):

| Fact | Counts only if | Otherwise | Decision |
|---|---|---|---|
| Towing capacity | its quote is in the caller's own words and carries a number | dropped: the tow is asked | D-08, D-54 |
| Sauna | its quote is the caller's and names or describes a sauna; code resolves the sauna from the quote if the model left it out | dropped | D-54, D-75 |
| People | always; a hedge ("eight, maybe nine") is raised to the higher number | — | D-79 |
| A "yes" to an offer | its quote is from the caller's latest turn, our last line asked to book, and the turn holds no price demand or instruction to the system | void: the offer is read back again | D-10, D-54, D-74 |
| Name | a name the caller asks about is not theirs | dropped | D-78 |
| Dates, numbers, the sauna id | a real date; 1–50 people; 100–20,000 kg; a sauna in the catalogue | unknown, so it is asked again | D-49 |
| The answer as a whole | valid JSON in the schema | "Sorry, could you say that again?"; twice in a row, a colleague | D-19 |

## 4 · The policy

`next_step()` turns the checked state into one decision. Some intents need nobody's data;
everything else waits for identification (D-07, D-11).

```mermaid
flowchart LR
  s["CallState"] --> open{"Intent"}
  open -->|"human"| h["Hand-off to front desk"]
  open -->|"goodbye / hold"| gh["Goodbye / wait"]
  open -->|"price"| pr["Price from the catalogue"]
  open -->|"other customer's info"| rf["Refuse, never name a holder"]
  open -->|"off topic"| ot["Fixed line, back to saunas"]
  open -->|"unclear"| un{"Asked again<br/>last turn?"}
  un -->|"no"| so["'Sorry, say that again?'"]
  un -->|"yes"| h
  open -->|"smalltalk"| sm["Short safe answer, or 'sorry'"]
  open -->|"book, asking what's free"| fd["The next three weekends, how many saunas are free on each"]
  open -->|"anything else"| idq{"Caller identified<br/>earlier in the call?"}
  idq -->|"no"| full{"Full name given?<br/>two words or more"}
  full -->|"no"| askn["Ask for the name"]
  full -->|"yes"| ident["customers.identify<br/>phone + name"]
  ident -->|"no match"| nc["Hand-off to new customers<br/>no sign-up by phone"]
  ident -->|"match"| intent2{"Intent"}
  idq -->|"yes"| intent2
  intent2 -->|"greeting"| need["'What can I do for you?'"]
  intent2 -->|"check booking"| rd["Read the caller's own upcoming bookings"]
  intent2 -->|"change"| ch["Change flow, section 6"]
  intent2 -->|"cancel"| ca["Cancel flow, section 6"]
  intent2 -->|"book"| bk["Booking flow, section 5"]
```

A refusal that would repeat the line we just said word for word closes the topic
instead: "I'm sorry, none of our saunas can take that many people, so I can't book this
one. Is there anything else I can help with?" (D-98).

## 5 · Booking: one question at a time, then an offer

`book()` asks for the **one** next missing fact (D-09), says a problem as soon as it is
known (D-68, D-71), and writes only on a yes to exactly what the caller heard (D-10, D-52).

```mermaid
flowchart TD
  b0{"More people than<br/>the biggest sauna seats?"} -->|"yes"| nf1["Say so now, before any question"]
  b0 -->|"no"| b1{"Dates?"}
  b1 -->|"none"| a1["Ask the dates"]
  b1 -->|"already past"| a2["Say the date has passed"]
  b1 -->|"yes"| b2{"People?"}
  b2 -->|"no"| a3["Ask how many"]
  b2 -->|"yes"| b3{"Car's towing capacity?"}
  b3 -->|"no"| a4["Ask what the car tows"]
  b3 -->|"yes"| b4{"Clashes with the caller's<br/>own booking?"}
  b4 -->|"yes"| oc["Say so, offer to change that one"]
  b4 -->|"no"| se["One availability search, all filters"]
  se --> co["Offer engine: choose_offer"]
  co -->|"nothing fits"| nf2["Say why: too many, car too light,<br/>nothing free on any nearby dates"]
  co -->|"an offer"| b5{"Electric sauna and<br/>power unknown?"}
  b5 -->|"yes"| a5["Ask about mains power"]
  b5 -->|"no"| b6{"Caller said yes to this exact offer?<br/>sauna, dates, people as read back"}
  b6 -->|"no"| of["Read the offer back with the price,<br/>'Shall I book it?'"]
  b6 -->|"yes, or 'book it'"| cr["create: pre-flight, write, retries"]
  cr --> ok["'That's booked', a text message,<br/>'Is there anything else I can help with?'"]
```

**The offer engine** (`choose_offer()`, D-12, D-35):
1. Drop every sauna that breaks a hard limit: too small, too heavy for the car, electric
   without power. Hard limits are never overridden.
2. A heater preference **sorts**; it **filters** only when the caller gave a reason
   ("the smoke bothers me") or named that sauna as a must-have, and a must-have is never
   filtered out (D-69).
3. A booking touching a weekend is stretched to two days (D-13).
4. The sauna the caller asked for, on their dates, if the rules allow it. If its dates
   are taken: another sauna on those dates, or, if they insist on that one or nothing
   else fits, the same sauna moved in time.
5. Otherwise the smallest fitting sauna on their dates.
6. Otherwise the nearest dates that work for any fitting sauna, searching 0, +1, −1, +2,
   −2 … days, never before today.

Every candidate passes `violations()`, the copy of the API's write rules, so nothing is
offered that the API would reject (D-31).

**Writing** (`create()`): the rules run once more before the write. If something changed
since the offer, the new offer is read back and nothing is written. A 409 (someone took
the slot) means a second look and one more try, or the next best offer; a 500 means
checking whether the booking landed before trying once more (D-19). The paths are drawn
in [`sequences.md`](sequences.md).

## 6 · Changing or cancelling

```mermaid
flowchart TD
  c0["The caller's own bookings only"] --> c1{"Which booking?<br/>the date or sauna they named,<br/>or their only one"}
  c1 -->|"unclear"| q1["Ask which booking"]
  c1 -->|"none"| q2["Say there's no booking to change"]
  c1 -->|"found"| c2{"Change or cancel?"}
  c2 -->|"cancel"| c3{"Go-ahead given?"}
  c3 -->|"no"| q3["Read it back, ask to confirm"]
  c3 -->|"yes"| c4["Cancel, with a retry on a version conflict"]
  c2 -->|"change"| c5["New dates computed in code<br/>from the stored booking and the shift"]
  c5 --> c6{"Rules pass without<br/>the old booking?"}
  c6 -->|"no"| q4["Say those dates don't work"]
  c6 -->|"yes"| c7{"Go-ahead given?"}
  c7 -->|"no"| q5["Read the change back, ask to confirm"]
  c7 -->|"yes"| c8["Move it, with a retry on a version conflict,<br/>then a text message"]
```

The booking's own days, and its own turnaround day when the day beyond is free, count
as free when it moves (D-15).

## 7 · Speech

`render(decision, voice)` turns each decision into actions from code templates (D-17):
- **one question** per turn, short sentences, offers ending "Shall I book it?"
- **numbers, dates and euros as words**; the `say` handler turns any digit left into
  words, whatever wrote the line
- **things that aren't done are said outright** ("I can't look at your bookings until I
  know who's calling"), so no reply can be read two ways (D-51, D-87)
- a **calmer, slower voice** when the caller's latest turn sounds angry or upset
- a **text message** after a booking or a move; a **summary for the colleague** on every
  transfer

## Where the model's judgement ends

| Decided by the model | Decided by code |
|---|---|
| what the caller wants (the intent) | whether to answer at all this turn |
| what they said: name, dates, people, tow, sauna, heater, power, consent, with quotes | whether each of those facts counts |
| — | which question comes next |
| — | which sauna, which dates, what price |
| — | whether to write, and what |
| — | every word the receptionist says |

So a model mistake can cost an extra question or a "say that again", never a booking
that breaks a rule or a reply the code didn't write.
