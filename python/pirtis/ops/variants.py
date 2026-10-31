"""Metamorphic variants of the given fixtures, checked by the kit's API, not by our policy.

Hidden fixtures are "the same rules, other callers, cars and dates", so we make exactly
that: each transform changes a given call the way another caller or another day would,
while the right answer stays the same or moves with it.

- persona: another seed customer with no bookings of their own calls instead (phone,
  name, spelled-out name, customer id)
- shift: the whole call happens one or two weeks later (today, every spoken and written
  date, the setup and the expectation; weekdays stay true)
- noise: the caller's lines as speech-to-text writes them (fillers, a restart, lower
  case, figures as digits)
- tow: the car tows a different figure in the same band, so the same saunas fit

The oracle is the kit itself. A variant is kept only if the kit's loader reads it, the
kit's API accepts every booking it expects (in a fresh API with its setup), and the
API's availability returns the same fitting saunas for the caller's needs as it does
for the source. What a variant's caller needs comes from the source's gold call state,
moved by the same transform. Variants are written to fixtures/variants-candidates/ with
a review table; a person moves them into fixtures/variants, which the gate runs.

    uv run python -m pirtis.ops.variants [--out ../fixtures/variants-candidates]
"""

import argparse
import hashlib
import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from pirtis.agent.understand import fold
from pirtis.agent.words import spoken_ordinal
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import ApiError, AvailabilityQuery, BookingPatch, BookingRequest, Site
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.fixture import _Loader as KitLoader  # read exactly as the harness reads
from pirtis.ops.extract_eval import GOLD_DIR

