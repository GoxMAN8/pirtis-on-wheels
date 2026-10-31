"""Every turn leaves two traces: a note in the run's result, and one telemetry record on
the "pirtis.turn" logger. The record says what happened and what it cost, and never
what the caller said."""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.agent.telemetry import TurnRecord, to_otel
from pirtis.agent.version import AGENT_VERSION
from pirtis.api.client import Fault
from pirtis.api.memory import Memory, load_seed
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.run import Runner
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.transcript.types import Action

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
ALL = {f.id: f for f in load_fixtures(FIXTURES) + load_fixtures(FIXTURES / "extra")}
GOLD_DIR = Path(__file__).parent / "gold"


def gold(fid: str) -> dict[str, Any]:
    state: dict[str, Any] = json.loads((GOLD_DIR / f"{fid}.json").read_text())["state"]
    return state


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(record.getMessage()))


@pytest.fixture
def records() -> Iterator[list[dict[str, Any]]]:
    cap = Capture()
    logger = logging.getLogger("pirtis.turn")
    logger.addHandler(cap)
    yield cap.lines
    logger.removeHandler(cap)


def turn(fid: str, script: list[str], faults: list[Fault] | None = None) -> list[Action]:
    f: Fixture = ALL[fid]
    seed = load_seed(f.seed_path())
    seed.bookings.extend(sb.booking() for sb in f.setup.bookings)
    api = Memory(seed, f.faults if faults is None else faults)
    return decide(f.transcript, api, Fake(script=[Response(content=s) for s in script]))


def test_one_record_per_turn_with_what_happened(records: list[dict[str, Any]]) -> None:
    turn("happy-path", [json.dumps(gold("happy-path"))])
    assert len(records) == 1
    r = records[0]
    assert r["event"] == "turn" and r["telemetry_version"] == 1
    path = (r["gate"], r["intent"], r["decision"], r["fallback"])
    assert path == ("none", "book", "Booked", False)
    assert r["agent_version"] == AGENT_VERSION
    assert r["llm_calls"] == 1 and r["latency_ms"] >= 0
    assert [c["method"] for c in r["api_calls"]][-1] == "bookings.create"
    assert all(c["status"] == 200 for c in r["api_calls"])
    assert r["action_types"] == ["say", "sms", "note"]


def test_the_record_holds_no_content(records: list[dict[str, Any]]) -> None:
    turn("happy-path", [json.dumps(gold("happy-path"))])
    text = json.dumps(records[0], ensure_ascii=False)
    for said in ["Jonas", "Jonaitis", "Passat", "two thousand", "+37062223344", "62223344"]:
        assert said not in text, said
    assert re.fullmatch(r"[0-9a-f]{16}", records[0]["phone_hash"])


def test_gated_turns_are_recorded_without_a_model_call(records: list[dict[str, Any]]) -> None:
    turn("interim-speech", [])
    turn("rising-frustration", [])
    wait, transfer = records
    assert (wait["gate"], wait["llm_calls"], wait["decision"]) == ("wait", 0, "")
    assert transfer["gate"] == "transfer"
    assert transfer["transfer_reason"] == "caller asked for a person"


def test_a_fallback_is_marked(records: list[dict[str, Any]]) -> None:
    turn("ask-tow-capacity", ["not json"])
    assert (records[0]["decision"], records[0]["fallback"]) == ("Sorry", True)


def test_every_turn_ends_with_a_note() -> None:
    actions = turn("capacity", [json.dumps(gold("capacity"))])
    note = actions[-1]
    assert note["type"] == "note"
    assert (note["decision"], note["intent"], note["agent_version"]) == (
        "Offer", "book", AGENT_VERSION,
    )  # fmt: skip


def test_the_note_reaches_the_runs_result_and_the_fixture_still_passes() -> None:
    verdicts = {f"criterion_{i}": {"reason": "ok", "pass": True} for i in (1, 2, 3)}
    judge = Fake(default=Response(content=json.dumps(verdicts)))
    llm = Fake(script=[Response(content=json.dumps(gold("capacity")))])
    r = Runner(llm=llm, judge=judge, decide=decide, handlers=handlers).run(ALL["capacity"]).runs[0]
    assert r.passed(), r.failures
    assert [e["type"] for e in r.effects] == ["speech", "note"]


def test_otel_rendering() -> None:
    rec = TurnRecord(
        telemetry_version=1, ts="2026-10-31T00:00:00Z", call_id="c", turn_id="t",
        agent_version="v", gate="none", intent="book", decision="Offer",
        action_types=("say", "note"), llm_calls=1, input_tokens=2000, output_tokens=150,
        latency_ms=1500, api_calls=(), fallback=False, transfer_reason="", phone_hash="h",
    )  # fmt: skip
    attrs = to_otel(rec)
    assert attrs["gen_ai.request.model"] == "gpt-4o-mini"
    assert (attrs["gen_ai.usage.input_tokens"], attrs["gen_ai.usage.output_tokens"]) == (2000, 150)
    assert attrs["pirtis.agent.version"] == "v" and attrs["pirtis.decision"] == "Offer"


def test_an_api_failure_is_handled_quietly(capsys: pytest.CaptureFixture[str]) -> None:
    faults = [Fault(on="bookings.create", nth=n, status=500) for n in (1, 2)]
    actions = turn("slot-stolen", [json.dumps(gold("slot-stolen"))], faults)
    assert "transfer" in [a["type"] for a in actions]
    assert capsys.readouterr().err == "", "nothing printed to the harness's stderr"
