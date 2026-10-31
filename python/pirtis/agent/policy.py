"""Policy: every decision about what happens next, made in code.

next_step reads the transcript and the CallState and returns one Decision. It is the
only place that calls the API on the caller's behalf and the only place that writes,
and it writes only when three things agree: the caller said yes, what they said yes to
is exactly what we read back to them, and the rules mirror finds nothing wrong.
"""

import json
import re
from dataclasses import dataclass
from datetime import date

from pirtis.agent.rules import DAY, days, free_days, violations, without_booking
from pirtis.agent.understand import CallState, caller_lines, fold
from pirtis.agent.words import spoken_number, spoken_ordinal
from pirtis.api.client import METHOD_CUSTOMERS_IDENTIFY
from pirtis.api.client import Client as ApiClient
from pirtis.api.types import (
    CODE_UNAVAILABLE,
    CODE_VERSION_MISMATCH,
    STATUS_BOOKED,
    STATUS_CANCELLED,
    ApiError,
    AvailabilityQuery,
    Booking,
    BookingPatch,
    BookingQuery,
    BookingRequest,
    Site,
    Unit,
)
from pirtis.transcript.types import Transcript

# ---- decisions: what speech renders ------------------------------------------------


@dataclass(frozen=True)
class Ask:
    """Ask for one missing detail: name, dates, people, tow, power, which_booking or
    new_dates."""

    slot: str


@dataclass(frozen=True)
class Offer:
    """Offer one unit on one date range and ask for a yes.

    instead_of/why_not: the unit the caller asked for, and why it cannot work.
    weekend_min: the range was stretched to the two-day weekend minimum.
    blocked_day: the offered unit isn't free on this requested day, so the dates moved.
    """

    unit: Unit
    start: str
    end: str
    people: int
    total: int
    instead_of: Unit | None = None
    why_not: str = ""  # "capacity" | "weight" | "power" | "taken"
    weekend_min: bool = False
    blocked_day: str | None = None


@dataclass(frozen=True)
class Booked:
    booking: Booking
    unit: Unit


@dataclass(frozen=True)
class OwnClash:
    """The caller already has a booking on the dates they asked for."""

    booking: Booking
    unit: Unit


@dataclass(frozen=True)
class Refuse:
    """Other customers' details: never shared. unit lets us offer other dates for it."""

    unit: Unit | None = None


@dataclass(frozen=True)
class ConfirmChange:
    booking: Booking
    unit: Unit
    start: str
    end: str


@dataclass(frozen=True)
class Moved:
    booking: Booking
    unit: Unit


@dataclass(frozen=True)
class ConfirmCancel:
    booking: Booking
    unit: Unit


@dataclass(frozen=True)
class Cancelled:
    booking: Booking
    unit: Unit


@dataclass(frozen=True)
class BookingsRead:
    bookings: tuple[tuple[Booking, Unit], ...]


@dataclass(frozen=True)
class Price:
    """A price from the catalogue: for one unit and dates, one unit a day, or the range."""

    unit: Unit | None
    start: str | None
    end: str | None
    total: int
    units: tuple[Unit, ...]


@dataclass(frozen=True)
class Goodbye:
    pass


@dataclass(frozen=True)
class Hold:
    pass


@dataclass(frozen=True)
class Reply:
    text: str


@dataclass(frozen=True)
class Sorry:
    """We didn't understand; ask the caller to say it again."""


@dataclass(frozen=True)
class Handoff:
    """Pass the call to a person in the named queue, with what we already know so the
    caller doesn't have to repeat it."""

    queue: str
    reason: str
    summary: str = ""
    apologise: bool = False  # the caller is upset with us, not just asking


@dataclass(frozen=True)
class NoFit:
    """Nothing can be offered. limit is the catalogue figure that rules it out."""

    reason: str  # "too_many" | "too_light" | "nothing_fits" | "no_dates" | "no_booking"
    limit: int = 0


Decision = (
    Ask | Offer | Booked | OwnClash | Refuse | ConfirmChange | Moved | ConfirmCancel
    | Cancelled | BookingsRead | Price | Goodbye | Hold | Reply | Sorry | Handoff | NoFit
)  # fmt: skip


# ---- the turn's decision -------------------------------------------------------------


