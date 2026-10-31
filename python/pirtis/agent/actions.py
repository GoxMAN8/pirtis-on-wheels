from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pirtis.agent.words import digits_to_words
from pirtis.api.client import Client
from pirtis.transcript.types import Action, Effect, Transcript


@dataclass
class HandlerContext:
    """What a handler can reach while carrying out an action."""

    api: Client
    transcript: Transcript


# A Handler carries out one action and reports what happened as an Effect.
Handler = Callable[[Action, HandlerContext], Effect]


def _say(a: Action, _: HandlerContext) -> Effect:
    # the last stop before the voice: whatever wrote the text, no digit is ever spoken
    text = " ".join(digits_to_words(str(a.get("text") or "")).split())
    return {
        "type": "speech",
        "text": text,
        "generation_config": a.get("generation_config"),
    }


def _wait(a: Action, _: HandlerContext) -> Effect:
    return {"type": "silence", "seconds": a.get("seconds")}


def _transfer(a: Action, _: HandlerContext) -> Effect:
    # the summary travels with the call, so the caller doesn't have to repeat themselves
    return {
        "type": "transfer",
        "to": a.get("to"),
        "reason": a.get("reason"),
        "summary": a.get("summary", ""),
    }


def _end_call(a: Action, _: HandlerContext) -> Effect:
    return {"type": "hangup", "reason": a.get("reason")}


def _sms(a: Action, ctx: HandlerContext) -> Effect:
    # only ever to the number that called; a text is read, so figures are fine
    return {"type": "sms", "to": ctx.transcript.caller.phone, "text": a.get("text")}


def _note(a: Action, _: HandlerContext) -> Effect:
    # what the agent decided and why, kept in the run's result for tracing
    return {**a, "type": "note"}


def sms(text: str) -> Action:
    """Texts the caller a written confirmation."""
    return {"type": "sms", "text": text}


def handoff(to: str, reason: str, summary: str) -> Action:
    """A transfer that carries what we already know about the call."""
    return {"type": "transfer", "to": to, "reason": reason, "summary": summary}


def note(**fields: Any) -> Action:
    """Records fields in the run's effects. Nothing is said or sent."""
    return {"type": "note", **fields}


# handlers maps an action type to the code that carries it out. The harness
# runs every action decide returns through the handler for its type and
# asserts on the effects. An action with no handler fails the fixture.
handlers: dict[str, Handler] = {
    "say": _say,
    "wait": _wait,
    "transfer": _transfer,
    "end_call": _end_call,
    "sms": _sms,
    "note": _note,
}
