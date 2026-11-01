"""The rules on calls nobody wrote: a thousand generated callers, groups, cars, sites,
dates and sauna wishes, against calendars made busier at random, straight into the
policy. Whatever the case, the brief's hard promises hold:

- an offer is one the API would accept, as offered (no 422, no 409)
- nothing is written without the caller's yes
- no customer's data but the caller's own is read
- a refusal says the true reason: too many people, a car too light, or no sauna free

The seed of the generator is fixed, so a failure here reproduces exactly.
"""

import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pirtis.agent.policy import NoFit, Offer, OwnClash, apply_weekend_min, next_step
from pirtis.agent.understand import CallState
from pirtis.api import client as api_client
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import ApiError, BookingRequest, Site
from pirtis.harness.fixture import load_fixtures

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
CASES = {f.id: f for f in load_fixtures(FIXTURES)}
# two callers already identified on the kit's own transcripts: Jonas has a booking of his
# own (so clashes come up), Eglė has none
CALLERS = [("weekend-minimum", "cus_07"), ("power", "cus_19")]
TODAY = date(2026, 9, 28)
OTHERS = ["cus_12", "cus_31"]


def world(case: str, extra: list[BookingRequest]) -> Memory:
    """The case's seed with these extra bookings: the same calendar, rebuilt fresh."""
    api = Memory(load_seed(CASES[case].seed_path()))
    for req in extra:
        api.create_booking(req)
    return api


def busier(api: Memory, rng: random.Random) -> list[BookingRequest]:
    """A few more bookings by other customers, each one the API accepts."""
    units = api.list_units()
    made: list[BookingRequest] = []
    for _ in range(rng.randint(0, 5)):
        u = rng.choice(units)
        start = TODAY + timedelta(days=rng.randint(0, 35))
        end = start + timedelta(days=rng.randint(1, 3))
        req = BookingRequest(rng.choice(OTHERS), u.id, start.isoformat(), end.isoformat(),
                             1, Site(True), 10_000)  # fmt: skip
        try:
            api.create_booking(req)
            made.append(req)
        except ApiError:
            pass  # taken, or a turnaround day: this calendar simply stays as it was
    return made


def reading(rng: random.Random) -> CallState:
    start = TODAY + timedelta(days=rng.randint(0, 30))
    end = start + timedelta(days=rng.choice([0, 0, 1, 1, 2, 3]))
    unit = rng.choice([None, None, "unit_mazoji", "unit_statine", "unit_vidutine", "unit_didzioji"])
    heater = rng.choice([None, None, "wood", "electric"])
    state: dict[str, Any] = {
        "gist": "", "intent": "book", "full_name": None,
        "people": rng.randint(1, 10), "start": start.isoformat(), "end": end.isoformat(),
        "shift_days": None, "change_target_start": None, "tow_quote": None,
        "tow_kg": rng.choice([600, 800, 1000, 1200, 1500, 1800, 2000, 2500, 3500]),
        "power": rng.choice([None, True, False]), "unit": unit,
        "unit_is_must": bool(unit) and rng.random() < 0.3, "heater_pref": heater,
        "heater_reason": "smoke" if heater and rng.random() < 0.3 else None,
        "unit_quote": None, "answer": None, "consent_quote": None,
        "accepted_offer": False, "go_ahead": False,
    }  # fmt: skip
    return CallState.model_validate(state)


def test_the_policys_promises_hold_on_a_thousand_generated_calls() -> None:
    rng = random.Random(20261101)
    seen: dict[str, int] = {}
    for i in range(1000):
        case, customer = rng.choice(CALLERS)
        f = CASES[case]
        api = Memory(load_seed(f.seed_path()))
        extra = busier(api, rng)
        s = reading(rng)
        calls_before = len(api.log())
        d = next_step(f.transcript, s, api, api.list_units())
        made = api.log()[calls_before:]
        kind = type(d).__name__
        seen[kind] = seen.get(kind, 0) + 1
        where = f"call {i}: {case}, {s.people} people, tow {s.tow_kg}, power {s.power}, " \
                f"{s.start}..{s.end}, unit {s.unit} -> {kind}"  # fmt: skip

        # nothing written without a yes; nobody else's data read
        writes = (api_client.METHOD_BOOKINGS_CREATE, api_client.METHOD_BOOKINGS_UPDATE)
        assert not [c for c in made if c.method in writes], where
        for c in made:
            assert c.status < 400, f"{where}: {c.method} {c.status} {c.code}"
            assert c.method != api_client.METHOD_CUSTOMERS_GET, where
            if c.method == api_client.METHOD_BOOKINGS_LIST:
                assert c.args.customer_id == customer, where

        if isinstance(d, Offer):
            # an electric sauna is offered only to a caller who said there's mains power
            assert not d.unit.needs_power or s.power is True, where
            assert d.unit.capacity >= (s.people or 0), where
            assert d.unit.gross_weight_kg <= (s.tow_kg or 0), where
            # and the API takes it, exactly as offered
            req = BookingRequest(customer, d.unit.id, d.start, d.end, d.people,
                                 Site(s.power is not False), s.tow_kg or 0)  # fmt: skip
            try:
                api.create_booking(req)
            except ApiError as e:
                raise AssertionError(f"{where}: offered, but the API says {e.code}") from e
        elif isinstance(d, NoFit):
            units = api.list_units()
            if d.reason == "too_many":
                assert (s.people or 0) > max(u.capacity for u in units), where
            elif d.reason == "too_light":
                assert (s.tow_kg or 0) < min(u.gross_weight_kg for u in units), where
            elif d.reason in ("nothing_fits", "no_dates"):
                # true: no sauna the caller can take is free on the dates asked (stretched to
                # the weekend minimum, as the policy stretches them)
                start, end = apply_weekend_min(
                    date.fromisoformat(s.start or ""), date.fromisoformat(s.end or "")
                )
                hard = s.heater_pref is not None and (s.unit_is_must or bool(s.heater_reason))
                for u in units:
                    if (
                        hard
                        and u.heater != s.heater_pref
                        and not (s.unit_is_must and u.id == s.unit)
                    ):
                        continue  # the caller ruled this heater out
                    req = BookingRequest(customer, u.id, start.isoformat(), end.isoformat(),
                                         s.people or 1, Site(s.power is not False),
                                         s.tow_kg or 0)  # fmt: skip
                    try:
                        world(case, extra).create_booking(req)
                    except ApiError:
                        continue
                    raise AssertionError(f"{where}: said none fits, but {u.name} does")
        elif isinstance(d, OwnClash):
            assert customer == "cus_07", where  # only Jonas has a booking of his own

    # the generator reached every kind of answer worth checking
    assert {"Offer", "NoFit", "Ask"} <= set(seen), seen
