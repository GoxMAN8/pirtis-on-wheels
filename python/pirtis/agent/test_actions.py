"""The handlers are the last step before the caller hears or receives anything, so they
are where a few guarantees are made certain: no digit is ever spoken, a transfer
always carries its summary, and a text goes to the number that called."""

from pirtis.agent.actions import HandlerContext, handlers, handoff, note, sms
from pirtis.api.memory import Memory, Seed
from pirtis.transcript.types import Caller, Transcript, end_call, say, wait

CTX = HandlerContext(
    api=Memory(Seed()), transcript=Transcript(today="2026-09-28", caller=Caller("+37061234567"))
)


def run(action: dict[str, object]) -> dict[str, object]:
    return handlers[str(action["type"])](action, CTX)


def test_say_never_speaks_a_digit() -> None:
    effect = run(say("That's  240\neuros.", "calm", 0.95))
    assert effect == {
        "type": "speech",
        "text": "That's two hundred and forty euros.",
        "generation_config": {"emotion": "calm", "speed": 0.95},
    }


def test_transfer_carries_its_summary() -> None:
    effect = run(handoff("front_desk", "caller asked for a person", "Tomas Petrauskas, 6 people"))
    assert effect == {
        "type": "transfer",
        "to": "front_desk",
        "reason": "caller asked for a person",
        "summary": "Tomas Petrauskas, 6 people",
    }


def test_sms_goes_to_the_caller_and_may_use_digits() -> None:
    effect = run(sms("Mažoji booked, 6–7 Oct, €240"))
    assert effect == {"type": "sms", "to": "+37061234567", "text": "Mažoji booked, 6–7 Oct, €240"}


def test_note_records_fields_for_the_trace() -> None:
    effect = run(note(decision="Offer", intent="book", agent_version="abc123"))
    assert effect == {
        "type": "note",
        "decision": "Offer",
        "intent": "book",
        "agent_version": "abc123",
    }


def test_every_action_we_build_has_a_handler() -> None:
    built = [
        say("hi", "neutral", 1.0),
        wait(1.5),
        handoff("q", "r", "s"),
        end_call("bye"),
        sms("t"),
        note(decision="x"),
    ]
    for a in built:
        assert a["type"] in handlers
    assert set(handlers) == {"say", "wait", "transfer", "end_call", "sms", "note"}