def next_step(t: Transcript, s: CallState, api: ApiClient, units: list[Unit]) -> Decision:
    """The one next thing to do, given the call so far."""
    today = date.fromisoformat(t.today)
    by_id = {u.id: u for u in units}

    # things we can do for anyone, identified or not; none of them touches customer data
    match s.intent:
        case "human":
            return Handoff("front_desk", "caller asked for a person", summary(s, t))
        case "goodbye":
            return Goodbye()
        case "hold":
            return Hold()
        case "price":
            return price(s, units)
        case "other_customer_info":
            return Refuse(by_id.get(s.unit or ""))
        case "unclear":
            return twice_misunderstood(t, s) if asked_again(t) else Sorry()
        case "smalltalk":
            answer = safe_answer(s.answer)
            return Reply(answer) if answer else Sorry()

    customer = identified_customer(t)
    if not customer:
        name = normalise_name(s.full_name, caller_lines(t))
        if not is_full_name(name):
            return Ask("name")
        found = api.identify_customer(t.caller.phone, name or "")
        if not found.ok:
            reason = "no customer matches this phone and name"
            return Handoff("new_customers", reason, summary(s, t))
        customer = found.customer_id

    match s.intent:
        case "check_booking":
            own = upcoming(own_bookings(api, customer), today)
            return BookingsRead(tuple((b, by_id[b.unit_id]) for b in own))
        case "cancel":
            return cancel(s, api, customer, by_id, today)
        case "change":
            return change(s, api, customer, by_id, today)
    return book(t, s, api, customer, units, today)


def book(
    t: Transcript, s: CallState, api: ApiClient, customer: str, units: list[Unit], today: date
) -> Decision:
    if s.start is None:
        return Ask("dates")
    if s.people is None:
        return Ask("people")
    if s.tow_kg is None:
        return Ask("tow")
    start = date.fromisoformat(s.start)
    end = date.fromisoformat(s.end) if s.end else start
    if end < start or start < today:
        return Ask("dates")

    own = own_bookings(api, customer)
    clash = next((b for b in own if b.status == STATUS_BOOKED and overlaps(b, start, end)), None)
    if clash is not None:
        return OwnClash(clash, next(u for u in units if u.id == clash.unit_id))

    r = Request(
        people=s.people,
        tow_kg=s.tow_kg,
        power=s.power,
        start=start,
        end=end,
        unit=s.unit,
        must=s.unit_is_must,
        heater=s.heater_pref,
        heater_hard=s.heater_pref is not None and (s.unit_is_must or bool(s.heater_reason)),
    )
    free = search(api, r, today)
    choice = choose_offer(r, units, free, own, today)
    if isinstance(choice, NoFit):
        return choice
    if choice.unit.needs_power and s.power is None:
        return Ask("power")

    heard = last_offer(t, units)
    agreed = (
        s.accepted_offer
        and heard is not None
        and (heard.unit_id, heard.start, heard.end) == (choice.unit.id, choice.start, choice.end)
        and heard.people in (None, s.people)
    )
    if not agreed:
        return choice
    return create(api, customer, choice, r, units, free, own, today)


def create(
    api: ApiClient,
    customer: str,
    offer: Offer,
    r: "Request",
    units: list[Unit],
    free: dict[str, set[date]],
    own: list[Booking],
    today: date,
) -> Decision:
    """Books the agreed offer. A pre-flight run of the rules first; then, on a 409 the
    slot is checked again and booked if still free; on a 500 we look whether the booking
    landed before trying once more."""
    unit = offer.unit
    req = BookingRequest(
        customer_id=customer,
        unit_id=unit.id,
        start=offer.start,
        end=offer.end,
        people=offer.people,
        site=Site(power_available=bool(r.power)),
        tow_capacity_kg=r.tow_kg,
    )
    if violations(req, unit, free.get(unit.id, set()), own):
        return offer  # something changed since we offered; offer again, never write
    for attempt in (1, 2):
        try:
            return Booked(api.create_booking(req), unit)
        except ApiError as e:
            if e.status == 409 and e.code == CODE_UNAVAILABLE:
                free = search(api, r, today)
                if attempt == 1 and not violations(req, unit, free.get(unit.id, set()), own):
                    continue
                return choose_offer(r, units, free, own, today)
            if e.status >= 500:
                landed = next(
                    (
                        b
                        for b in own_bookings(api, customer)
                        if (b.unit_id, b.start, b.end, b.status)
                        == (unit.id, offer.start, offer.end, STATUS_BOOKED)
                    ),
                    None,
                )
                if landed is not None:
                    return Booked(landed, unit)
                if attempt == 1:
                    continue
            raise
    raise AssertionError("unreachable")


