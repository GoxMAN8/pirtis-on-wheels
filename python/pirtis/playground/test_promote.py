"""A flagged turn becomes a test case through a person, never by itself: the call as decide
saw it at that turn, the world as it was, and what the reviewer says should happen. The
case must load in the kit's harness, replay what happened, and go red when the reviewer's
expectation differs from what the agent did."""

import json
from pathlib import Path
from typing import Any

import pytest

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.api.memory import load_seed
from pirtis.harness.fixture import load_fixtures
from pirtis.harness.run import Runner
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.playground.calls import UNKNOWN_PHONE, LiveCall
from pirtis.playground.promote import Expected, PromoteError, build, save, world_before
from pirtis.playground.test_calls import JONAS, LINES, SEED, T1, T2, T3, T4, make, scripted, state
from pirtis.transcript.types import Item

PASS_JUDGE = Fake(
    default=Response(
        content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in range(1, 9)})
    )
)
SEEDS = SEED.parent
OFFER_JUDGE = ["The reply offers Mažoji for Tuesday the sixth to Wednesday the seventh of October."]


def folder(tmp_path: Path) -> Path:
    d = tmp_path / "cases"
    d.mkdir()
    (d / "seeds").symlink_to(SEEDS)
    return d


def call_through(tmp_path: Path, *states: dict[str, Any], lines: list[str]) -> LiveCall:
    calls = make(tmp_path, scripted(*states))
    call = calls.start(JONAS)
    for line in lines:
        calls.say(call, line)
    return call


def offer_case(tmp_path: Path, action: str = "say") -> tuple[LiveCall, Path]:
    call = call_through(tmp_path, T1, T2, T3, lines=LINES[:3])
    step = call.steps[2]  # the offer
    case = build(
        call, step, load_seed(SEED), Expected("Offers Mažoji for four", action, [], OFFER_JUDGE)
    )
    return call, save(case, folder(tmp_path))


def run(path: Path, *states: dict[str, Any]) -> Any:
    [f] = load_fixtures(path.parent)
    llm = Fake(script=[Response(content=json.dumps(s)) for s in states])
    return Runner(llm=llm, judge=PASS_JUDGE, decide=decide, handlers=handlers).run(f).runs[0]


def test_a_promoted_turn_is_a_fixture_the_kit_loads(tmp_path: Path) -> None:
    call, path = offer_case(tmp_path)
    assert path.name == "60_offers-mazoji-for-four.yaml"
    [f] = load_fixtures(path.parent)
    assert (f.id, f.expect.action, f.judge) == ("offers-mazoji-for-four", "say", OFFER_JUDGE)
    assert f.transcript.today == "2026-09-28" and f.transcript.now == call.steps[2]["at"]
    last = [i for i in f.transcript.items if i.type == "message"][-1]
    assert (last.role, last.content) == ("user", LINES[2])  # the call as decide saw it
    assert path.read_text().startswith("# Promoted by a reviewer from feedback on turn ")


def test_the_case_replays_what_happened(tmp_path: Path) -> None:
    _, path = offer_case(tmp_path)
    r = run(path, T3)
    assert r.passed(), r.failures


def test_a_corrected_expectation_turns_the_case_red(tmp_path: Path) -> None:
    """The loop's first step: the reviewer writes down what should have happened, and the
    current agent fails it until it's fixed."""
    _, path = offer_case(tmp_path, action="transfer")
    r = run(path, T3)
    assert not r.passed() and any("action: wanted transfer" in x for x in r.failures)


