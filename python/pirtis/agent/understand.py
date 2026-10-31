"""Understanding: one model call turns the call so far into a validated CallState.

The model only records what the caller said. Code applies every rule afterwards, so a
wrong guess here can cost a question, never a booking that breaks a rule.
"""

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from pirtis.api.types import Unit
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import Message, Request, ResponseFormat
from pirtis.transcript.types import Transcript


class UnderstandError(Exception):
    """The model's answer could not be read, or it did not validate."""


HEATERS = ("wood", "electric")


class CallState(BaseModel):
    """What the caller has told us so far, as the model heard it. Facts, never rules.

    Field order is the order the model writes them in: a short gist first, each
    caller quote before the value taken from it, and the consent flag last.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    gist: str = Field(description="At most 15 words: what the caller wants in their latest turn.")
    intent: Literal[
        "book",
        "change",
        "cancel",
        "check_booking",
        "price",
        "other_customer_info",
        "human",
        "goodbye",
        "hold",
        "smalltalk",
        "unclear",
    ] = Field(
        description="What the caller wants now. book: rent a sauna. change: move or change "
        "their existing booking. cancel: cancel their existing booking. check_booking: ask "
        "about their own booking. price: ask what something costs. other_customer_info: ask "
        "who has a booking or for someone else's details. human: ask for a person. goodbye: "
        "end the call. hold: ask us to wait a moment. smalltalk: anything else we can answer "
        "briefly. unclear: you cannot tell."
    )
    full_name: str | None = Field(
        description="The caller's full name exactly as said, keeping letters like ė, ž, š; "
        "join spelled-out letters into the word. Null if no name was given."
    )
    people: int | None = Field(
        description="How many people. If they hedge ('seven, maybe eight'), the higher number."
    )
    start: str | None = Field(
        description="First day they asked for, YYYY-MM-DD, read off the calendar. For a change, "
        "the new first day if they named one."
    )
    end: str | None = Field(
        description="Last day, YYYY-MM-DD. The same as start when they asked for a single day."
    )
    shift_days: int | None = Field(
        description="For a change: how many days to move their booking by, if they said so "
        "('push it a week' is 7, 'a day earlier' is -1). Null otherwise."
    )
    change_target_start: str | None = Field(
        description="For a change or cancel: the first day of the existing booking they mean, "
        "YYYY-MM-DD, if the call shows it. Null otherwise."
    )
    tow_quote: str | None = Field(
        description="The caller's own words that give a number for what the car can tow, "
        "copied exactly. Null if they gave no number."
    )
    tow_kg: int | None = Field(
        description="Braked towing capacity in kg, from tow_quote. Tonnes times 1000. "
        "If they hedge, the lower number. Null when tow_quote is null."
    )
    power: bool | None = Field(
        description="True if the site has mains electricity, false if it has none "
        "(a generator is not mains), null if not said."
    )
    unit: str | None = Field(description="The sauna the caller asked for by name or description.")
    unit_is_must: bool = Field(
        description="True if the caller said it has to be that sauna, e.g. for health "
        "reasons or 'it has to be that one'."
    )
    heater_pref: str | None = Field(description="The heater type the caller wants, if they said.")
    heater_reason: str | None = Field(
        description="The caller's reason for that heater, if they gave one, in their words "
        "(e.g. 'the wood smoke sets off my asthma'). Null otherwise."
    )
    answer: str | None = Field(
        description="Only for smalltalk: one or two short sentences answering the caller, "
        "using only facts given here. Never about bookings or availability. Null otherwise."
    )
    accepted_offer: bool = Field(
        description="True only if the receptionist's last message offered something "
        "specific (a sauna and dates, or to move or cancel a booking) and the caller said yes."
    )
    go_ahead: bool = Field(
        description="True if the caller explicitly told us to do it now ('just move it', "
        "'yes, cancel it', 'book it')."
    )

    @field_validator("start", "end", "change_target_start")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        """An unreadable date becomes unknown, so we ask again rather than book it."""
        if v is None:
            return None
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            return None

    @field_validator("people")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        return v if v is not None and v > 0 else None

    @field_validator("unit")
    @classmethod
    def _known_unit(cls, v: str | None, info: ValidationInfo) -> str | None:
        ids = (info.context or {}).get("unit_ids")
        return v if v is None or ids is None or v in ids else None

    @field_validator("heater_pref")
    @classmethod
    def _known_heater(cls, v: str | None) -> str | None:
        return v if v in HEATERS else None

    @model_validator(mode="before")
    @classmethod
    def _tow_needs_a_quote(cls, data: Any, info: ValidationInfo) -> Any:
        """A towing capacity counts only if the caller really said a number for it."""
        lines = (info.context or {}).get("caller_lines")
        if not isinstance(data, dict) or lines is None:
            return data
        if data.get("tow_kg") is not None and not quote_supported(data.get("tow_quote"), lines):
            return {**data, "tow_kg": None}
        return data


# words that make a quote carry a number, besides digits
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "fifteen", "twenty", "thirty", "forty", "fifty", "sixty",
    "seventy", "eighty", "ninety", "hundred", "thousand", "half", "point", "tonne",
    "tonnes", "ton", "tons", "kilo", "kilos", "kg",
}  # fmt: skip


def quote_supported(quote: object, caller_lines: Sequence[str]) -> bool:
    """True when most of the quote's words appear in one caller line and the quote
    carries a number. The model may tidy a quote, so the match is by words, not text."""
    if not isinstance(quote, str) or not quote.strip():
        return False
    words = _words(quote)
    if not words or not (any(w.isdigit() for w in words) or words & _NUMBER_WORDS):
        return False
    return any(len(words & _words(line)) >= 0.7 * len(words) for line in caller_lines)


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", fold(s).lower()))


def fold(s: str) -> str:
    """Drops diacritics, for matching only: "Mažoji" and "Mazoji" are the same word."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def strict_schema(
    model: type[BaseModel], enums: Mapping[str, Sequence[str]] | None = None
) -> dict[str, Any]:
    """The model's JSON schema in the shape strict structured output accepts: every
    field required, nulls allowed through anyOf, no extra keys, no titles or defaults.
    enums narrows a string field to the given values, or null."""
    props: dict[str, Any] = {}
    for name, prop in model.model_json_schema()["properties"].items():
        prop = _clean(prop)
        if enums and name in enums:
            prop = {
                "anyOf": [{"type": "string", "enum": list(enums[name])}, {"type": "null"}],
                "description": prop.get("description", ""),
            }
        props[name] = prop
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _clean(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _clean(v) for k, v in node.items() if k not in ("title", "default")}
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