def change(
    s: CallState, api: ApiClient, customer: str, by_id: dict[str, Unit], today: date
) -> Decision:
    own = own_bookings(api, customer)
    target = pick_target(upcoming(own, today), s)
    if target is None:
        return Ask("which_booking") if upcoming(own, today) else NoFit("no_booking")
    old_start, old_end = date.fromisoformat(target.start), date.fromisoformat(target.end)
    if s.shift_days:
        start, end = old_start + s.shift_days * DAY, old_end + s.shift_days * DAY
    elif s.start is not None and s.start != target.start:
        start = date.fromisoformat(s.start)
        end = date.fromisoformat(s.end) if s.end else start + (old_end - old_start)
    else:
        return Ask("new_dates")
    if start < today or end < start:
        return Ask("new_dates")
    start, end = apply_weekend_min(start, end)

    unit = by_id[target.unit_id]
    q = AvailabilityQuery(from_=(start - 3 * DAY).isoformat(), to=(end + 3 * DAY).isoformat())
    free = without_booking(
        free_days(next((a.free for a in api.availability(q) if a.unit_id == unit.id), [])),
        target,
    )
    req = BookingRequest(
        customer, unit.id, start.isoformat(), end.isoformat(), target.people, target.site,
        target.tow_capacity_kg,
    )  # fmt: skip
    if violations(req, unit, free, own, exclude_id=target.id):
        return NoFit("no_dates")
    if not (s.go_ahead or s.accepted_offer):
        return ConfirmChange(target, unit, req.start, req.end)
    moved = update_with_retry(api, target, BookingPatch(start=req.start, end=req.end), customer)
    return Moved(moved, unit)


def cancel(
    s: CallState, api: ApiClient, customer: str, by_id: dict[str, Unit], today: date
) -> Decision:
    own = upcoming(own_bookings(api, customer), today)
    target = pick_target(own, s)
    if target is None:
        return Ask("which_booking") if own else NoFit("no_booking")
    unit = by_id[target.unit_id]
    if not (s.go_ahead or s.accepted_offer):
        return ConfirmCancel(target, unit)
    done = update_with_retry(api, target, BookingPatch(status=STATUS_CANCELLED), customer)
    return Cancelled(done, unit)


def update_with_retry(api: ApiClient, b: Booking, patch: BookingPatch, customer: str) -> Booking:
    """One retry after a stale version: fetch the booking again and use its version."""
    try:
        return api.update_booking(b.id, b.version, patch)
    except ApiError as e:
        if e.code != CODE_VERSION_MISMATCH:
            raise
    fresh = next(x for x in own_bookings(api, customer) if x.id == b.id)
    return api.update_booking(fresh.id, fresh.version, patch)


# ---- the offer engine -----------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """What the caller asked for, as the offer engine needs it."""

    people: int
    tow_kg: int
    power: bool | None
    start: date
    end: date
    unit: str | None = None
    must: bool = False
    heater: str | None = None
    heater_hard: bool = False  # filter by heater only for a stated reason or a must-have


def search(api: ApiClient, r: Request, today: date) -> dict[str, set[date]]:
    """One availability call with every filter we know, over a window wide enough for
    the date shifts we may offer. Returns each fitting unit's free days."""
    q = AvailabilityQuery(
        from_=max(today, r.start - 7 * DAY).isoformat(),
        to=(r.end + 14 * DAY).isoformat(),
        people=r.people,
        max_tow_kg=r.tow_kg,
        power_available=r.power,
    )
    return {a.unit_id: free_days(a.free) for a in api.availability(q)}


def apply_weekend_min(start: date, end: date) -> tuple[date, date]:
    """A single day on a Saturday or Sunday becomes two days, extended forward."""
    if start == end and start.weekday() >= 5:
        return start, start + DAY
    return start, end


