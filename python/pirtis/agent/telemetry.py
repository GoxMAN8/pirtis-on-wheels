"""Telemetry: one record per turn, and no content.

A TurnRecord says what happened on a turn (which path, what was decided, what it cost,
which API calls were made) and nothing the caller said: no text, no names, and the
phone only as a salted hash. It is written as one JSON line on the "pirtis.turn"
logger. That logger is silent unless whoever runs the agent attaches a handler, as
the playground and the gate job do; the harness stays quiet.

The record is our own, versioned model. OpenTelemetry's GenAI names are a rendering of
it (to_otel), so a change in that convention touches one function, not the record.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from pirtis.api.client import Call
from pirtis.llm.client import MODEL, Stats
from pirtis.transcript.types import Action, Transcript

TELEMETRY_VERSION = 1

log = logging.getLogger("pirtis.turn")
log.addHandler(logging.NullHandler())
log.setLevel(logging.INFO)


@dataclass(frozen=True)
class ApiCallRecord:
    method: str
    status: int
    code: str


@dataclass(frozen=True)
class TurnRecord:
    telemetry_version: int
    ts: str  # UTC, ISO 8601
    call_id: str
    turn_id: str
    agent_version: str
    gate: str  # "none" | "wait" | "check_in" | "hang_up" | "transfer"
    intent: str  # "" when the model was not asked
    decision: str  # the policy's decision, "" when the gate settled the turn
    action_types: tuple[str, ...]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    api_calls: tuple[ApiCallRecord, ...]
    fallback: bool  # an apology or a hand-off because something failed
    transfer_reason: str
    phone_hash: str


def phone_hash(phone: str) -> str:
    """A salted hash of the phone's digits: the same caller links across turns, but the
    number itself never leaves. The salt comes from PIRTIS_HASH_SALT."""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    salt = os.environ.get("PIRTIS_HASH_SALT", "pirtis")
    return hashlib.sha256((salt + digits).encode()).hexdigest()[:16]


def turn_id(t: Transcript) -> str:
    """Stable for one turn of one call: the call id, how far the call has got, and when."""
    return hashlib.sha256(f"{t.call_id}:{len(t.items)}:{t.now}".encode()).hexdigest()[:16]


def build(
    t: Transcript,
    *,
    agent_version: str,
    gate: str,
    intent: str,
    decision: str,
    fallback: bool,
    actions: list[Action],
    stats: Stats,
    calls: list[Call],
    latency_ms: int,
) -> TurnRecord:
    return TurnRecord(
        telemetry_version=TELEMETRY_VERSION,
        ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
        call_id=t.call_id,
        turn_id=turn_id(t),
        agent_version=agent_version,
        gate=gate,
        intent=intent,
        decision=decision,
        action_types=tuple(str(a.get("type")) for a in actions),
        llm_calls=stats.calls,
        input_tokens=stats.prompt_tokens,
        output_tokens=stats.completion_tokens,
        latency_ms=latency_ms,
        api_calls=tuple(ApiCallRecord(c.method, c.status, c.code) for c in calls),
        fallback=fallback,
        transfer_reason=next(
            (str(a.get("reason")) for a in actions if a.get("type") == "transfer"), ""
        ),
        phone_hash=phone_hash(t.caller.phone),
    )


def to_otel(r: TurnRecord) -> dict[str, Any]:
    """The record as OpenTelemetry attributes: GenAI names where the convention has them
    (both the older gen_ai.system and the newer gen_ai.provider.name, while tools differ
    on which they read), our own names for the rest."""
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "openai",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL,
        "gen_ai.usage.input_tokens": r.input_tokens,
        "gen_ai.usage.output_tokens": r.output_tokens,
        "pirtis.call.id": r.call_id,
        "pirtis.turn.id": r.turn_id,
        "pirtis.agent.version": r.agent_version,
        "pirtis.gate": r.gate,
        "pirtis.intent": r.intent,
        "pirtis.decision": r.decision,
        "pirtis.latency_ms": r.latency_ms,
        "pirtis.llm.calls": r.llm_calls,
        "pirtis.api.calls": len(r.api_calls),
        "pirtis.api.errors": sum(c.status >= 400 for c in r.api_calls),
        "pirtis.fallback": r.fallback,
    }


def emit(r: TurnRecord) -> None:
    """Writes the record as one JSON line. Never raises: telemetry must not cost a turn."""
    try:
        log.info(json.dumps({"event": "turn", **asdict(r), "otel": to_otel(r)}))
    except Exception:  # a broken log handler must never reach the caller
        pass
