"""The data layer: five versioned records, each with a privacy class on every field, kept
as one JSON file per record so two containers can share the folder safely."""

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pirtis.agent.agent import decide
from pirtis.api.memory import Memory, load_seed
from pirtis.harness.fixture import load_fixtures
from pirtis.llm.fake import Fake
from pirtis.ops.gate_report import RunRow
from pirtis.ops.model import (
    ENTITIES,
    PII,
    Calibration,
    Entity,
    Feedback,
    GateRun,
    HumanRating,
    Session,
    Turn,
)
from pirtis.ops.store import FileStore, store_from_env

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def session(i: int = 1, started: datetime = NOW) -> Session:
    return Session(
        session_id=f"s{i}",
        started=started,
        agent_version="v1",
        caller_phone_hash="abc",
        turns=3,
        status="open",
        transcript={"items": []},
        bookings=[],
    )


def turn(i: int = 1, ts: datetime = NOW, session_id: str = "s1") -> Turn:
    return Turn(
        turn_id=f"t{i}",
        ts=ts,
        source="playground",
        session_id=session_id,
        call_id="c",
        agent_version="v1",
        telemetry_version=1,
        gate="none",
        intent="book",
        decision="Offer",
        action_types=["say", "note"],
        llm_calls=1,
        input_tokens=2000,
        output_tokens=150,
        latency_ms=1500,
        api_calls=[],
        fallback=False,
        transfer_reason="",
        phone_hash="abc",
    )


SAMPLES: list[Entity] = [
    session(),
    Feedback(
        turn_id="t1",
        session_id="s1",
        agent_version="v1",
        verdict="down",
        note="too long",
        transcript={"items": []},
        created=NOW,
    ),
    HumanRating(
        scenario_id="sc1",
        session_id="s1",
        rater="GS",
        agent_version="v1",
        goal_achieved=True,
        natural=4,
        numbers_clear=5,
        one_question=5,
        turn_taking=4,
        note="",
        created=NOW,
    ),
    GateRun(
        run_ts=NOW,
        agent_version="v1",
        passed=True,
        k=3,
        cases=24,
        runs=72,
        passed_cases=24,
        deterministic_passed_cases=24,
        judge_passed_cases=24,
        model_calls=60,
        zero_call_runs=12,
        input_tokens=121110,
        output_tokens=8234,
        est_cost_usd=0.0231,
        p50_ms=1550,
        p95_ms=1790,
        failed=[],
    ),
    turn(),
    Calibration(
        run_ts=NOW,
        judge_model="gpt-4o-mini",
        ratings=12,
        items={"natural": {"n": 12, "agree": 0.75, "kappa": 0.41}},
        overall_agree=0.75,
        overall_kappa=0.41,
        rater_pairs=3,
        rater_kappa=0.5,
    ),
]


def test_the_kinds_of_record() -> None:
    kinds = {"session", "feedback", "rating", "gate_run", "turn", "calibration"}
    assert {e.KIND for e in ENTITIES} == kinds
    assert {type(s) for s in SAMPLES} == set(ENTITIES)


@pytest.mark.parametrize("cls", ENTITIES, ids=lambda c: c.KIND)
def test_every_field_has_a_privacy_class(cls: type[Entity]) -> None:
    for name, f in cls.model_fields.items():
        extra = f.json_schema_extra
        assert isinstance(extra, dict) and extra.get("pii") in PII, f"{cls.KIND}.{name}"


def test_content_is_only_where_the_spec_says() -> None:
    personal = {
        cls.KIND: {
            n
            for n, f in cls.model_fields.items()
            if isinstance(f.json_schema_extra, dict) and f.json_schema_extra["pii"] == "personal"
        }
        for cls in ENTITIES
    }
    assert personal == {
        "session": {"transcript", "bookings", "steps"},
        "feedback": {"transcript", "note"},
        "rating": set(),
        "gate_run": set(),
        "turn": set(),
        "calibration": set(),
    }