def hard_limit(u: Unit, r: Request) -> str:
    """Why this unit can never work for this caller, whatever the dates, or ""."""
    if u.capacity < r.people:
        return "capacity"
    if u.gross_weight_kg > r.tow_kg:
        return "weight"
    if u.needs_power and r.power is False:
        return "power"
    return ""


def choose_offer(
    r: Request,
    units: list[Unit],
    free: dict[str, set[date]],
    own: list[Booking],
    today: date,
) -> Offer | NoFit:
    """The best legal offer for the request, or why there is none.

    The caller's unit if it can work, on their dates; if its dates are taken, moved in
    time when they insist on it or nothing else fits; otherwise the smallest unit that
    fits, on their dates; otherwise the nearest dates that work.
    """
    biggest = max(u.capacity for u in units)
    lightest = min(u.gross_weight_kg for u in units)
    if r.people > biggest:
        return NoFit("too_many", biggest)
    if r.tow_kg < lightest:
        return NoFit("too_light", lightest)

    fitting = [u for u in units if not hard_limit(u, r)]
    if r.heater_hard:
        fitting = [u for u in fitting if u.heater == r.heater]
    if not fitting:
        return NoFit("nothing_fits")
    fitting.sort(key=lambda u: (r.heater is not None and u.heater != r.heater, u.capacity))

    start, end = apply_weekend_min(r.start, r.end)
    stretched = (start, end) != (r.start, r.end)
    wanted = next((u for u in units if u.id == r.unit), None)

    def ok(u: Unit, s: date, e: date) -> bool:
        return not violations(_request(u, r, s, e), u, free.get(u.id, set()), own)

    def offer(
        u: Unit,
        s: date,
        e: date,
        instead_of: Unit | None = None,
        why_not: str = "",
        weekend_min: bool = False,
        blocked_day: str | None = None,
    ) -> Offer:
        total = u.price_per_day * len(days(s, e))
        return Offer(
            u, s.isoformat(), e.isoformat(), r.people, total, instead_of, why_not, weekend_min,
            blocked_day,
        )  # fmt: skip

    def window(u: Unit) -> tuple[date, date] | None:
        return find_window(u, r, free, own, today)

    if wanted is not None and wanted in fitting:
        if ok(wanted, start, end):
            return offer(wanted, start, end, weekend_min=stretched)
        others = [u for u in fitting if u != wanted and ok(u, start, end)]
        if r.must or not others:
            moved = window(wanted)
            if moved is not None:
                return offer(wanted, *moved, blocked_day=_first_blocked(wanted, start, end, free))
        if others:
            return offer(others[0], start, end, instead_of=wanted, why_not="taken")
    else:
        why = hard_limit(wanted, r) if wanted is not None else ""
        for u in fitting:
            if ok(u, start, end):
                return offer(
                    u, start, end, instead_of=wanted, why_not=why or ("taken" if wanted else ""),
                    weekend_min=stretched and wanted is None,
                )  # fmt: skip

    # nothing fits on the dates asked: the nearest dates that work, for any fitting unit
    best: tuple[Unit, tuple[date, date]] | None = None
    for u in fitting:
        moved = window(u)
        if moved and (best is None or abs(moved[0] - r.start) < abs(best[1][0] - r.start)):
            best = (u, moved)
    if best is None:
        return NoFit("no_dates")
    u, (s, e) = best
    return offer(u, s, e, blocked_day=_first_blocked(u, start, end, free))


def find_window(
    u: Unit,
    r: Request,
    free: dict[str, set[date]],
    own: list[Booking],
    today: date,
    max_offset: int = 7,
) -> tuple[date, date] | None:
    """The nearest range of the same length that this unit can take, trying the asked
    start, then a day later, a day earlier, two days later, and so on; never in the
    past. The weekend minimum is applied to every candidate."""
    length = r.end - r.start
    for offset in [0] + [k * sign for k in range(1, max_offset + 1) for sign in (1, -1)]:
        s = r.start + offset * DAY
        if s < today:
            continue
        s, e = apply_weekend_min(s, s + length)
        if not violations(_request(u, r, s, e), u, free.get(u.id, set()), own):
            return s, e
    return None


