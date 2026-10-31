"""Policy: every decision about what happens next, made in code.

next_step reads the transcript and the CallState and returns one Decision. It is the
only place that calls the API on the caller's behalf, and the only place that writes.
"""

import json
import re
from dataclasses import dataclass
from datetime import date

from pirtis.agent.understand import CallState, fold
from pirtis.api.client import METHOD_CUSTOMERS_IDENTIFY
from pirtis.api.client import Client as ApiClient
from pirtis.api.types import AvailabilityQuery, Booking, BookingRequest, DateRange, Site, Unit
from pirtis.transcript.types import Transcript


@dataclass(frozen=True)
class Ask:
    """Ask the caller for one missing detail: name, dates, people, tow or power."""

    slot: str


@dataclass(frozen=True)
class Offer:
    """Offer one unit on one date range and ask for a yes. instead_of is the unit the
    caller asked for when it cannot work, and why_not says why."""

    unit: Unit
    start: str
    end: str
    people: int
    total: int
    instead_of: Unit | None = None
    why_not: str = ""  # "capacity" | "weight" | "power" | "taken"


@dataclass(frozen=True)
class Booked:
    booking: Booking
    unit: Unit


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
    reason: str


Decision = Ask | Offer | Booked | Handoff | NoFit


def next_step(t: Transcript, s: CallState, api: ApiClient, units: list[Unit]) -> Decision:
    """The one next thing to do, given the call so far."""
    customer = identified_customer(t)
    if not customer:
        if not is_full_name(s.full_name):
            return Ask("name")
        found = api.identify_customer(t.caller.phone, s.full_name or "")
        if not found.ok:
            return Handoff("new_customers", "no customer matches this phone and name")
        customer = found.customer_id

    if s.start is None:
        return Ask("dates")
    if s.people is None:
        return Ask("people")
    if s.tow_kg is None:
        return Ask("tow")
    start, end = s.start, s.end or s.start

    # every filter we know goes into the query, so only units that can work come back
    by_id = {u.id: u for u in units}
    free = api.availability(
        AvailabilityQuery(
            from_=start, to=end, people=s.people, max_tow_kg=s.tow_kg, power_available=s.power
        )
    )
    fits = [by_id[a.unit_id] for a in free if covers(a.free, start, end)]
    if not fits:
        return NoFit("no unit fits those details on those dates")

    wanted = by_id.get(s.unit) if s.unit else None
    unit = wanted if wanted in fits else min(fits, key=lambda u: (u.capacity, u.price_per_day))
    if unit.needs_power and s.power is None:
        return Ask("power")

    if s.accepted_offer and was_offered(t, unit):
        booking = api.create_booking(
            BookingRequest(
                customer_id=customer,
                unit_id=unit.id,
                start=start,
                end=end,
                people=s.people,
                site=Site(power_available=bool(s.power)),
                tow_capacity_kg=s.tow_kg,
            )
        )
        return Booked(booking, unit)

    instead_of = wanted if wanted is not None and wanted != unit else None
    return Offer(
        unit=unit,
        start=start,
        end=end,
        people=s.people,
        total=unit.price_per_day * days(start, end),
        instead_of=instead_of,
        why_not=why_not(instead_of, s) if instead_of else "",
    )


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


def is_full_name(name: str | None) -> bool:
    """At least two different words: the API will not match a first name alone."""
    return name is not None and len(set(re.findall(r"[^\W\d_]+", name.lower()))) >= 2


def covers(free: list[DateRange], start: str, end: str) -> bool:
    return any(r.start <= start and end <= r.end for r in free)


def days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def why_not(u: Unit, s: CallState) -> str:
    if s.people is not None and u.capacity < s.people:
        return "capacity"
    if s.tow_kg is not None and u.gross_weight_kg > s.tow_kg:
        return "weight"
    if u.needs_power and s.power is False:
        return "power"
    return "taken"


def was_offered(t: Transcript, unit: Unit) -> bool:
    """True when our last finished line offered this unit and asked to book it, so a yes
    from the caller is a yes to this unit and not to something else."""
    for it in reversed(t.items):
        if it.type == "message" and it.role == "assistant":
            if it.interrupted:
                return False
            line = fold(it.content).lower()
            return fold(unit.name).lower() in line and "book" in line and "?" in line
    return False
