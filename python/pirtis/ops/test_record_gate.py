"""A gate run's traces become one stored record, with the agent version its turns noted."""

import json
from pathlib import Path

from pirtis.ops.model import GateRun
from pirtis.ops.record_gate import record
from pirtis.ops.store import FileStore


def _trace(d: Path, fixture: str, run: int, version: str | None) -> None:
    d = d / fixture / f"run-{run}"
    d.mkdir(parents=True)
    effects = [{"type": "note", "agent_version": version}] if version else []
    result = {"fixture": fixture, "run": run, "passed": True, "effects": effects}
    (d / "result.json").write_text(json.dumps({**result, "llm": {"calls": 1}, "wall_ms": 900}))


def test_a_gate_run_is_saved_under_the_version_its_turns_noted(tmp_path: Path) -> None:
    for fixture in ("happy-path", "silence"):
        for n in (1, 2, 3):
            _trace(tmp_path / "runs" / "fixtures", fixture, n, "abc123")
    store = FileStore(tmp_path / "data")
    run = record(tmp_path / "runs", store)
    assert run is not None and (run.agent_version, run.passed, run.cases) == ("abc123", True, 2)
    assert store.query(GateRun) == [run]


def test_traces_without_notes_take_the_version_given(tmp_path: Path) -> None:
    for fixture in ("happy-path", "silence"):
        _trace(tmp_path / "baseline", fixture, 1, None)
    store = FileStore(tmp_path / "data")
    run = record(tmp_path / "baseline", store, suite="fixtures", agent_version="naive")
    assert run is not None and (run.agent_version, run.k, run.passed) == ("naive", 1, False)
    assert {r.suite for r in run.rows} == {"fixtures"}


def test_no_traces_saves_nothing(tmp_path: Path) -> None:
    assert record(tmp_path, FileStore(tmp_path / "data")) is None
