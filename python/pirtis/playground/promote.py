"""Promotion: a turn a person flagged becomes a test case, and only through a person.

The case is the call as decide saw it at that turn, the world as it stood then (bookings
the call had already made or moved are set up), and what the reviewer says should have
happened: the action, the booking change, and judge statements. It is one more YAML
fixture in fixtures/extra, numbered from 60, never written over another file, and read
back by the kit's own loader before it is kept. Nothing here runs by itself (D-30).

Only seed people may enter the repo (D-39). The call must come from a seed customer's
phone or the playground's unknown number; every name given to identify must be a seed
customer's, except a stranger's, which the reviewer vouches for; and no other
phone-length number or email may appear in what the caller said or the reviewer wrote.
The form adds the reviewer's own confirmation on top of these checks.
"""

import json
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pirtis.api.memory import Memory, Seed
from pirtis.api.types import STATUS_BOOKED
from pirtis.harness.fixture import load_fixtures
from pirtis.playground.calls import UNKNOWN_PHONE, LiveCall, replay_writes, seen_at
from pirtis.transcript.types import Item

FIRST_NUMBER = 60  # promoted cases: 60-99 (30-41 and 50-56 are hand-written suites)
ACTIONS = ("say", "wait", "transfer", "end_call")
_NUMBER = re.compile(r"\+?\d(?:[\s-]?\d){6,}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class PromoteError(ValueError):
    """Why this turn can't become a case, in words for the reviewer."""


@dataclass(frozen=True)
class Expected:
    """What the reviewer says should have happened on the turn."""

    title: str
    action: str
    bookings: list[dict[str, Any]] = field(default_factory=list)  # BookingExpect fields
    judge: list[str] = field(default_factory=list)
    why: str = ""


@dataclass(frozen=True)
class Case:
    id: str
    text: str  # the fixture file, comments included


def build(call: LiveCall, step: dict[str, Any], seed: Seed, want: Expected) -> Case:
    """The fixture for one turn of a stored call, checked by the kit's loader."""
    case_id = slug(want.title)
    if want.action not in ACTIONS:
        raise PromoteError(f"The action must be one of {', '.join(ACTIONS)}.")
    judge = [j.strip() for j in want.judge if j.strip()]
    if not judge:
        raise PromoteError("Write at least one statement the reply must satisfy.")
    items = seen_at(call.t.items, step["at"])
    # names given to identify, before this turn and on it: the caller said them in the case
    given = [
        str(c["args"].get("name", ""))
        for c in step["api_calls"]
        if c["method"] == "customers.identify" and isinstance(c["args"], dict)
    ]
    seed_only(call.t.caller.phone, items, seed, [want.title, want.why, *judge], given)
    rows, ids = world_before(items, seed)

    data: dict[str, Any] = {
        "id": case_id,
        "expect": {
            "action": want.action,
            "bookings": [_expect(b, ids, seed) for b in want.bookings],
        },
    }
    if rows:
        data["setup"] = {"bookings": rows}
    data["transcript"] = {
        "call_id": f"case_{case_id}",
        "today": call.t.today,
        "now": step["at"],
        "caller": {"phone": call.t.caller.phone},
        "items": [_item(i, ids) for i in items],
    }
    data["judge"] = judge
    head = [
        f"Promoted by a reviewer from feedback on turn {step['turn_id']}",
        f"(agent {step['agent_version']}, {datetime.now(UTC):%Y-%m-%d}): {want.title}.",
    ]
    if want.why.strip():
        head.append(f"Why: {want.why.strip()}")
    text = "".join(f"# {line}\n" for line in head) + yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, width=4096
    )
    _loads(text)
    return Case(case_id, text)


def save(case: Case, folder: Path) -> Path:
    """Writes the case as the next free NN_<id>.yaml in folder, never over a file. Ids
    must be unique across the suites the gate runs: this folder and its parent."""
    taken = {f.id for d in (folder, folder.parent) for f in load_fixtures(d)}
    if case.id in taken:
        raise PromoteError(
            f'A case called "{case.id}" already exists; give this one another title.'
        )
    used = [int(p.name[:2]) for p in folder.glob("[0-9][0-9]_*.yaml")]
    n = max([FIRST_NUMBER - 1, *(u for u in used if u >= FIRST_NUMBER)]) + 1
    while n <= 99:
        path = folder / f"{n:02d}_{case.id}.yaml"
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(case.text)
            return path
        except FileExistsError:  # another reviewer took this number a moment ago
            n += 1
    raise PromoteError("Case numbers 60 to 99 are all used; renumber fixtures/extra first.")


def slug(title: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:48].strip("-")
    if not s:
        raise PromoteError("The title needs a few letters or digits.")
    return s


