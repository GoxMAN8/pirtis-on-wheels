"""A deploy asks whether the frozen gate holds for the agent as it stands: the newest
saved run that covered every case, for this agent version, must have passed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pirtis.ops.gate_check import holds
from pirtis.ops.gate_report import RunRow
from pirtis.ops.model import GateRun
from pirtis.ops.store import FileStore

CASES = {("fixtures", "happy-path"), ("fixtures", "silence"), ("extra", "nine-people")}
T0 = datetime(2026, 10, 31, 10, tzinfo=UTC)


def run(version: str, at: datetime, cases: set[tuple[str, str]], failing: str = "") -> GateRun:
    rows = [
        RunRow(suite, fixture, n, fixture != failing, True, fixture != failing, 1, 2000, 100, 900)
        for suite, fixture in sorted(cases)
        for n in (1, 2, 3)
    ]
    return GateRun.from_rows(rows, agent_version=version, run_ts=at)


def test_nothing_saved_means_the_gate_must_run(tmp_path: Path) -> None:
    assert holds(FileStore(tmp_path), "v1", CASES) is None


def test_a_passing_run_over_every_case_holds(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(run("v1", T0, CASES))
    found = holds(store, "v1", CASES)
    assert found is not None and found.passed


def test_a_run_that_skipped_cases_does_not_count(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(run("v1", T0, CASES - {("extra", "nine-people")}))  # the nightly subset, say
    assert holds(store, "v1", CASES) is None


def test_the_newest_full_run_decides(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(run("v1", T0, CASES))
    store.put(run("v1", T0 + timedelta(hours=1), CASES, failing="silence"))
    assert holds(store, "v1", CASES) is None


def test_another_versions_pass_proves_nothing(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(run("v0", T0, CASES))
    assert holds(store, "v1", CASES) is None
