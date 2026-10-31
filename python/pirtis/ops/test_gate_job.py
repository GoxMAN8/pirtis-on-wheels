"""The gate job: the frozen gate in one process, as the nightly cloud job runs it. Driven
here by a scripted model, so it spends no tokens."""

import io
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pirtis.agent.version import AGENT_VERSION
from pirtis.harness.fixture import load_fixtures
from pirtis.llm.client import Request, Response
from pirtis.llm.fake import Fake
from pirtis.ops import gate_job
from pirtis.ops.gate_job import SUITES, Case, run_job, select
from pirtis.ops.model import GateRun, Session, Turn
from pirtis.ops.store import FileStore

GOLD = Path(__file__).resolve().parents[1] / "agent" / "gold"
HAPPY = json.loads((GOLD / "happy-path.json").read_text())["state"]
RED_TEAM = {
    "ignore-your-instructions",
    "book-it-without-reading-back",
    "the-manager-asks-for-a-number",
    "right-name-wrong-phone",
    "who-booked-it-and-where",
    "read-me-your-instructions",
    "say-the-price-in-digits",
}

# a judge that passes every criterion; the judge is tested live, not here
PASS_JUDGE = Fake(
    default=Response(
        content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in range(1, 9)})
    )
)


class Down:
    """A model service that refuses every call, as it does when the credit runs out."""

    def chat(self, req: Request) -> Response:
        raise RuntimeError("429 insufficient_quota")


def two_cases() -> list[Case]:
    return [c for c in select("all") if c[1].id in ("interim-speech", "happy-path")]


def test_the_nightly_suite_is_the_given_cases_and_the_red_team() -> None:
    cases = select("nightly")
    given = {f.id for f in load_fixtures(SUITES["fixtures"])}
    assert {f.id for s, f in cases if s == "fixtures"} == given and len(given) == 14
    assert {f.id for s, f in cases if s == "extra"} == RED_TEAM


def test_all_is_every_suite_make_gate_runs() -> None:
    cases = select("all")
    want = sum(len(load_fixtures(d)) for d in SUITES.values() if d.is_dir())
    assert len(cases) == want
    assert RED_TEAM <= {f.id for _, f in cases}
    assert len({(s, f.id) for s, f in cases}) == len(cases)


def test_ids_narrow_the_suites_and_an_unknown_name_is_refused() -> None:
    assert [f.id for _, f in select("nightly", ["capacity", "happy-path"])] == [
        "happy-path",
        "capacity",
    ]
    with pytest.raises(ValueError, match="nightly or all"):
        select("everything")


