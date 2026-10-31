"""The policy, driven by hand-built call states against the real in-memory API: every
fixture's scenario plus the edges where a receptionist could book wrong, leak data or
break a rule. No model involved."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pirtis.agent.policy import (
    Ask,
    Booked,
    BookingsRead,
    Cancelled,
    Decision,
    Goodbye,
    Handoff,
    HeardOffer,
    Hold,
    Moved,
    NoFit,
    Offer,
    OwnClash,
    Price,
    Refuse,
    last_offer,
    next_step,
)
from pirtis.agent.speech import SORRY, render
from pirtis.agent.understand import CallState
from pirtis.api import client as api_client
from pirtis.api.client import Fault
from pirtis.api.memory import Memory, load_seed
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.transcript.types import Item

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
ALL = {f.id: f for f in load_fixtures(FIXTURES) + load_fixtures(FIXTURES / "extra")}


def state(**kw: Any) -> CallState:
    base: dict[str, Any] = {
        "gist": "",
        "intent": "book",
        "full_name": None,
        "people": None,
        "start": None,
        "end": None,
        "shift_days": None,
        "change_target_start": None,
        "tow_quote": None,
        "tow_kg": None,
        "power": None,
        "unit": None,
        "unit_is_must": False,
        "heater_pref": None,
        "heater_reason": None,
        "answer": None,
        "accepted_offer": False,
        "go_ahead": False,
    }
    return CallState.model_validate({**base, **kw})


def run(
    fid: str, faults: list[Fault] | None = None, items: list[Item] | None = None, **kw: Any
) -> tuple[Decision, Memory]:
    f: Fixture = ALL[fid]
    seed = load_seed(f.seed_path())
    seed.bookings.extend(sb.booking() for sb in f.setup.bookings)
    api = Memory(seed, f.faults if faults is None else faults)
    t = f.transcript if items is None else replace(f.transcript, items=items)
    d = next_step(t, state(**kw), api, api.list_units())
    _assert_clean(api)
    return d, api


def _assert_clean(api: Memory) -> None:
    for c in api.log():
        assert c.injected or c.status < 400, f"{c.method} {c.status} {c.code}: {c.detail}"
        if c.method == api_client.METHOD_BOOKINGS_LIST:
            assert c.args.customer_id, "never list bookings without our caller's id"
        assert c.method != api_client.METHOD_CUSTOMERS_GET


def methods(api: Memory) -> list[str]:
    return [c.method for c in api.log()]


def created(api: Memory) -> list[tuple[str, str, str, str]]:
    """Bookings made in this run: the in-memory API numbers new ones from bk_101."""
    return [
        (b.unit_id, b.start, b.end, b.status)
        for b in api.bookings()
        if int(b.id.removeprefix("bk_")) >= 101
    ]


JONAS: dict[str, Any] = {"full_name": "Jonas Jonaitis"}


# the given fixtures


def test_00_books_what_was_read_back() -> None:
    d, api = run(
        "happy-path", people=4, start="2026-10-06", end="2026-10-07", tow_kg=2000,
        unit="unit_mazoji", accepted_offer=True, **JONAS,
    )  # fmt: skip
    assert isinstance(d, Booked)
    assert created(api) == [("unit_mazoji", "2026-10-06", "2026-10-07", "booked")]


def test_00_yes_but_five_of_us_re_offers_and_never_books() -> None:
    d, api = run(
        "happy-path", people=5, start="2026-10-06", end="2026-10-07", tow_kg=2000,
        unit="unit_mazoji", accepted_offer=True, **JONAS,
    )  # fmt: skip
    # five don't fit Mažoji: the next step is a new question or offer, never a booking
    assert not isinstance(d, Booked)
    assert api_client.METHOD_BOOKINGS_CREATE not in methods(api)


def test_01_booking_question_before_identification_asks_for_the_name() -> None:
    d, api = run("identify-before-reading", intent="check_booking")
    assert d == Ask("name")
    assert methods(api) == [api_client.METHOD_UNITS_LIST]


def test_04_too_small_offers_the_one_that_fits() -> None:
    d, _ = run(
        "capacity", people=8, start="2026-10-03", end="2026-10-04", tow_kg=3500, power=True,
        unit="unit_mazoji",
    )  # fmt: skip
    assert isinstance(d, Offer)
    assert (d.unit.id, d.why_not) == ("unit_didzioji", "capacity")
    assert d.instead_of is not None and d.instead_of.id == "unit_mazoji"


def test_05_no_tow_number_asks_even_when_told_to_book() -> None:
    d, api = run(
        "ask-tow-capacity", people=7, start="2026-10-10", end="2026-10-11", power=True,
        unit="unit_didzioji", go_ahead=True,
    )  # fmt: skip
    assert d == Ask("tow")
    assert api_client.METHOD_BOOKINGS_CREATE not in methods(api)


def test_06_tow_limit_leaves_vidutine() -> None:
    d, _ = run(
        "tow-weight", people=6, start="2026-10-10", end="2026-10-11", tow_kg=1500, power=True
    )
    assert isinstance(d, Offer) and d.unit.id == "unit_vidutine"


def test_07_no_mains_leaves_didzioji() -> None:
    d, _ = run("power", people=6, start="2026-10-10", end="2026-10-11", tow_kg=3000, power=False)
    assert isinstance(d, Offer) and d.unit.id == "unit_didzioji"


@pytest.mark.parametrize("must", [True, False])
def test_08_must_have_unit_moves_to_sunday_never_another_unit(must: bool) -> None:
    d, _ = run(
        "turnaround", people=6, start="2026-10-03", end="2026-10-04", tow_kg=2000, power=True,
        unit="unit_vidutine", unit_is_must=must, heater_pref="electric",
        heater_reason="the wood smoke sets off my asthma",
    )  # fmt: skip
    assert isinstance(d, Offer)
    assert (d.unit.id, d.start, d.end, d.blocked_day) == (
        "unit_vidutine", "2026-10-04", "2026-10-05", "2026-10-03",
    )  # fmt: skip


def test_09_moves_the_existing_booking_a_week() -> None:
    d, api = run(
        "change-of-plan", intent="change", shift_days=7, go_ahead=True, unit="unit_mazoji",
        change_target_start="2026-10-03",
    )  # fmt: skip
    assert isinstance(d, Moved)
    b = next(b for b in api.bookings() if b.id == "bk_91")
    assert (b.start, b.end, b.version) == ("2026-10-10", "2026-10-11", 2)
    update = next(c for c in api.log() if c.method == api_client.METHOD_BOOKINGS_UPDATE)
    assert (update.args["id"], update.args["version"]) == ("bk_91", 1)
    assert api_client.METHOD_BOOKINGS_CREATE not in methods(api)


def test_10_refuses_other_customers_details_without_reading_any() -> None:
    d, api = run(
        "other-customers-data", intent="other_customer_info", unit="unit_didzioji", people=8,
        start="2026-10-06", end="2026-10-07",
    )  # fmt: skip
    assert isinstance(d, Refuse)
    assert api_client.METHOD_BOOKINGS_LIST not in methods(api)
    line = str(render(d)[0]["text"])
    assert "can't share" in line and "details" in line


def test_11_single_saturday_becomes_the_weekend() -> None:
    d, _ = run(
        "weekend-minimum", people=4, start="2026-10-10", end="2026-10-10", tow_kg=2000,
        unit="unit_mazoji", **JONAS,
    )  # fmt: skip
    assert isinstance(d, Offer)
    assert (d.start, d.end, d.weekend_min) == ("2026-10-10", "2026-10-11", True)


def test_12_injected_409_rechecks_and_books() -> None:
    d, api = run(
        "slot-stolen", people=4, start="2026-10-10", end="2026-10-11", tow_kg=3000,
        unit="unit_mazoji", accepted_offer=True,
    )  # fmt: skip
    assert isinstance(d, Booked)
    creates = [c for c in api.log() if c.method == api_client.METHOD_BOOKINGS_CREATE]
    assert [(c.status, c.injected) for c in creates] == [(409, True), (200, False)]


def test_12_injected_500_checks_then_retries() -> None:
    d, api = run(
        "slot-stolen", faults=[Fault(on="bookings.create", nth=1, status=500)], people=4,
        start="2026-10-10", end="2026-10-11", tow_kg=3000, unit="unit_mazoji",
        accepted_offer=True,
    )  # fmt: skip
    assert isinstance(d, Booked)
    assert len(created(api)) == 1


# our own fixtures


def test_20_unknown_caller_goes_to_a_colleague() -> None:
    d, _ = run("unknown-caller", full_name="Marius Kazlauskas", people=6, start="2026-10-10")
    assert isinstance(d, Handoff) and d.queue == "new_customers"


def test_21_first_name_alone_is_never_sent_to_identify() -> None:
    d, api = run("first-name-only", full_name="Jonas", unit="unit_mazoji")
    assert d == Ask("name")
    assert api_client.METHOD_CUSTOMERS_IDENTIFY not in methods(api)


@pytest.mark.parametrize(
    "faults", [None, [Fault(on="bookings.update", nth=1, status=409, code="VERSION_MISMATCH")]]
)
def test_22_cancels_with_the_fetched_version(faults: list[Fault] | None) -> None:
    d, api = run(
        "cancel-booking", faults=faults, intent="cancel", unit="unit_didzioji",
        change_target_start="2026-10-06", accepted_offer=True,
    )  # fmt: skip
    assert isinstance(d, Cancelled)
    assert next(b for b in api.bookings() if b.id == "bk_88").status == "cancelled"


def test_23_nobody_seats_nine() -> None:
    d, _ = run(
        "nine-people", people=9, start="2026-10-10", end="2026-10-11", tow_kg=2500, power=True
    )
    assert d == NoFit("too_many", limit=8)


def test_24_car_too_light_for_any_unit() -> None:
    d, _ = run(
        "car-too-light", people=4, start="2026-10-10", end="2026-10-11", tow_kg=700,
        unit="unit_mazoji",
    )  # fmt: skip
    assert d == NoFit("too_light", limit=750)


def test_25_single_sunday_extends_forward() -> None:
    d, _ = run(
        "sunday-only", people=4, start="2026-10-11", end="2026-10-11", tow_kg=2000,
        unit="unit_mazoji",
    )  # fmt: skip
    assert isinstance(d, Offer)
    assert (d.start, d.end, d.weekend_min) == ("2026-10-11", "2026-10-12", True)


def test_27_correction_to_eight_offers_didzioji() -> None:
    d, _ = run(
        "barge-in-correction", people=8, start="2026-10-10", end="2026-10-11", tow_kg=3500,
        power=True,
    )  # fmt: skip
    assert isinstance(d, Offer) and d.unit.id == "unit_didzioji"


def test_28_identifies_then_asks_the_next_detail() -> None:
    d, api = run(
        "identify-this-turn", full_name="Eglė Butkutė", start="2026-10-10", end="2026-10-11"
    )
    assert d == Ask("people")
    identify = next(c for c in api.log() if c.method == api_client.METHOD_CUSTOMERS_IDENTIFY)
    assert identify.result.ok


def test_29_own_booking_clash_is_named() -> None:
    d, api = run(
        "own-booking-overlap", people=4, start="2026-10-06", end="2026-10-07", tow_kg=1500,
        unit="unit_mazoji",
    )  # fmt: skip
    assert isinstance(d, OwnClash) and d.booking.id == "bk_88"
    assert api_client.METHOD_BOOKINGS_CREATE not in methods(api)


# edges


def test_a_friday_shifted_onto_the_weekend_becomes_two_days() -> None:
    d, _ = run(
        "turnaround", people=6, start="2026-10-02", end="2026-10-02", tow_kg=2000, power=True,
        unit="unit_vidutine", unit_is_must=True,
    )  # fmt: skip
    assert isinstance(d, Offer)
    assert (d.start, d.end) == ("2026-10-04", "2026-10-05")


def test_a_one_day_move_is_made() -> None:
    d, api = run("change-of-plan", intent="change", shift_days=1, go_ahead=True)
    assert isinstance(d, Moved)
    b = next(b for b in api.bookings() if b.id == "bk_91")
    assert (b.start, b.end) == ("2026-10-04", "2026-10-05")


def test_a_change_without_go_ahead_is_read_back_first() -> None:
    d, api = run("change-of-plan", intent="change", shift_days=7)
    assert api_client.METHOD_BOOKINGS_UPDATE not in methods(api)
    assert "Shall I" in str(render(d)[0]["text"])


def test_a_heater_preference_without_a_reason_only_sorts() -> None:
    d, _ = run(
        "tow-weight", people=6, start="2026-10-10", end="2026-10-11", tow_kg=1500, power=True,
        heater_pref="wood",
    )  # fmt: skip
    assert isinstance(d, Offer) and d.unit.id == "unit_vidutine"


def test_price_is_answered_before_identification() -> None:
    d, api = run("identify-before-reading", intent="price", unit="unit_mazoji")
    assert isinstance(d, Price)
    assert methods(api) == [api_client.METHOD_UNITS_LIST]


@pytest.mark.parametrize("intent, kind", [("goodbye", Goodbye), ("hold", Hold), ("human", Handoff)])
def test_goodbye_hold_and_person(intent: str, kind: type) -> None:
    d, _ = run("happy-path", intent=intent)
    assert isinstance(d, kind)


def test_booking_status_reads_only_the_callers_own() -> None:
    d, api = run("cancel-booking", intent="check_booking")
    assert isinstance(d, BookingsRead)
    assert {b.id for b, _ in d.bookings} == {"bk_77", "bk_88"}


def test_two_misunderstandings_in_a_row_hand_over() -> None:
    items = list(ALL["happy-path"].transcript.items) + [
        Item(type="message", role="assistant", start=70, end=72, content=SORRY),
        Item(
            type="message", role="user", start=73, end=75, content="mmf the, the thing",
            is_final=True, speech_final=True,
        ),
    ]  # fmt: skip
    d, _ = run("happy-path", items=items, intent="unclear")
    assert isinstance(d, Handoff)


# the read-back parser


def test_last_offer_reads_the_fixture_offers() -> None:
    api = Memory(load_seed(FIXTURES / "seeds" / "base.json"))
    units = api.list_units()
    assert last_offer(ALL["happy-path"].transcript, units) == HeardOffer(
        "unit_mazoji", "2026-10-06", "2026-10-07", 4
    )
    assert last_offer(ALL["slot-stolen"].transcript, units) == HeardOffer(
        "unit_mazoji", "2026-10-10", "2026-10-11", 4
    )
    assert last_offer(ALL["capacity"].transcript, units) is None  # we asked, not offered


@pytest.mark.parametrize(
    "fid, kw",
    [
        ("capacity", dict(people=8, start="2026-10-03", end="2026-10-04", tow_kg=3500,
                          power=True, unit="unit_mazoji")),
        ("turnaround", dict(people=6, start="2026-10-03", end="2026-10-04", tow_kg=2000,
                            power=True, unit="unit_vidutine", unit_is_must=True)),
        ("weekend-minimum", dict(people=4, start="2026-10-10", end="2026-10-10", tow_kg=2000,
                                 unit="unit_mazoji")),
        ("tow-weight", dict(people=6, start="2026-10-10", end="2026-10-11", tow_kg=1500,
                            power=True, unit="unit_didzioji")),
    ],
)  # fmt: skip
def test_our_own_offers_read_back_exactly(fid: str, kw: dict[str, Any]) -> None:
    d, api = run(fid, **kw)
    assert isinstance(d, Offer)
    line = str(render(d)[0]["text"])
    heard_in = replace(
        ALL[fid].transcript,
        items=[Item(type="message", role="assistant", start=1, end=2, content=line)],
    )
    assert last_offer(heard_in, api.list_units()) == HeardOffer(d.unit.id, d.start, d.end, d.people)
