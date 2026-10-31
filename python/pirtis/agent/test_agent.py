"""Replays fixtures through the real harness with a scripted model: the call state the
model should produce goes in, and the harness checks bookings, actions and invariants.
No tokens are spent, so this proves the wiring before any live run.

The call states come from gold/, the same files the extractor eval compares the live
model against: if the model produces the gold state, the fixture passes."""

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
GOLD_DIR = Path(__file__).parent / "gold"
GOLD: dict[str, dict[str, Any]] = {
    d["fixture"]: d["state"] for d in (json.loads(p.read_text()) for p in GOLD_DIR.glob("*.json"))
}

# a judge that passes every criterion; the judge is tested live, not here
PASS_JUDGE = Fake(
    default=Response(
        content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in range(1, 9)})
    )
)


def fixture(id: str) -> Fixture:
    for f in load_fixtures(FIXTURES) + load_fixtures(FIXTURES / "extra"):
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


def test_turnaround_names_the_blocked_day_and_no_other_unit() -> None:
    said = speech(replay("turnaround", [GOLD["turnaround"]]))
    assert "Saturday" in said and "Sunday the fourth" in said and "Didžioji" not in said


def test_weekend_minimum_explains_the_rule_and_never_offers_friday() -> None:
    said = speech(replay("weekend-minimum", [GOLD["weekend-minimum"]]))
    assert "two days minimum" in said and "Friday" not in said


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


SEED_CUSTOMERS = json.loads((FIXTURES / "seeds" / "base.json").read_text())["customers"]


@pytest.mark.parametrize("id", sorted(GOLD))
def test_no_reply_ever_names_another_customer(id: str) -> None:
    """Privacy holds on every path, not only in the refusal: nothing said or texted to a
    caller names any customer but the one calling."""
    f = fixture(id)
    r = replay(id, [GOLD[id]])
    heard = " ".join(str(e.get("text")) for e in r.effects if e.get("type") in ("speech", "sms"))
    for c in SEED_CUSTOMERS:
        if c["phone"] == f.transcript.caller.phone:
            continue
        for part in c["name"].split():
            assert part not in heard, f"{id}: said {part!r}"
        assert c["phone"] not in heard


def test_after_a_barge_in_the_reply_is_rebuilt_not_repeated() -> None:
    """The caller cut into our offer of Vidutinė to say eight, not six. The next reply
    comes from the recomputed state: it never repeats the cut-off line or its unit."""
    f = fixture("barge-in-correction")
    cut = next(it for it in f.transcript.items if it.interrupted).content
    said = speech(replay("barge-in-correction", [GOLD["barge-in-correction"]]))
    assert cut not in said and "Vidutinė" not in said
    assert "Didžioji" in said and "eight" in said
