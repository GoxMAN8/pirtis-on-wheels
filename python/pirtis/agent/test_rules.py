"""The rules mirror is only worth something if it agrees with the API. These tests ask
the API itself: hundreds of random bookings and moves against the real seed, and our
check must say yes exactly when the API would."""

import random
from datetime import date, timedelta
from pathlib import Path

from pirtis.agent.rules import days, free_days, touches_weekend, violations, without_booking
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import (
    ApiError,
    AvailabilityQuery,
    Booking,
    BookingPatch,
    BookingQuery,
    BookingRequest,
    Site,
    Unit,
)

SEED = Path(__file__).resolve().parents[3] / "fixtures" / "seeds" / "base.json"
TODAY = date(2026, 9, 28)


def fresh() -> Memory:
    return Memory(load_seed(SEED))


def unit_free(api: Memory, unit_id: str, start: date, end: date) -> set[date]:
    q = AvailabilityQuery(from_=start.isoformat(), to=end.isoformat())
    return free_days(next(a.free for a in api.availability(q) if a.unit_id == unit_id))


def own(api: Memory, customer: str) -> list[Booking]:
    return api.list_bookings(BookingQuery(customer_id=customer))


def test_helpers() -> None:
    assert days(date(2026, 10, 3), date(2026, 10, 5)) == [
        date(2026, 10, 3),
        date(2026, 10, 4),
        date(2026, 10, 5),
    ]
    assert touches_weekend(date(2026, 10, 2), date(2026, 10, 3))  # Fri-Sat
    assert not touches_weekend(date(2026, 10, 5), date(2026, 10, 9))  # Mon-Fri


def test_violations_name_the_rule() -> None:
    api = fresh()
    units = {u.id: u for u in api.list_units()}
    mazoji = units["unit_mazoji"]

    def check(
        people: int, tow: int, power: bool, s: str, e: str, cust: str = "cus_12"
    ) -> list[str]:
        req = BookingRequest(cust, mazoji.id, s, e, people, Site(power), tow)
        lo, hi = sorted([date.fromisoformat(s), date.fromisoformat(e)])
        return violations(req, mazoji, unit_free(api, mazoji.id, lo, hi), own(api, cust))

    assert check(4, 2000, False, "2026-10-06", "2026-10-07") == []
    assert check(5, 2000, False, "2026-10-06", "2026-10-07") == ["CAPACITY_EXCEEDED"]
    assert check(4, 700, False, "2026-10-06", "2026-10-07") == ["TOW_WEIGHT_EXCEEDED"]
    assert check(4, 2000, False, "2026-10-10", "2026-10-10") == ["MIN_DAYS_WEEKEND"]
    assert check(4, 2000, False, "2026-10-16", "2026-10-17") == ["UNAVAILABLE"]  # bk_65
    assert check(4, 2000, False, "2026-10-06", "2026-10-07", "cus_31") == ["OVERLAPS_EXISTING"]
    assert check(4, 2000, False, "2026-10-07", "2026-10-06") == ["BAD_DATES"]
    assert check(0, 2000, False, "2026-10-06", "2026-10-07") == ["BAD_PEOPLE"]
    vid = units["unit_vidutine"]
    req = BookingRequest("cus_12", vid.id, "2026-10-12", "2026-10-13", 6, Site(False), 2000)
    free = unit_free(api, vid.id, date(2026, 10, 12), date(2026, 10, 13))
    assert violations(req, vid, free, []) == ["POWER_REQUIRED"]


def _random_request(rng: random.Random, units: list[Unit], customers: list[str]) -> BookingRequest:
    start = TODAY + timedelta(days=rng.randint(-3, 40))
    end = start + timedelta(days=rng.randint(0, 3))
    return BookingRequest(
        customer_id=rng.choice(customers),
        unit_id=rng.choice(units).id,
        start=start.isoformat(),
        end=end.isoformat(),
        people=rng.randint(0, 9),
        site=Site(power_available=rng.random() < 0.5),
        tow_capacity_kg=rng.choice([600, 750, 900, 1100, 1500, 1600, 2000, 3500]),
    )


def test_create_property_our_check_agrees_with_the_api() -> None:
    """Exact agreement: no rule violation by our check if and only if the API books it."""
    rng = random.Random(20261031)
    seed_api = fresh()
    units = seed_api.list_units()
    by_id = {u.id: u for u in units}
    customers = ["cus_07", "cus_12", "cus_19", "cus_31"]
    outcomes = {True: 0, False: 0}
    for _ in range(600):
        api = fresh()
        req = _random_request(rng, units, customers)
        s, e = date.fromisoformat(req.start), date.fromisoformat(req.end)
        ours = violations(
            req, by_id[req.unit_id], unit_free(api, req.unit_id, s, e), own(api, req.customer_id)
        )
        try:
            api.create_booking(req)
            booked = True
        except ApiError:
            booked = False
        assert (not ours) == booked, (req, ours)
        outcomes[booked] += 1
    assert outcomes[True] >= 100 and outcomes[False] >= 100, outcomes


def test_move_property_our_check_is_never_wrong_when_it_says_yes() -> None:
    """For moves we only need soundness: whenever our check allows a move, the API
    accepts it. (Being cautious near the booking's own turnaround day is allowed.)"""
    rng = random.Random(7)
    allowed = 0
    for _ in range(400):
        api = fresh()
        b = rng.choice(api.bookings())
        unit = next(u for u in api.list_units() if u.id == b.unit_id)
        start = date.fromisoformat(b.start) + timedelta(days=rng.randint(-6, 20))
        end = start + timedelta(days=rng.randint(0, 3))
        if start < TODAY:
            continue
        free = without_booking(
            unit_free(api, unit.id, start - timedelta(days=3), end + timedelta(days=3)), b
        )
        req = BookingRequest(
            b.customer_id, b.unit_id, start.isoformat(), end.isoformat(), b.people, b.site,
            b.tow_capacity_kg,
        )  # fmt: skip
        if violations(req, unit, free, own(api, b.customer_id), exclude_id=b.id):
            continue
        allowed += 1
        api.update_booking(b.id, b.version, BookingPatch(start=req.start, end=req.end))
    assert allowed >= 50


def test_a_one_day_move_is_allowed() -> None:
    """Moving a booking by a single day crosses its own turnaround day, which
    availability marks busy; only the booking itself explains it, so it counts as free."""
    seed = load_seed(SEED)
    b = Booking(
        customer_id="cus_12", unit_id="unit_mazoji", start="2026-10-03", end="2026-10-04",
        people=4, site=Site(False), tow_capacity_kg=1500, id="bk_91", version=1,
        status="booked", total=240,
    )  # fmt: skip
    seed.bookings.append(b)
    api = Memory(seed)
    mazoji = next(u for u in api.list_units() if u.id == "unit_mazoji")
    free = without_booking(unit_free(api, mazoji.id, date(2026, 10, 1), date(2026, 10, 8)), b)
    req = BookingRequest("cus_12", mazoji.id, "2026-10-04", "2026-10-05", 4, Site(False), 1500)
    assert violations(req, mazoji, free, own(api, "cus_12"), exclude_id="bk_91") == []
    api.update_booking("bk_91", 1, BookingPatch(start="2026-10-04", end="2026-10-05"))
