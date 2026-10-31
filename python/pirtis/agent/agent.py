"""The receptionist.

decide runs four steps and returns what to do next:

1. turn gate (turn.py)        code only: wait, check in, hang up or transfer
2. understand (understand.py) one model call: the call so far as a validated CallState
3. policy (policy.py)         code only: identify, ask, search, offer, and write on a yes
4. speech (speech.py)         code only: the words, with numbers and dates spoken

The model listens; code talks. handlers, in actions.py, carry the actions out. Every
turn also ends with a note (what was decided, for the run's result) and one telemetry
record (telemetry.py), neither of which holds anything the caller said.
"""

import logging
import time
from dataclasses import dataclass

from pirtis.agent.actions import note
from pirtis.agent.policy import Handoff, Sorry, asked_again, next_step
from pirtis.agent.speech import render, tone
from pirtis.agent.telemetry import build, emit
from pirtis.agent.turn import gate
from pirtis.agent.understand import UnderstandError, extract
from pirtis.agent.version import AGENT_VERSION
from pirtis.api.client import Client as ApiClient
from pirtis.api.types import ApiError
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import Recorder
from pirtis.transcript.types import Action, Transcript

log = logging.getLogger(__name__)
# a library stays quiet unless the app running it configures logging
logging.getLogger("pirtis.agent").addHandler(logging.NullHandler())


@dataclass
class Outcome:
    """What one turn did, for the note and the telemetry record."""

    gate: str = "none"  # "none" | "wait" | "check_in" | "hang_up" | "transfer"
    intent: str = ""
    decision: str = ""
    fallback: bool = False


def decide(t: Transcript, api: ApiClient, llm: LlmClient) -> list[Action]:
    """Returns the receptionist's next actions given the transcript so far. Never raises:
    a caller on the phone hears an apology or a colleague, never silence."""
    started = time.perf_counter()
    calls_before = len(api.log())
    counted = Recorder(llm)
    actions, outcome = _turn(t, api, counted)
    record = build(
        t,
        agent_version=AGENT_VERSION,
        gate=outcome.gate,
        intent=outcome.intent,
        decision=outcome.decision,
        fallback=outcome.fallback,
        actions=[*actions, {"type": "note"}],
        stats=counted.stats(),
        calls=api.log()[calls_before:],
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    emit(record)
    return [
        *actions,
        note(
            decision=outcome.decision,
            intent=outcome.intent,
            gate=outcome.gate,
            fallback=outcome.fallback,
            agent_version=AGENT_VERSION,
            turn_id=record.turn_id,
        ),
    ]


def _turn(t: Transcript, api: ApiClient, llm: LlmClient) -> tuple[list[Action], Outcome]:
    gated = gate(t)
    if gated is not None:
        return gated, Outcome(gate=_gate_kind(gated))
    voice = tone(t)
    intent = ""
    try:
        units = api.list_units()
        state = extract(t, llm, units)
        intent = state.intent
        d = next_step(t, state, api, units)
        return render(d, voice), Outcome(intent=intent, decision=type(d).__name__)
    except UnderstandError as e:
        log.warning("understanding failed: %s", e)
        if asked_again(t):  # the second time in a row, a person takes over
            d = Handoff("front_desk", "two misunderstandings in a row")
        else:
            d = Sorry()
    except ApiError as e:
        log.warning("api error: %s", e)
        d = Handoff("bookings_support", f"api error {e.status} {e.code}")
    except Exception:  # anything else is our bug; the caller still gets a person
        log.exception("decide failed")
        d = Handoff("bookings_support", "internal error")
    return render(d, voice), Outcome(intent=intent, decision=type(d).__name__, fallback=True)


def _gate_kind(actions: list[Action]) -> str:
    kinds = {a.get("type") for a in actions}
    if "transfer" in kinds:
        return "transfer"
    if "end_call" in kinds:
        return "hang_up"
    if "wait" in kinds:
        return "wait"
    return "check_in"