def seed_only(
    phone: str, items: list[Item], seed: Seed, written: list[str], given: list[str]
) -> None:
    """Refuses anything that could carry a real person into the repo (D-39)."""
    known = {_digits(c.phone) for c in seed.customers} | {_digits(UNKNOWN_PHONE)}
    if _digits(phone) not in known:
        raise PromoteError(
            "The call came from a number that isn't a seed customer's or the playground's "
            "unknown number, so it can't enter the repo."
        )
    stranger = _digits(phone) == _digits(UNKNOWN_PHONE)
    names = {_fold(c.name) for c in seed.customers}
    said = [i.content for i in items if i.type == "message" and i.role == "user"]
    earlier = [
        str(json.loads(i.arguments).get("name", ""))
        for i in items
        if i.type == "function_call" and i.name == "customers.identify"
    ]
    for name in [*earlier, *given]:
        if not stranger and _fold(name) not in names:
            raise PromoteError(
                f"The caller gave the name \"{name}\", which isn't a seed customer's, so the "
                "call can't enter the repo."
            )
    tails = {k[-8:] for k in known}
    for text in [*said, *written]:
        if _EMAIL.search(text):
            raise PromoteError("An email address appears in the call or the form; remove it.")
        for m in _NUMBER.finditer(text):
            if not _ISO_DATE.fullmatch(m.group()) and _digits(m.group())[-8:] not in tails:
                raise PromoteError(
                    f"\"{m.group()}\" looks like a phone number that isn't a seed customer's; "
                    "the call can't enter the repo."
                )


def world_before(items: list[Item], seed: Seed) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The setup rows that rebuild the bookings this call had made or moved before the
    turn, and the new ids for the ones it made. A fresh API numbers new bookings from
    bk_101, so a booking the call made is set up under a free id below 100."""
    api = Memory(seed)
    replay_writes(api, items, "promote")
    before = {b.id: b for b in seed.bookings}
    changed = [b for b in api.bookings() if before.get(b.id) != b]
    if any(b.status != STATUS_BOOKED for b in changed):
        raise PromoteError(
            "This turn comes after a cancellation in the same call, and a case can't start "
            "from a cancelled booking yet. Promote a turn before the cancellation."
        )
    taken, ids, n = set(before), {}, 99
    for b in changed:
        if b.id in before:
            continue  # a moved seed booking keeps its id
        while f"bk_{n}" in taken:
            n -= 1
        ids[b.id] = f"bk_{n}"
        taken.add(ids[b.id])
    rows = [
        {
            "id": ids.get(b.id, b.id),
            "customer": b.customer_id,
            "unit": b.unit_id,
            "start": b.start,
            "end": b.end,
            "people": b.people,
            "power": b.site.power_available,
            "tow": b.tow_capacity_kg,
            "version": b.version,
            "total": b.total,
        }
        for b in changed
    ]
    return rows, ids


def _expect(b: dict[str, Any], ids: dict[str, str], seed: Seed) -> dict[str, Any]:
    if b.get("unit") and b["unit"] not in {u.id for u in seed.units}:
        raise PromoteError(f"No sauna {b['unit']!r} in the catalogue.")
    if not (b.get("unit") or b.get("id")):
        raise PromoteError("An expected booking needs a sauna, or the id of the one that changes.")
    out = {k: v for k, v in b.items() if v not in ("", None)}
    if "id" in out:
        out["id"] = ids.get(out["id"], out["id"])
    return out


def _item(i: Item, ids: dict[str, str]) -> dict[str, Any]:
    """One transcript item as the fixtures write it: only the fields its type uses."""
    if i.type == "message" and i.role == "assistant":
        keys = ["start", "end", "content", "interrupted"]
    elif i.type == "message":
        keys = ["start", "end", "content", "is_final", "speech_final", "confidence"]
    elif i.type == "function_call":
        keys = ["call_id", "name", "arguments", "start"]
    else:
        keys = ["call_id", "output", "end"]
    out: dict[str, Any] = {"type": i.type, **({"role": i.role} if i.type == "message" else {})}
    for k in keys:
        v = getattr(i, k)
        out[k] = _renamed(v, ids) if k in ("arguments", "output") else v
    if i.type == "message" and i.role == "user" and i.prosody and i.prosody.emotions:
        out["prosody"] = {
            "emotions": [{"name": e.name, "score": e.score} for e in i.prosody.emotions]
        }
    return out


def _renamed(raw: str, ids: dict[str, str]) -> str:
    """The same JSON with every booking id this case renames replaced."""

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return ids.get(x, x) if isinstance(x, str) else x

    return json.dumps(walk(json.loads(raw)), ensure_ascii=False) if ids else raw


def _loads(text: str) -> None:
    """The kit's own loader must accept the case, or it isn't one."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "case.yaml").write_text(text, encoding="utf-8")
        try:
            load_fixtures(d)
        except ValueError as e:
            raise PromoteError(f"The case didn't load: {e}") from e


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _fold(s: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().split()
    )
