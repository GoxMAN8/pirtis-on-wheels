> Verified against `3e57ec9` on 2026-10-31.

# Sequences

The two paths that write something a customer relies on: a new booking, and a change
to one. The participants are the containers in `containers.md`.

## 1 · A booking, on the caller's yes to our read-back

```mermaid
sequenceDiagram
  participant caller as Caller simulator
  participant server as Playground server
  participant agent as decide()
  participant llm as gpt-4o-mini
  participant api as Rental API
  participant data as Data directory
  caller->>server: POST /api/turn "yes, book it" (session cookie)
  server->>agent: decide(the call so far)
  Note over agent: turn gate first, no model call: still talking, silence, a request for a person
  agent->>api: units.list
  agent->>llm: one strict-JSON extraction of the call so far
  break the answer doesn't validate (UnderstandError)
    agent-->>server: "Sorry, could you say that again?" (twice in a row: put through to a person)
  end
  llm-->>agent: call state with accepted_offer and the consent quote
  Note over agent: a yes counts only if our last line was the offer, the quote is in the caller's latest words, and holds no injection or price demand (D-10, D-74)
  agent->>agent: violations() pre-flight on the offer
  alt something changed since we offered
    agent-->>server: offer again, nothing is written
  else the offer still holds
    agent->>api: bookings.create
    alt 409 UNAVAILABLE: someone took the slot meanwhile
      api-->>agent: 409
      agent->>api: units.availability again
      alt still free on a second look
        agent->>api: bookings.create, once more
      else gone
        agent-->>server: the next best offer, nothing is written
      end
    else 500: did it land anyway?
      agent->>api: bookings.list for this caller
      Note over agent,api: landed: treated as booked. Not landed: one more try. A second 500: put through to a colleague
    else created
      api-->>agent: the booking
      agent-->>server: "That's booked", an SMS, a note
    end
  end
  server->>data: save the call (temp file, then rename)
  server-->>caller: the reply, the booking card, the SMS preview
```

The pre-flight `violations()` mirrors every rule the API enforces and is property-tested
against it (`python/pirtis/agent/rules.py`), so a 422 can't happen by construction. The
retries are in `create()` (`python/pirtis/agent/policy.py`); a failure no branch
handles becomes a hand-off to a person (`python/pirtis/agent/agent.py`).

## 2 · Moving or cancelling a booking

```mermaid
sequenceDiagram
  participant caller as Caller simulator
  participant server as Playground server
  participant agent as decide()
  participant llm as gpt-4o-mini
  participant api as Rental API
  caller->>server: POST /api/turn "move it a week later"
  server->>agent: decide(the call so far)
  agent->>llm: extraction: intent change, shift_days 7, go-ahead?
  llm-->>agent: call state
  agent->>api: bookings.list (customer_id: the identified caller only)
  Note over agent: the target is picked from the caller's own bookings, the new dates are computed in code from the stored booking
  alt no go-ahead yet
    agent-->>server: read the change back, ask to confirm
  else go-ahead, and the pre-flight passes without the old booking
    agent->>api: bookings.update(id, version, the new dates)
    alt 409 VERSION_MISMATCH: the booking changed meanwhile
      api-->>agent: 409
      agent->>api: bookings.list again, for the fresh version
      agent->>api: bookings.update(id, fresh version), once
    end
    break any other API error, or the retry fails too
      agent-->>server: an apology, then put through to a colleague (bookings support)
    end
    api-->>agent: the moved booking
    agent-->>server: "That's moved", an SMS, a note
  end
  server-->>caller: the reply and the updated booking card
```

A cancel is the same path with `status: cancelled` as the change. The retry is
`update_with_retry()` in `python/pirtis/agent/policy.py`.

**Not built:** an idempotency key on writes. A double submit is safe today only because
one call is one session handled one turn at a time, and the 500 branch looks before
retrying.