def _request(u: Unit, r: Request, s: date, e: date) -> BookingRequest:
    # power unknown counts as available here; we ask before offering an electric unit
    return BookingRequest("", u.id, s.isoformat(), e.isoformat(), r.people,
                          Site(power_available=r.power is not False), r.tow_kg)  # fmt: skip


def _first_blocked(u: Unit, start: date, end: date, free: dict[str, set[date]]) -> str | None:
    return next((d.isoformat() for d in days(start, end) if d not in free.get(u.id, set())), None)


# ---- reading back what we offered -----------------------------------------------------


@dataclass(frozen=True)
class HeardOffer:
    """What our last offer said, read back from the words the caller heard."""

    unit_id: str
    start: str
    end: str
    people: int | None


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]  # fmt: skip
_ORDINAL_DAY = {spoken_ordinal(n): n for n in range(1, 32)}
_COUNT = {spoken_number(n): n for n in range(1, 21)}
_DATE = re.compile(
    r"(?:\b("
    + "|".join(_WEEKDAYS)
    + r")\s+)?\bthe\s+("
    + "|".join(sorted(_ORDINAL_DAY, key=len, reverse=True))
    + r"|\d{1,2}(?:st|nd|rd|th))\b(?:\s+of\s+("
    + "|".join(_MONTHS)
    + "))?"
)
_PEOPLE = re.compile(r"\b([a-z]+(?:-[a-z]+)?|\d+)\s+(?:people|of you)\b")


def last_offer(t: Transcript, units: list[Unit]) -> HeardOffer | None:
    """The offer in our last finished line: the last unit named, the date range, the
    number of people, and a question about booking. None if that line was no offer or
    the caller cut it off."""
    line = _last_line_we_finished(t)
    if line is None:
        return None
    text = fold(line).lower()
    if not re.search(r"\bbook\b", text) or "?" not in text:
        return None
    named = [(text.rfind(fold(u.name).lower()), u) for u in units]
    named = [(pos, u) for pos, u in named if pos >= 0]
    if not named:
        return None
    unit = max(named, key=lambda p: p[0])[1]

    found = list(_DATE.finditer(text))
    if not found:
        return None
    first = last = found[-1]
    for a, b in zip(found, found[1:], strict=False):
        if text[a.end() : b.start()].strip(" ,") == "to":
            first, last = a, b
    today = date.fromisoformat(t.today)
    end = _resolve(last, today, after=today)
    start = _resolve(first, today, before=end) if end else None
    if start is None or end is None:
        return None
    people = _PEOPLE.search(text)
    count = None
    if people:
        word = people.group(1)
        count = int(word) if word.isdigit() else _COUNT.get(word)
    return HeardOffer(unit.id, start.isoformat(), end.isoformat(), count)


def _last_line_we_finished(t: Transcript) -> str | None:
    for it in reversed(t.items):
        if it.type == "message" and it.role == "assistant":
            return None if it.interrupted else it.content
    return None


def _resolve(
    m: re.Match[str], today: date, after: date | None = None, before: date | None = None
) -> date | None:
    """The date a spoken "Tuesday the sixth (of October)" means: the first one on or
    after `after`, or the last one on or before `before`."""
    weekday, ordinal, month = m.group(1), m.group(2), m.group(3)
    day = _ORDINAL_DAY.get(ordinal) or int(re.sub(r"\D", "", ordinal))
    if before is not None:
        candidates = [before - i * DAY for i in range(0, 62)]
    else:
        candidates = [(after or today) + i * DAY for i in range(0, 366)]
    for d in candidates:
        if (
            d.day == day
            and (weekday is None or d.weekday() == _WEEKDAYS.index(weekday))
            and (month is None or d.month == _MONTHS.index(month) + 1)
        ):
            return d
    return None


# ---- small helpers ---------------------------------------------------------------------


def identified_customer(t: Transcript) -> str:
    """The customer id a successful identify earlier in the call returned, or ""."""
    names: dict[str, str] = {}
    found = ""
    for it in t.items:
        if it.type == "function_call":
            names[it.call_id] = it.name
            continue
        if it.type == "function_call_output" and names.get(it.call_id) == METHOD_CUSTOMERS_IDENTIFY:
            try:
                out = json.loads(it.output)
            except ValueError:
                continue
            if isinstance(out, dict) and out.get("ok") and out.get("customer_id"):
                found = str(out["customer_id"])
    return found


