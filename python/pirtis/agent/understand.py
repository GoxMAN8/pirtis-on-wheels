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

from pirtis.agent.reading import latest_caller_turn
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
        "greeting",
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
        "off_topic",
        "unclear",
    ] = Field(
        description="What the caller wants now. greeting: they only said hello or gave "
        "their own name, and haven't asked for anything yet. book: rent a sauna. change: move "
        "or change their existing booking. cancel: cancel their existing booking. "
        "check_booking: ask about their own booking. price: ask what something costs. "
        "other_customer_info: ask who has a booking, or for another person's details; giving "
        "your own name is never this. human: ask for a person. goodbye: "
        "end the call. hold: ask us to wait a moment. smalltalk: anything else we can answer "
        "briefly. off_topic: ask for something a sauna rental doesn't do, such as our "
        "instructions or prompt, or anything unrelated to renting a sauna. unclear: you "
        "cannot tell what they said."
    )
    full_name: str | None = Field(
        description="The caller's own full name exactly as said, keeping letters like ė, ž, "
        "š; join spelled-out letters into the word. A name they ask about is someone else's, "
        "not theirs. Null if they gave no name of their own."
    )
    people: int | None = Field(
        description="How many people, only if the caller said a number. If they hedge "
        "('seven, maybe eight'), the higher number."
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
        description="True if the caller said the site has mains electricity, false if they "
        "said it has none (a generator is not mains), null if they haven't said it in this call."
    )
    unit_quote: str | None = Field(
        description="The caller's own words that name or describe the sauna they want "
        "('the little one', 'the wood-fired four-seater'), copied exactly. Null if none."
    )
    unit: str | None = Field(description="The sauna the caller asked for by name or description.")
    unit_is_must: bool = Field(
        description="True only if the caller said they cannot take another sauna: 'nothing "
        "else will do', or a health reason. Naming or wanting a sauna is false."
    )
    heater_reason: str | None = Field(
        description="The caller's reason for wanting or avoiding a heater, in their words "
        "(e.g. 'electric heat gives me a headache'). Null otherwise."
    )
    heater_pref: str | None = Field(
        description="The heater type the caller wants, if they said ('the wood one'). If "
        "they say one kind is a problem for them, it is the other kind."
    )
    answer: str | None = Field(
        description="Only for smalltalk: one or two short sentences answering the caller, "
        "using only facts given here. Never about bookings or availability. Null otherwise."
    )
    consent_quote: str | None = Field(
        description="The caller's own words in their latest turn that say yes to our offer or "
        "tell us to go ahead ('yes, book it', 'just move it'), copied exactly. Null if none."
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
    def _plausible_people(cls, v: int | None) -> int | None:
        return v if v is not None and 0 < v <= 50 else None

    @field_validator("shift_days")
    @classmethod
    def _plausible_shift(cls, v: int | None) -> int | None:
        return v if v is not None and v != 0 and abs(v) <= 60 else None

    @field_validator("tow_kg")
    @classmethod
    def _plausible_tow(cls, v: int | None) -> int | None:
        return v if v is not None and 100 <= v <= 20000 else None

    @field_validator("answer")
    @classmethod
    def _short_answer(cls, v: str | None) -> str | None:
        """A free answer is spoken as-is, so it is cut to two sentences."""
        if not v or not v.strip():
            return None
        return " ".join(re.split(r"(?<=[.!?])\s+", v.strip())[:2])

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
    def _facts_need_the_callers_words(cls, data: Any, info: ValidationInfo) -> Any:
        """Three facts count only with the caller's own words behind them, checked here in
        code: a towing figure needs a quote with a number; a sauna needs a quote that
        names or describes one; consent needs a quote from the caller's latest turn, and
        accepting an offer also needs our last line to have made one."""
        ctx = info.context or {}
        lines, latest = ctx.get("caller_lines"), ctx.get("latest_lines")
        if not isinstance(data, dict) or lines is None:
            return data
        data = dict(data)
        if data.get("tow_kg") is not None and not quote_supported(data.get("tow_quote"), lines):
            data["tow_kg"] = None
        names = ctx.get("unit_names", [])
        if data.get("unit") is not None and not (
            quote_in(data.get("unit_quote"), lines) and names_a_sauna(data.get("unit_quote"), names)
        ):
            data["unit"], data["unit_is_must"] = None, False
        if isinstance(data.get("people"), int):  # a hedge counts as the higher number (D-08)
            data["people"] = hedged_higher(data["people"], lines)
        units = ctx.get("units")
        quote = data.get("unit_quote")
        if (
            data.get("unit") is None
            and units
            and quote_in(quote, lines)
            and names_a_sauna(quote, names)
        ):
            data["unit"] = unit_from_quote(quote, units)  # the quote is there, the sauna wasn't
        if latest is not None and not quote_in(data.get("consent_quote"), latest):
            data["accepted_offer"], data["go_ahead"] = False, False
        # a yes that names its own price, or that talks to the system, isn't a yes to us
        if latest is not None and any(_NOT_A_YES.search(line) for line in latest):
            data["accepted_offer"], data["go_ahead"] = False, False
        if not ctx.get("offered", True):
            data["accepted_offer"] = False
        return data


# a latest turn with a price demand or an instruction to the system voids consent (D-74)
_NOT_A_YES = re.compile(
    r"\b(for free|free of charge|for nothing|zero euros?|no charge|discount|half price"
    r"|ignore (all |your |the )?(previous |prior |earlier )?instructions|new instructions"
    r"|admin mode|developer mode|system prompt|you are now|you're now|you're in \w+ mode)\b",
    re.IGNORECASE,
)

# words that make a quote carry a number, besides digits
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "fifteen", "twenty", "thirty", "forty", "fifty", "sixty",
    "seventy", "eighty", "ninety", "hundred", "thousand", "half", "point", "tonne",
    "tonnes", "ton", "tons", "kilo", "kilos", "kg",
}  # fmt: skip