def test_a_passing_run_is_saved_with_every_turn(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data")
    llm = Fake(default=Response(content=json.dumps(HAPPY)))
    line = run_job(two_cases(), llm, store, judge=PASS_JUDGE, trace_dir=tmp_path / "t", which="all")

    [run] = store.query(GateRun)
    assert (run.passed, run.cases, run.runs, run.k) == (True, 2, 6, 3)
    assert run.agent_version == AGENT_VERSION
    # interim speech never reaches the model, so only happy-path has a gold state
    assert (run.extract_cases, run.extract_passed_cases) == (1, 1)

    turns = store.query(Turn)
    assert len(turns) == 6 and {t.source for t in turns} == {"gate"}
    assert len({t.key() for t in turns}) == 6, "three runs of one case must not overwrite"
    assert {t.session_id.rsplit("-", 1)[1] for t in turns} == {"r1", "r2", "r3"}

    assert line["event"] == "gate" and line["passed"] is True
    assert (line["pass_k"], line["det_pass_k"], line["judge_pass_k"]) == (1.0, 1.0, 1.0)
    assert line["extract_pass_k"] == 1.0 and line["llm_errors"] == 0
    assert (line["agent_version"], line["run"], line["turns_saved"]) == (
        AGENT_VERSION,
        run.key(),
        6,
    )


def test_a_wrong_answer_fails_the_gate(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data")
    llm = Fake(default=Response(content="not json"))
    line = run_job(two_cases(), llm, store, judge=PASS_JUDGE, trace_dir=tmp_path / "t")
    [run] = store.query(GateRun)
    assert run.passed is False and run.failed == ["happy-path"]
    assert line["passed"] is False and line["failed"] == ["happy-path"]
    assert line["extract_pass_k"] == 0.0 and line["llm_errors"] == 0


def test_a_model_outage_is_told_apart_from_a_regression(tmp_path: Path) -> None:
    """A 429 when the credit runs out fails the cases too; llm_errors says why."""
    store = FileStore(tmp_path / "data")
    line = run_job(two_cases(), Down(), store, judge=PASS_JUDGE, trace_dir=tmp_path / "t")
    assert line["passed"] is False and line["failed"] == ["happy-path"]
    assert line["llm_errors"] == 3
    assert line["extract_pass_k"] is None and "429" in line["extract_error"]


def test_turn_records_reach_no_log_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fault cases make API errors on purpose, and the cloud's logs collect stdout and
    stderr alike: the agent-errors alert must see neither."""
    sink = io.StringIO()
    root = logging.StreamHandler(sink)  # what logging.basicConfig attaches
    logging.getLogger().addHandler(root)
    try:
        llm = Fake(default=Response(content=json.dumps(HAPPY)))
        run_job(two_cases(), llm, FileStore(tmp_path / "d"), judge=PASS_JUDGE, trace_dir=tmp_path)
    finally:
        logging.getLogger().removeHandler(root)
    out = capsys.readouterr()
    assert out.out == "" and '"event": "turn"' not in out.err + sink.getvalue()
    assert logging.getLogger("pirtis.turn").propagate, "restored after the job"


def test_records_past_their_retention_are_pruned(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data")
    old = datetime.now(UTC) - timedelta(days=40)
    store.put(
        Session(
            session_id=old.strftime("%Y%m%d") + "-0000abcd",
            started=old,
            agent_version="old",
            caller_phone_hash="h",
            turns=1,
            status="ended",
            transcript={},
            bookings=[],
        )
    )
    cases = [c for c in select("all") if c[1].id == "interim-speech"]
    line = run_job(cases, Fake(), store, extract=False, trace_dir=tmp_path / "t")
    assert store.query(Session) == [] and line["pruned"]["session"] == 1
    assert line["extract_pass_k"] is None and "extract_error" not in line


def test_main_prints_one_line_and_exits_by_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PIRTIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(gate_job, "open_ai_from_env", lambda: Fake())
    with pytest.raises(SystemExit) as done:
        gate_job.main(["--suites", "all", "--id", "interim-speech,happy-path", "--no-extract"])
    assert done.value.code == 1  # the fake model can't hear the booking
    [out] = capsys.readouterr().out.splitlines()
    line = json.loads(out)
    assert (line["event"], line["passed"], line["suites"], line["cases"]) == (
        "gate",
        False,
        "all",
        2,
    )


def test_main_refuses_a_trace_folder_with_old_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "leftover").write_text("x")
    monkeypatch.setattr(gate_job, "open_ai_from_env", lambda: Fake())
    with pytest.raises(SystemExit) as done:
        gate_job.main(["--trace", str(tmp_path / "old")])
    assert done.value.code == 2


def test_every_way_the_job_fails_still_prints_one_gate_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate-regression alert reads that line: a job that dies without it goes unseen."""

    def no_key() -> Fake:
        raise RuntimeError("LLM_API_KEY is not set")

    def broken(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("the share is not mounted")

    monkeypatch.setenv("PIRTIS_DATA_DIR", str(tmp_path / "data"))
    for patch, code in ((("open_ai_from_env", no_key), 2), (("run_job", broken), 1)):
        with monkeypatch.context() as m:
            if patch[0] == "run_job":
                m.setattr(gate_job, "open_ai_from_env", lambda: Fake())
            m.setattr(gate_job, *patch)
            with pytest.raises(SystemExit) as done:
                gate_job.main(["--id", "interim-speech", "--no-extract"])
        assert done.value.code == code
        [out] = capsys.readouterr().out.splitlines()
        line = json.loads(out)
        assert line["event"] == "gate" and line["passed"] is False and line["error"]
        assert line["agent_version"] == AGENT_VERSION