_HONORIFICS = {"mr", "mrs", "ms", "miss", "mister", "dr"}


def normalise_name(name: str | None, lines: list[str]) -> str | None:
    """The name as the API should see it: no single letters (a spelled-out name), no
    honorifics, and the caller's own spelling where the model dropped a diacritic."""
    if not name:
        return None
    said = {fold(w).lower(): w for line in lines for w in re.findall(r"[^\W\d_]+", line)}
    words = []
    for w in re.findall(r"[^\W\d_]+", name):
        if len(w) < 2 or w.lower() in _HONORIFICS:
            continue
        theirs = said.get(fold(w).lower())
        words.append(theirs if theirs and theirs != fold(theirs) and w == fold(w) else w)
    return " ".join(words) or None


def is_full_name(name: str | None) -> bool:
    """At least two different words: the API will not match a first name alone."""
    return name is not None and len(set(re.findall(r"[^\W\d_]+", name.lower()))) >= 2


def own_bookings(api: ApiClient, customer: str) -> list[Booking]:
    """The caller's own bookings. Always filtered by their id: we never list anyone
    else's."""
    return api.list_bookings(BookingQuery(customer_id=customer))


def upcoming(own: list[Booking], today: date) -> list[Booking]:
    return [b for b in own if b.status == STATUS_BOOKED and date.fromisoformat(b.end) >= today]


def pick_target(own: list[Booking], s: CallState) -> Booking | None:
    """Which of the caller's bookings they mean: by its first day, by the unit, or the
    only one they have."""
    if s.change_target_start:
        hit = [b for b in own if b.start == s.change_target_start]
        if hit:
            return hit[0]
    if s.unit:
        hit = [b for b in own if b.unit_id == s.unit]
        if len(hit) == 1:
            return hit[0]
    return own[0] if len(own) == 1 else None


def overlaps(b: Booking, start: date, end: date) -> bool:
    return date.fromisoformat(b.start) <= end and start <= date.fromisoformat(b.end)


def price(s: CallState, units: list[Unit]) -> Price:
    unit = next((u for u in units if u.id == s.unit), None)
    if unit is not None and s.start:
        end = s.end or s.start
        total = unit.price_per_day * len(days(date.fromisoformat(s.start), date.fromisoformat(end)))
        return Price(unit, s.start, end, total, tuple(units))
    return Price(unit, None, None, 0, tuple(units))


_ASKED_AGAIN = re.compile(r"\bsay (it|that) again\b", re.IGNORECASE)


def asked_again(t: Transcript) -> bool:
    """True when our last line already asked the caller to repeat themselves."""
    last = next(
        (it for it in reversed(t.items) if it.type == "message" and it.role == "assistant"), None
    )
    return last is not None and bool(_ASKED_AGAIN.search(last.content))


def twice_misunderstood(t: Transcript, s: CallState) -> Handoff:
    return Handoff("front_desk", "two misunderstandings in a row", summary(s, t))


_BOOKING_WORDS = re.compile(
    r"\b(book(ed|ing)?|reserv\w*|confirm\w*|still on|cancel\w*|availab\w*|free)\b", re.IGNORECASE
)


def safe_answer(answer: str | None) -> str | None:
    """A free answer from the model, but only if it says nothing about bookings or
    availability, and at most two sentences."""
    if not answer or _BOOKING_WORDS.search(answer):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", answer.strip())
    return " ".join(sentences[:2])


def summary(s: CallState, t: Transcript) -> str:
    """What a colleague needs to know when we hand the call over."""
    who = identified_customer(t)
    parts = [f"customer {who}" if who else "not identified", s.gist]
    if s.people:
        parts.append(f"{s.people} people")
    if s.start:
        parts.append(f"{s.start} to {s.end or s.start}")
    if s.tow_kg:
        parts.append(f"tows {s.tow_kg} kg")
    if s.power is not None:
        parts.append("mains power" if s.power else "no mains power")
    return "; ".join(p for p in parts if p)