ROOT = Path(__file__).resolve().parents[3]
GIVEN = ROOT / "fixtures"
SEED = load_seed(GIVEN / "seeds" / "base.json")
NO_SHIFT = {"turnaround", "other-customers-data"}  # their judge lines lean on seed bookings
NO_PERSONA = {"identify-before-reading"}  # the caller's own seed booking is the subject
MONTHS = (
    "January February March April May June July August September October November December"
).split()  # spelled out, not strftime: another locale's month names would break parsing
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORDINALS = {spoken_ordinal(n): n for n in range(1, 32)}
_ORD = "|".join(sorted(ORDINALS, key=len, reverse=True)) + r"|\d{1,2}(?:st|nd|rd|th)"
# "Saturday the third to Sunday the fourth of October", "the sixth and seventh"
_THE = r"the(?:,?\s+(?:um|uh|er),?)?\s+"  # "the, um, sixth": a filler may sit in between
_RUN = re.compile(
    rf"\b(?:({'|'.join(WEEKDAYS)})\s+)?{_THE}({_ORD})"
    rf"((?:(?:,|\s+and|\s+to|\s+till|\s+until)\s+(?:(?:{'|'.join(WEEKDAYS)})\s+)?(?:the\s+)?(?:{_ORD}))*)"
    rf"(?:[.,]?\s+of\s+({'|'.join(MONTHS)}))?",
    re.IGNORECASE,
)
_ONE = re.compile(rf"\b(?:({'|'.join(WEEKDAYS)})\s+)?({_THE})?({_ORD})\b", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# figures as speech-to-text often writes them; and each tow figure's same-band neighbour
DIGITS = {
    "three and a half tonnes": "3.5 tonnes",
    "two and a half tonnes": "2.5 tonnes",
    "three tonnes": "3 tonnes",
    "two thousand": "2000",
    "fifteen hundred": "1500",
}
TOW_BAND = {
    "three and a half tonnes": ("three tonnes", 3000),
    "three tonnes": ("two and a half tonnes", 2500),
    "two and a half tonnes": ("two tonnes", 2000),
    "two thousand": ("two thousand five hundred", 2500),
    "fifteen hundred": ("fourteen hundred", 1400),
}
FILLERS = ["um, ", "uh, ", "so, um, ", "yeah, "]


@dataclass
class Variant:
    id: str
    source: str
    transforms: list[str]
    data: dict[str, Any]
    ask: dict[str, Any] | None  # what the caller needs, for the oracle
    verdict: str = ""  # "" kept, else why not
    notes: list[str] = field(default_factory=list)


# ---- text helpers ---------------------------------------------------------------------


def read(path: Path) -> dict[str, Any]:
    """A fixture file as the harness reads it: a fault's "on:" stays the word "on"."""
    d: dict[str, Any] = yaml.load(path.read_text(), Loader=KitLoader)
    return d


def _walk(x: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(x, dict):
        return {k: _walk(v, fn) for k, v in x.items()}
    if isinstance(x, list):
        return [_walk(v, fn) for v in x]
    return fn(x) if isinstance(x, str) else x


def _contents(d: dict[str, Any], role: str, fn: Callable[[str], str]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(d))
    for it in out["transcript"]["items"]:
        if it.get("type") == "message" and it.get("role") == role:
            it["content"] = fn(it["content"])
    return out


def _resolve(day: int, month: str | None, weekday: str | None, today: date) -> date | None:
    """Which date "the <day>" means on a call made `today`."""
    if month:
        d = date(today.year, MONTHS.index(month.capitalize()) + 1, day)
        return d if d >= today - timedelta(days=60) else date(today.year + 1, d.month, day)
    for i in range(-7, 60):
        d = today + timedelta(days=i)
        if d.day == day and (weekday is None or WEEKDAYS[d.weekday()].lower() == weekday.lower()):
            return d
    return None


def _day(word: str) -> int:
    w = word.lower()
    digits = re.match(r"\d+", w)
    return ORDINALS.get(w) or (int(digits[0]) if digits else 0)


def shift_spoken(text: str, today: date, k: int) -> str:
    """Every spoken date run moved k days. Weekdays are kept as said; a month said at the
    end of a run belongs to its last date, and one is added when a move crosses months."""

    def run(m: re.Match[str]) -> str:
        month = m.group(4)
        body = (
            re.sub(r"[.,]?\s+of\s*$", "", m.group(0)[: m.start(4) - m.start(0)])
            if month
            else m.group(0)
        )
        count = len(_ONE.findall(body))
        seen: list[tuple[date, date]] = []

        def one(mm: re.Match[str]) -> str:
            wd, the, ordw = mm.group(1), mm.group(2) or "", mm.group(3)
            last = len(seen) == count - 1
            d = _resolve(_day(ordw), month if last else None, wd, today)
            if d is None:
                seen.append((today, today))
                return mm.group(0)
            nd = d + timedelta(days=k)
            seen.append((d, nd))
            wd_out = ""
            if wd:
                name = WEEKDAYS[nd.weekday()]
                wd_out = (name.lower() if wd[0].islower() else name) + " "
            return f"{wd_out}{the}{spoken_ordinal(nd.day)}"

        moved = _ONE.sub(one, body)
        old, new = seen[-1] if seen else (today, today)
        if month or new.month != old.month:
            return f"{moved} of {MONTHS[new.month - 1]}"
        return moved

    return _RUN.sub(run, text)


def _shift_iso(text: str, k: int) -> str:
    return _ISO.sub(
        lambda m: (date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=k)).isoformat(), text
    )


# ---- transforms -----------------------------------------------------------------------


def shift(d: dict[str, Any], k: int) -> dict[str, Any] | None:
    """The same call k days later. None if a seed booking appears in it: the seed's
    bookings stay where they are, so the call would no longer match the world."""
    raw = json.dumps(d, ensure_ascii=False)
    if any(b.id in raw for b in SEED.bookings):
        return None
    today = date.fromisoformat(d["transcript"]["today"])
    out: dict[str, Any] = _walk(d, lambda s: _shift_iso(s, k))
    for role in ("user", "assistant"):
        out = _contents(out, role, lambda s: shift_spoken(s, today, k))
    out["judge"] = [shift_spoken(j, today, k) for j in out.get("judge") or []]
    return out


def persona(d: dict[str, Any], to_id: str) -> dict[str, Any] | None:
    """The same call from another seed customer. None if that customer is already in it."""
    phone = d["transcript"]["caller"]["phone"]
    src = next((c for c in SEED.customers if c.phone == phone), None)
    dst = next(c for c in SEED.customers if c.id == to_id)
    raw = json.dumps(d, ensure_ascii=False)
    if src is None or src.id == dst.id or dst.name.split()[0] in raw:
        return None
    first, last = src.name.split()[0], src.name.split()[-1]
    nfirst, nlast = dst.name.split()[0], dst.name.split()[-1]
    spelled = " ".join(fold(last).upper())

    def swap(s: str) -> str:
        s = s.replace(src.name, dst.name).replace(spelled, " ".join(nlast.upper()))
        s = re.sub(rf"\b{re.escape(last)}\b", nlast, s)
        s = re.sub(rf"\b{re.escape(first)}\b", nfirst, s)
        return (
            s.replace(src.phone, dst.phone)
            .replace(f'"{src.id}"', f'"{dst.id}"')
            .replace(src.id, dst.id)
        )

    swapped: dict[str, Any] = _walk(d, swap)
    return swapped


def noise(d: dict[str, Any]) -> dict[str, Any]:
    """The caller's lines as speech-to-text writes them, chosen by the fixture's id so a
    run is repeatable: a filler, one restart, lower case outside names, digits."""
    names = [p for c in SEED.customers for p in c.name.split()] + [u.name for u in SEED.units]
    h = int(hashlib.sha256(d["id"].encode()).hexdigest(), 16)
    state = {"i": 0}

    def rough(s: str) -> str:
        i = state["i"]
        state["i"] += 1
        for spoken, digits in DIGITS.items():
            s = re.sub(rf"\b{spoken}\b", digits, s, flags=re.IGNORECASE)
        kept = {n: f"\x00{j}\x00" for j, n in enumerate(names)}
        for n, mark in kept.items():
            s = s.replace(n, mark)
        s = s.lower()
        for n, mark in kept.items():
            s = s.replace(mark, n)
        words = s.split()
        if i == 1 and len(words) > 3:  # one restart, on the second line: their own words again
            s = " ".join(w.strip(".,?!") for w in words[:2]) + ", " + s
        if (h + i) % 2 == 0:
            s = FILLERS[(h + i) % len(FILLERS)] + s
        return s

    return _contents(d, "user", rough)


def tow(d: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
    """The car tows a different figure in the same band. None if no figure we know."""
    lines = " ".join(
        it["content"] for it in d["transcript"]["items"] if it.get("role") == "user"
    ).lower()
    for said, (other, kg) in TOW_BAND.items():
        if re.search(rf"\b{said}\b", lines):
            pattern = re.compile(rf"\b{said}\b", re.IGNORECASE)

            def swap(s: str, p: re.Pattern[str] = pattern, o: str = other) -> str:
                return p.sub(o, s)

            moved = _contents(d, "user", swap)
            return moved, kg
    return None


# ---- the oracle -----------------------------------------------------------------------


def _fixture(data: dict[str, Any]) -> Fixture:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "v.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )
        [f] = load_fixtures(tmp)
    return replace(f, dir=GIVEN)


def _world(f: Fixture) -> Memory:
    seed = load_seed(GIVEN / "seeds" / "base.json")
    seed.bookings.extend(sb.booking() for sb in f.setup.bookings)
    return Memory(seed)


def _fitting(f: Fixture, ask: dict[str, Any]) -> set[str] | None:
    if not (ask.get("start") and ask.get("people") and ask.get("tow_kg")):
        return None
    start, end = ask["start"], ask.get("end") or ask["start"]
    q = AvailabilityQuery(start, end, ask["people"], ask["tow_kg"], ask.get("power"))
    n = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return {
        a.unit_id
        for a in _world(f).availability(q)
        if sum((date.fromisoformat(r.end) - date.fromisoformat(r.start)).days + 1 for r in a.free)
        == n
    }


def oracle(src: Fixture, src_ask: dict[str, Any] | None, v: Variant) -> str:
    """ "" when the kit agrees the variant is sound; otherwise why not."""
    try:
        f = _fixture(v.data)
    except (ValueError, TypeError) as e:
        return f"the kit can't load it: {e}"
    customer = next((c.id for c in SEED.customers if c.phone == f.transcript.caller.phone), "")
    api = _world(f)
    for b in f.expect.bookings:
        try:
            if b.id:
                cur = next(x for x in api.bookings() if x.id == b.id)
                patch = BookingPatch(b.start or None, b.end or None, status=b.status or None)
                api.update_booking(b.id, cur.version, patch)
            else:
                people = b.people or (v.ask or {}).get("people") or 1
                api.create_booking(
                    BookingRequest(customer, b.unit, b.start, b.end, people, Site(True), 10_000)
                )
        except ApiError as e:
            return f"the API refuses the booking it expects: {e.code}"
    if src_ask is not None and v.ask is not None:
        before, after = _fitting(src, src_ask), _fitting(f, v.ask)
        if before != after:
            return f"the saunas that fit change: {sorted(before or [])} -> {sorted(after or [])}"
    return ""


# ---- generation -----------------------------------------------------------------------


def _gold(fid: str) -> dict[str, Any] | None:
    p = GOLD_DIR / f"{fid}.json"
    return json.loads(p.read_text())["state"] if p.exists() else None


def generate() -> tuple[list[Variant], dict[str, Fixture], dict[str, dict[str, Any] | None]]:
    sources = {f.id: f for f in load_fixtures(GIVEN)}
    asks = {fid: _gold(fid) for fid in sources}
    out: list[Variant] = []
    for path in sorted(GIVEN.glob("[0-9][0-9]_*.yaml")):
        d = read(path)
        fid, ask = d["id"], asks.get(d["id"])

        def add(
            tag: str, data: dict[str, Any] | None, new_ask: dict[str, Any] | None, fid: str = fid
        ) -> None:
            if data is None:
                return
            vid = f"{fid}--{tag}"
            data = {**data, "id": vid, "variant_of": fid, "transforms": tag.split("+")}
            data["transcript"]["call_id"] = f"var_{vid}"
            out.append(Variant(vid, fid, tag.split("+"), data, new_ask))

        if fid not in NO_SHIFT:
            for k in (7, 14):
                moved = None
                if ask:
                    moved = {
                        **ask,
                        **{f: _shift_iso(ask[f], k) for f in ("start", "end") if ask.get(f)},
                    }
                add(f"shift{k}", shift(d, k), moved)
        if fid not in NO_PERSONA:
            for to in ("cus_12", "cus_19"):
                add(f"persona-{to}", persona(d, to), ask)
        add("noise", noise(d), ask)
        moved_tow = tow(d)
        if moved_tow:
            add("tow", moved_tow[0], {**ask, "tow_kg": moved_tow[1]} if ask else None)
    for v in out:
        v.verdict = oracle(sources[v.source], asks[v.source], v)
    return out, sources, asks


def write(variants: list[Variant], folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.yaml"):
        old.unlink()
    seeds = folder / "seeds"
    if not seeds.exists():
        seeds.symlink_to(Path("..") / "seeds")
    kept = [v for v in variants if not v.verdict]
    paths = []
    for i, v in enumerate(kept):
        p = folder / f"{i:02d}_{v.id.replace('--', '__').replace('-', '_')}.yaml"
        how = ", ".join(v.transforms)
        head = f"# variant of {v.source}: {how}. Generated; checked by the kit's API.\n"
        p.write_text(
            head + yaml.safe_dump(v.data, sort_keys=False, allow_unicode=True, width=4096),
            encoding="utf-8",
        )
        paths.append(p)
    rows = ["| Variant | Of | Transforms | Expect | Oracle |", "|---|---|---|---|---|"]
    for v in variants:
        e = v.data["expect"]
        books = (
            "; ".join(" ".join(f"{k}={x}" for k, x in b.items()) for b in e.get("bookings") or [])
            or "no change"
        )
        rows.append(
            f"| {v.id} | {v.source} | {', '.join(v.transforms)} | {e['action']}, {books} | "
            f"{'kept' if not v.verdict else 'dropped: ' + v.verdict} |"
        )
    (folder / "REVIEW.md").write_text(
        "# Variant candidates: review before they join the gate\n\n"
        "Generated by `python -m pirtis.ops.variants`; each kept one passed the kit's API "
        "(the loader, every expected booking, the same fitting saunas). Skim the table, read any "
        "you doubt, then `make variants-accept` moves them into `fixtures/variants`, which "
        "`make gate` runs.\n\n"
        f"Kept {len(kept)} of {len(variants)}.\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return paths


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="variants", description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(ROOT / "fixtures" / "variants-candidates"))
    args = p.parse_args(argv)
    variants, _, _ = generate()
    paths = write(variants, Path(args.out))
    dropped = [v for v in variants if v.verdict]
    print(f"{len(paths)} variants kept of {len(variants)}, written to {args.out}")
    for v in dropped:
        print(f"  dropped {v.id}: {v.verdict}")


if __name__ == "__main__":
    main()