RULES = """You read a phone call to Pirtis on Wheels, which rents mobile saunas on trailers, \
and you record what the caller has said so far.

Record facts only. Never apply booking rules, never judge availability, and never invent a \
value: if the caller has not said it, use null.
The latest statement wins: if the caller corrects themselves, record the correction.
Dates: turn the caller's words into YYYY-MM-DD using the calendar you are given. Record what \
they asked for, even if it seems impossible; a single day means start and end are the same.
The towing capacity must come from a number the caller said. "It's fine" or "we towed a \
caravan" is not a number.
"""


def system_prompt(units: Sequence[Unit]) -> str:
    """The rules and the catalogue: the part of the prompt that stays the same from turn
    to turn, so it can be cached."""
    by_size = sorted(units, key=lambda u: (u.capacity, u.gross_weight_kg))
    lines = []
    for i, u in enumerate(by_size):
        size = "the smallest" if i == 0 else "the biggest" if i == len(by_size) - 1 else ""
        heater = "electric, needs mains power" if u.needs_power else "wood-fired"
        lines.append(
            f"- {u.id}: {u.name}, {u.capacity} people, {heater}, {u.gross_weight_kg} kg"
            + (f", {size}" if size else "")
        )
    return RULES + "\nThe saunas:\n" + "\n".join(lines) + "\n"


def calendar(today: str, days: int = 42) -> str:
    """Today, the common date phrases already resolved, and the next weeks by weekday."""
    t = date.fromisoformat(today)
    sat = t + timedelta(days=(5 - t.weekday()) % 7) if t.weekday() != 6 else t - timedelta(1)
    lines = [
        f"Today is {t.strftime('%A')} {t.isoformat()}.",
        f"Tomorrow: {(t + timedelta(1)).isoformat()}.",
        f'"This weekend" and "next weekend": {sat.isoformat()} to {(sat + timedelta(1)).isoformat()}.',  # noqa: E501
        f'"The weekend after next": {(sat + timedelta(7)).isoformat()} to '
        f"{(sat + timedelta(8)).isoformat()}.",
        "Calendar:",
    ]
    lines += [
        f"{(t + timedelta(i)).isoformat()} {(t + timedelta(i)).strftime('%A')}" for i in range(days)
    ]
    return "\n".join(lines)


def render_transcript(t: Transcript) -> str:
    """The call as the model reads it: settled caller speech, our lines (marked when the
    caller cut us off), and short notes for API calls made earlier in the call."""
    names: dict[str, str] = {}
    lines = []
    for it in t.items:
        match it.type:
            case "message" if it.role == "assistant":
                cut = " (cut off)" if it.interrupted else ""
                lines.append("receptionist: " + it.content + cut)
            case "message" if it.is_final:
                lines.append("caller: " + it.content)
            case "function_call":
                names[it.call_id] = it.name
            case "function_call_output":
                lines.append(f"[system: {names.get(it.call_id, 'call')} -> {_short(it.output)}]")
    return "\n".join(lines)


def _short(output: str) -> str:
    try:
        data = json.loads(output)
    except ValueError:
        return output[:120]
    if isinstance(data, dict) and "ok" in data:
        return "caller identified" if data.get("ok") else "caller not found"
    return json.dumps(data, ensure_ascii=False)[:300]


def caller_lines(t: Transcript) -> list[str]:
    return [it.content for it in t.items if it.type == "message" and it.role == "user"]


def extract(t: Transcript, llm: LlmClient, units: Sequence[Unit]) -> CallState:
    """One model call: the call so far in, a validated CallState out."""
    ids = [u.id for u in units]
    call = calendar(t.today) + "\n\nThe call:\n" + render_transcript(t)
    resp = llm.chat(
        Request(
            messages=[
                Message(role="system", content=system_prompt(units)),
                Message(role="user", content=call),
            ],
            temperature=0.0,
            response_format=ResponseFormat(
                name="call_state",
                schema=strict_schema(CallState, {"unit": ids, "heater_pref": HEATERS}),
                strict=True,
            ),
        )
    )
    try:
        return CallState.model_validate(
            json.loads(resp.content), context={"unit_ids": ids, "caller_lines": caller_lines(t)}
        )
    except ValueError as e:  # bad JSON, or a pydantic ValidationError
        raise UnderstandError(str(e)) from e
