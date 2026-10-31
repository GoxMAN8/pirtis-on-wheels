"""Replays fixtures through the real harness with a scripted model: the call state the
model should produce goes in, and the harness checks bookings, actions and invariants.
No tokens are spent, so this proves the wiring before any live run."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.run import Runner, RunResult
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

# a judge that passes every criterion; the judge is tested live, not here
PASS_JUDGE = Fake(
    default=Response(
        content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in range(1, 9)})
    )
)


def state(**kw: Any) -> dict[str, Any]:
    """A CallState as the model would return it, nulls unless given."""
    base: dict[str, Any] = {
        "gist": "",
        "intent": "book",
        "full_name": None,
        "people": None,
        "start": None,
        "end": None,
        "tow_quote": None,
        "tow_kg": None,
        "power": None,
        "unit": None,
        "accepted_offer": False,
    }
    return {**base, **kw}


GOLD: dict[str, dict[str, Any]] = {
    "happy-path": state(
        full_name="Jonas Jonaitis",
        people=4,
        start="2026-10-06",
        end="2026-10-07",
        tow_quote="it does two thousand",
        tow_kg=2000,
        unit="unit_mazoji",
        accepted_offer=True,
    ),
    "capacity": state(
        full_name="Eglė Butkutė",
        people=8,
        start="2026-10-03",
        end="2026-10-04",
        tow_quote="three and a half tonnes it says",
        tow_kg=3500,
        power=True,
        unit="unit_mazoji",
    ),
    "ask-tow-capacity": state(
        full_name="Tomas Petrauskas",
        people=7,
        start="2026-10-10",
        end="2026-10-11",
        power=True,
        unit="unit_didzioji",
    ),
}


def fixture(id: str) -> Fixture:
    for f in load_fixtures(FIXTURES):
        if f.id == id:
            return f
    raise LookupError(id)


def replay(id: str, script: list[dict[str, Any]]) -> RunResult:
    llm = Fake(script=[Response(content=json.dumps(s)) for s in script])
    runner = Runner(llm=llm, judge=PASS_JUDGE, decide=decide, handlers=handlers)
    return runner.run(fixture(id)).runs[0]


def speech(r: RunResult) -> str:
    return " ".join(str(e.get("text")) for e in r.effects if e.get("type") == "speech")


@pytest.mark.parametrize("id", sorted(GOLD))
def test_gold_state_passes_the_fixture(id: str) -> None:
    r = replay(id, [GOLD[id]])
    assert r.passed(), (r.error, r.failures, speech(r))
    assert r.stats.calls == 1
    assert not re.search(r"\d", speech(r)), "numbers must be spoken as words"


def test_capacity_offers_didzioji_and_asks() -> None:
    said = speech(replay("capacity", [GOLD["capacity"]]))
    assert "Didžioji" in said and said.endswith("Shall I book it?")


def test_no_tow_number_means_ask_never_book() -> None:
    said = speech(replay("ask-tow-capacity", [GOLD["ask-tow-capacity"]]))
    assert "tow" in said


def test_interim_speech_waits_without_a_model_call() -> None:
    r = replay("interim-speech", [])
    assert r.passed(), r.failures
    assert r.stats.calls == 0


def test_unreadable_model_answer_apologises() -> None:
    llm = Fake(script=[Response(content="not json")])
    runner = Runner(llm=llm, judge=PASS_JUDGE, decide=decide, handlers=handlers)
    r = runner.run(fixture("ask-tow-capacity")).runs[0]
    assert "say it again" in speech(r)