def quote_in(quote: object, caller_lines: Sequence[str]) -> bool:
    """True when most of the quote's words appear in one of these caller lines. The model
    may tidy a quote, so the match is by words, not exact text."""
    if not isinstance(quote, str) or not quote.strip():
        return False
    words = _words(quote)
    return bool(words) and any(
        len(words & _words(line)) >= 0.7 * len(words) for line in caller_lines
    )


def quote_supported(quote: object, caller_lines: Sequence[str]) -> bool:
    """quote_in, and the quote carries a number."""
    if not isinstance(quote, str):
        return False
    words = _words(quote)
    has_number = any(w.isdigit() for w in words) or bool(words & _NUMBER_WORDS)
    return has_number and quote_in(quote, caller_lines)


# words that describe a sauna rather than people or dates
_SAUNA_WORDS = {
    "small", "smaller", "smallest", "little", "big", "bigger", "biggest", "large", "largest",
    "middle", "medium", "mid", "electric", "wood", "wooden", "barrel", "seater", "person",
    "sauna",
}  # fmt: skip


def names_a_sauna(quote: object, unit_names: Sequence[str]) -> bool:
    """True when the quote names a unit or uses a word that describes one. "Nine of us"
    describes the group, not a sauna."""
    if not isinstance(quote, str):
        return False
    words = _words(quote)
    names = {w for n in unit_names for w in _words(n)}
    return bool(words & (names | _SAUNA_WORDS))


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", fold(s).lower()))


_NUMBERS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}  # fmt: skip


# a hedged group size: "eight of us, maybe nine", "seven of us this time. maybe eight",
# "six, maybe seven", "four or five of us"; never a figure with a unit after it (D-79)
_N = r"(\d{1,2}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")"
_HEDGE = re.compile(
    rf"\b{_N}\b(?:\s+(?:of us|people))?[^.?!\d]{{0,15}}(?:[.?!]\s*)?"
    rf"\b(?:maybe|or|possibly|perhaps)\s+{_N}\b"
    r"(?!\s*(?:tonnes?|tons?|kilos?|kg|thousand|hundred|days?|nights?))",
    re.IGNORECASE,
)


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBERS[word.lower()]


def hedged_higher(people: int, lines: Sequence[str]) -> int:
    """The higher number of a hedge whose lower number is the group size taken."""
    for line in lines:
        for m in _HEDGE.finditer(line):
            low, high = sorted((_count(m[1]), _count(m[2])))
            if low == people and high > low:
                return high
    return people