def test_bookings_made_earlier_in_the_call_are_set_up_under_free_ids(tmp_path: Path) -> None:
    """A fresh API numbers new bookings from bk_101, so a booking the call already made is
    set up under a free id below 100, in the setup and in the transcript alike."""
    bye = {**T3, "intent": "goodbye"}
    call = call_through(tmp_path, T1, T2, T3, T4, bye, lines=[*LINES, "Great, thanks, bye!"])
    step = call.steps[4]
    case = build(
        call,
        step,
        load_seed(SEED),
        Expected("Says goodbye after booking", "end_call", [], ["The reply says goodbye."]),
    )
    path = save(case, folder(tmp_path))
    [f] = load_fixtures(path.parent)
    [made] = f.setup.bookings
    assert made.id == "bk_99" and (made.unit, made.start, made.people) == (
        "unit_mazoji",
        "2026-10-06",
        4,
    )
    text = path.read_text()
    assert "bk_101" not in text and "bk_99" in text
    assert run(path, bye).passed()


def test_a_turn_after_a_cancellation_is_refused() -> None:
    """The kit's setup can only add booked rows, so a call that cancelled one can't be
    reproduced yet; better to say so than to write a fixture with the wrong world."""
    cancel = {"id": "bk_88", "version": 2, "patch": {"status": "cancelled"}}
    done = {
        "id": "bk_88",
        "version": 3,
        "status": "cancelled",
        "unit_id": "unit_didzioji",
        "start": "2026-10-06",
        "end": "2026-10-07",
    }
    items = [
        Item(
            type="function_call", call_id="c1", name="bookings.update", arguments=json.dumps(cancel)
        ),
        Item(type="function_call_output", call_id="c1", output=json.dumps(done)),
    ]
    with pytest.raises(PromoteError, match="cancel"):
        world_before(items, load_seed(SEED))


def test_only_seed_people_reach_a_fixture(tmp_path: Path) -> None:
    """D-39: a promoted case enters git with seed phones and names, or a stranger the
    reviewer confirms is made up, and no other number."""
    seed = load_seed(SEED)
    expected = Expected("Some case", "say", [], ["The reply asks for a name."])

    unseeded = make(tmp_path / "a", Fake())
    real = unseeded.start("+37061111111")
    unseeded.silence(real, 8)
    with pytest.raises(PromoteError, match="number"):
        build(real, real.steps[-1], seed, expected)

    named = call_through(
        tmp_path / "b", state(full_name="Petras Petraitis"), lines=["It's Petras Petraitis."]
    )
    with pytest.raises(PromoteError, match="name"):
        build(named, named.steps[-1], seed, expected)

    digits = call_through(tmp_path / "c", T1, lines=["Jonas Jonaitis, call me back on 869998877"])
    with pytest.raises(PromoteError, match="number"):
        build(digits, digits.steps[-1], seed, expected)

    stranger = make(tmp_path / "d", scripted(state(full_name="Ona Onaitė")))
    call = stranger.start(UNKNOWN_PHONE)
    stranger.say(call, "Hi, I'm Ona Onaitė.")
    build(
        call,
        call.steps[-1],
        seed,
        Expected("Stranger", "transfer", [], ["The reply puts the caller through."]),
    )


def test_cases_are_numbered_from_60_and_never_overwritten(tmp_path: Path) -> None:
    call = call_through(tmp_path, T1, T2, T3, lines=LINES[:3])
    d = folder(tmp_path)
    seed = load_seed(SEED)
    first = save(
        build(call, call.steps[2], seed, Expected("First case", "say", [], OFFER_JUDGE)), d
    )
    second = save(
        build(
            call,
            call.steps[1],
            seed,
            Expected("Second case", "say", [], ["It asks about the car."]),
        ),
        d,
    )
    assert (first.name[:3], second.name[:3]) == ("60_", "61_")
    with pytest.raises(PromoteError, match="already"):
        save(build(call, call.steps[0], seed, Expected("First case", "say", [], ["x y z"])), d)


def test_without_a_folder_nothing_is_written(tmp_path: Path) -> None:
    call = call_through(tmp_path, T1, T2, T3, lines=LINES[:3])
    case = build(
        call, call.steps[2], load_seed(SEED), Expected("Download only", "say", [], OFFER_JUDGE)
    )
    assert case.text.startswith("# Promoted") and "id: download-only" in case.text
    assert not list(tmp_path.rglob("*.yaml"))
