"""The booking rules, mirrored from the API so we check before we write.

The API enforces every rule on write and answers 4xx when one is broken; a 422 means
the receptionist offered something it should not have. violations() runs the same
rules first, on every candidate offer and before every write, and a property test
checks it against the API itself. That is what makes a 422 impossible by
construction rather than merely unlikely.
"""

from datetime import date, timedelta

from pirtis.api.types import (
    CODE_BAD_DATES,
    CODE_BAD_PEOPLE,
    CODE_CAPACITY_EXCEEDED,
    CODE_MIN_DAYS_WEEKEND,
    CODE_OVERLAPS_EXISTING,
    CODE_POWER_REQUIRED,
    CODE_TOW_WEIGHT_EXCEEDED,
    CODE_UNAVAILABLE,
    STATUS_BOOKED,
    Booking,
    BookingRequest,
    DateRange,
    Unit,
)

DAY = timedelta(days=1)


def days(start: date, end: date) -> list[date]:
    """Every date from start to end, inclusive."""
    return [start + i * DAY for i in range((end - start).days + 1)]


def free_days(ranges: list[DateRange]) -> set[date]:
    """The free ranges availability() returned for one unit, as a set of dates. Days
    outside the queried window are not in it, so they count as not free."""
    return {d for r in ranges for d in days(date.fromisoformat(r.start), date.fromisoformat(r.end))}


def touches_weekend(start: date, end: date) -> bool:
    return any(d.weekday() >= 5 for d in days(start, end))


def violations(
    req: BookingRequest, unit: Unit, free: set[date], own: list[Booking], exclude_id: str = ""
) -> list[str]:
    """The API reason codes this request would fail with; empty means it would book.

    free is the unit's free days from availability(), which already leaves out every
    booking of the unit, the turnaround day either side, and blocked days, so the
    API's three date checks (taken, turnaround, out of service) become one: every
    requested day must be free. own is the caller's own bookings; exclude_id skips the
    booking being changed, as the API does on update.
    """
    try:
        start, end = date.fromisoformat(req.start), date.fromisoformat(req.end)
    except ValueError:
        return [CODE_BAD_DATES]
    if end < start:
        return [CODE_BAD_DATES]
    out = []
    if req.people < 1:
        out.append(CODE_BAD_PEOPLE)
    if req.people > unit.capacity:
        out.append(CODE_CAPACITY_EXCEEDED)
    if unit.gross_weight_kg > req.tow_capacity_kg:
        out.append(CODE_TOW_WEIGHT_EXCEEDED)
    if unit.needs_power and not req.site.power_available:
        out.append(CODE_POWER_REQUIRED)
    if touches_weekend(start, end) and len(days(start, end)) < 2:
        out.append(CODE_MIN_DAYS_WEEKEND)
    if any(
        b.id != exclude_id
        and b.status == STATUS_BOOKED
        and date.fromisoformat(b.start) <= end
        and start <= date.fromisoformat(b.end)
        for b in own
    ):
        out.append(CODE_OVERLAPS_EXISTING)
    if any(d not in free for d in days(start, end)):
        out.append(CODE_UNAVAILABLE)
    return out


def without_booking(free: set[date], b: Booking) -> set[date]:
    """The unit's free days as they would be without booking b, for moving it.

    b's own days are certainly free once it moves. Its turnaround day either side is
    counted free only when the day beyond is free too, since then nothing but b can
    explain it being busy. Otherwise we stay cautious and keep it busy.
    """
    start, end = date.fromisoformat(b.start), date.fromisoformat(b.end)
    out = free | set(days(start, end))
    if start - 2 * DAY in free:
        out.add(start - DAY)
    if end + 2 * DAY in free:
        out.add(end + DAY)
    return out