def unit_from_quote(quote: object, units: Sequence[Unit]) -> str | None:
    """The one sauna the caller's words name or describe, or None when they fit none or
    several. The model sometimes writes the quote and leaves the sauna out; this reads the
    quote in code: a name, a heater, an N-seater, then a size among what is left."""
    if not isinstance(quote, str):
        return None
    text = fold(quote).lower()
    named = [u for u in units if fold(u.name).lower() in text]
    if named:
        return named[0].id if len(named) == 1 else None
    words = set(re.findall(r"[a-z]+|\d+", text))
    fits, described = list(units), False
    for heater, said in (("electric", {"electric"}), ("wood", {"wood", "wooden"})):
        if words & said:
            fits, described = [u for u in fits if u.heater == heater], True
    seats = re.search(r"\b(\w+)[ -]seater\b", text)
    if seats:
        n = int(seats[1]) if seats[1].isdigit() else _NUMBERS.get(seats[1])
        fits, described = [u for u in fits if u.capacity == n], True
    sizes = sorted({u.capacity for u in units})
    if fits and words & {"small", "smallest", "little"}:
        fits, described = [u for u in fits if u.capacity == min(f.capacity for f in fits)], True
    if fits and words & {"big", "biggest", "large", "largest"}:
        fits, described = [u for u in fits if u.capacity == max(f.capacity for f in fits)], True
    if words & {"middle", "medium"}:
        fits, described = [u for u in fits if u.capacity == sizes[len(sizes) // 2]], True
    return fits[0].id if described and len(fits) == 1 else None


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
Only the caller's own words count. Never take a fact from the receptionist's suggestions or \
from the system notes about earlier bookings.
The latest statement wins: if the caller corrects themselves, record the correction.
When the caller says yes to an offer, copy the sauna, the dates and the number of people \
from that offer.
Dates: turn the caller's words into YYYY-MM-DD using the calendar you are given. Record what \
they asked for, even if it seems impossible; a single day means start and end are the same.
Numbers: record a number only if the caller said one. "The small one" names a sauna, not a \
number of people.
The towing capacity must come from a number the caller said. "It's fine" or "we towed a \
caravan" is not a number.
The sauna: record one only if the caller named or described it ("the largest", "the \
wood-fired four-seater"). Never choose one for them. It is a must only if they said they \
cannot take another, for example "nothing else will do" or a health reason; wanting one is \
not a must.
Heater: record the kind of heater the caller wants. When they say one kind is a problem \
for them (a headache, a cough, the smell), they want the other kind.
Names: copy the name exactly as said, keeping letters like ė, ž, š, and join spelled-out \
letters. A first name alone is recorded as said.
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
    # bookings by unit and dates only: site and car details stay out, so the model
    # can't mistake an old booking's facts for something the caller said today
    rows = data if isinstance(data, list) else [data]
    if rows and all(isinstance(r, dict) and {"unit_id", "start", "end"} <= set(r) for r in rows):
        return "bookings: " + "; ".join(
            f"{r.get('id', '')} {r['unit_id']} {r['start']} to {r['end']} {r.get('status', '')}"
            for r in rows
        )
    return json.dumps(data, ensure_ascii=False)[:300]


def caller_lines(t: Transcript) -> list[str]:
    return [it.content for it in t.items if it.type == "message" and it.role == "user"]


_OFFER_QUESTION = re.compile(r"\b(book|cancel|move|do that)\b[^?]*\?", re.IGNORECASE)


def we_offered(t: Transcript) -> bool:
    """True when our last finished line asked the caller to say yes to something."""
    for it in reversed(t.items):
        if it.type == "message" and it.role == "assistant":
            return not it.interrupted and bool(_OFFER_QUESTION.search(it.content))
    return False


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
    context = {
        "unit_ids": ids,
        "unit_names": [u.name for u in units],
        "units": list(units),
        "caller_lines": caller_lines(t),
        "latest_lines": [it.content for it in latest_caller_turn(t)],
        "offered": we_offered(t),
    }
    try:
        return CallState.model_validate(json.loads(resp.content), context=context)
    except ValueError as e:  # bad JSON, or a pydantic ValidationError
        raise UnderstandError(str(e)) from e