@pytest.mark.parametrize("e", SAMPLES, ids=lambda e: e.KIND)
def test_round_trip(e: Entity, tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    path = store.put(e)
    assert path.is_relative_to(tmp_path / e.KIND)
    assert store.get(type(e), e.partition(), e.key()) == e
    assert json.loads(path.read_text())["schema_version"] == 1


def test_query_by_partition_and_date(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    for i, days_ago in enumerate([0, 1, 5, 40]):
        store.put(session(i, NOW - timedelta(days=days_ago)))
    assert len(store.query(Session)) == 4
    assert [s.session_id for s in store.query(Session, since=NOW - timedelta(days=2))] == [
        "s1",
        "s0",
    ]  # oldest first
    today = (NOW).date().isoformat()
    assert [s.session_id for s in store.query(Session, partition=today)] == ["s0"]


def test_a_date_query_never_opens_older_day_folders(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(turn(1, NOW))
    old = tmp_path / "turn" / "2026-01-01"
    old.mkdir()
    (old / "t9.json").write_text("{not json")  # reading it would raise
    assert [t.turn_id for t in store.query(Turn, since=NOW - timedelta(days=1))] == ["t1"]


def test_prune_keeps_each_kind_for_its_retention(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(session(1, NOW - timedelta(days=29)))
    store.put(session(2, NOW - timedelta(days=31)))
    store.put(turn(1, NOW - timedelta(days=89)))
    store.put(turn(2, NOW - timedelta(days=91)))
    removed = store.prune(now=NOW)
    assert removed["session"] == 1 and removed["turn"] == 1
    assert [s.session_id for s in store.query(Session)] == ["s1"]
    assert [t.turn_id for t in store.query(Turn)] == ["t1"]


def test_a_failed_write_never_leaves_a_half_written_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileStore(tmp_path)
    store.put(session(1))
    before = store.get(Session, session(1).partition(), "s1")

    def broken(src: Any, dst: Any) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        store.put(session(1).model_copy(update={"turns": 99}))
    monkeypatch.undo()
    assert store.get(Session, session(1).partition(), "s1") == before
    assert not list(tmp_path.rglob("*.tmp"))


def test_stray_temp_files_are_never_read(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(turn(1))
    (tmp_path / "turn" / turn(1).partition() / "t2.json.x.tmp").write_text("{half")
    assert [t.turn_id for t in store.query(Turn)] == ["t1"]


def test_two_writers_at_once_lose_nothing(tmp_path: Path) -> None:
    """The playground and the gate job share one folder; no shared file means no clash."""
    store = FileStore(tmp_path)

    def write(prefix: str) -> None:
        for i in range(100):
            store.put(turn(i).model_copy(update={"turn_id": f"{prefix}{i}"}))

    threads = [threading.Thread(target=write, args=(p,)) for p in ("a", "b")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(store.query(Turn)) == 200


def test_keys_cannot_escape_the_data_folder(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data")
    path = store.put(turn().model_copy(update={"turn_id": "../../../etc/passwd"}))
    assert path.resolve().is_relative_to((tmp_path / "data").resolve())


def test_the_folder_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIRTIS_DATA_DIR", str(tmp_path))
    assert store_from_env().root == tmp_path


def test_a_turn_is_stored_from_the_agents_own_telemetry_record() -> None:
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    keep = Keep()
    logging.getLogger("pirtis.turn").addHandler(keep)
    try:
        f = next(x for x in load_fixtures(FIXTURES) if x.id == "interim-speech")
        decide(f.transcript, Memory(load_seed(f.seed_path())), Fake())
    finally:
        logging.getLogger("pirtis.turn").removeHandler(keep)
    t = Turn.from_record(json.loads(lines[0]), source="gate", session_id="gate-1")
    assert (t.gate, t.source, t.llm_calls) == ("wait", "gate", 0)


def row(fixture: str, run: int, passed: bool = True) -> RunRow:
    return RunRow("fixtures", fixture, run, passed, passed, True, 1, 2000, 150, 1500)


def test_a_gate_run_holds_only_when_every_case_passes_all_three_runs() -> None:
    rows = [row(f, r) for f in ("happy-path", "capacity") for r in (1, 2, 3)]
    g = GateRun.from_rows(rows, agent_version="v1", run_ts=NOW)
    assert (g.passed, g.cases, g.runs, g.k, g.failed) == (True, 2, 6, 3, [])
    assert len(g.rows) == 6

    flaky = rows[:-1] + [row("capacity", 3, passed=False)]
    g = GateRun.from_rows(flaky, agent_version="v1", run_ts=NOW)
    assert (g.passed, g.failed) == (False, ["capacity"])

    once = [row("happy-path", 1)]
    assert not GateRun.from_rows(once, agent_version="v1", run_ts=NOW).passed  # k < 3
    assert not GateRun.from_rows([], agent_version="v1", run_ts=NOW).passed


def test_the_gates_three_runs_of_one_turn_are_three_records(tmp_path: Path) -> None:
    """The agent's turn id is the same on every run of a fixture; the session is not."""
    store = FileStore(tmp_path)
    for run in (1, 2, 3):
        store.put(turn(1, session_id=f"gate-happy-path-{run}"))
    assert len(store.query(Turn)) == 3


def test_no_file_name_carries_a_colon(tmp_path: Path) -> None:
    """Azure Files refuses ':' in a name, and timestamps are full of them."""
    store = FileStore(tmp_path)
    for e in SAMPLES:
        store.put(e)
    store.put(turn(2, session_id="2026-10-31T12:00:00+00:00"))
    assert all(":" not in p.name for p in tmp_path.rglob("*"))


def test_a_record_with_an_unknown_field_or_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError):
        Session.model_validate({**session().model_dump(), "caller_name": "Jonas"})
    with pytest.raises(ValueError):
        session(started=datetime(2026, 10, 31, 12, 0))


def test_an_empty_or_dotted_key_names_no_folder(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    with pytest.raises(ValueError):
        store.put(session().model_copy(update={"session_id": ""}))
    path = store.put(turn(session_id=".."))
    assert path.parent == tmp_path / "turn" / turn().partition()
