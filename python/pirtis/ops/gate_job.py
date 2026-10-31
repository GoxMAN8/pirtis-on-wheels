"""The gate job: the frozen gate in one process, for the nightly cloud job.

    gate-job                              the given cases and the red team, 3 runs (D-48)
    gate-job --suites all                 every suite, as `make gate`
    gate-job --id happy-path --no-extract one case, no extractor eval

It runs the kit's Runner over each case with the real agent, exactly as the harness
does, and then:
- saves a GateRun built from the gate report's own rows, so "passed" means pass^3 held,
  with the extractor's pass^k on the same cases' gold states beside it;
- saves every turn's TurnRecord with source "gate", which the admin console filters on.
  They go to the store only, never to a log stream: the fault cases make API errors on
  purpose, and the cloud's agent-errors alert reads the container's stdout and stderr;
- prunes every record past its retention;
- prints one JSON line, {"event": "gate", ...}, which the gate-regression alert reads,
  and exits 1 when the gate failed. Its llm_errors tells a model outage (a 429 when the
  credit runs out) apart from an agent that regressed.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.agent.version import AGENT_VERSION
from pirtis.api.client import Client as ApiClient
from pirtis.cmd.harness import load_dotenv
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.run import Runner
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import open_ai_from_env
from pirtis.ops.extract_eval import evaluate, load_gold
from pirtis.ops.gate_report import load
from pirtis.ops.model import GATE_K, GateRun, Turn
from pirtis.ops.store import FileStore, store_from_env
from pirtis.transcript.types import Action, Transcript

PYTHON = Path(__file__).resolve().parents[2]
FIXTURES = PYTHON.parent / "fixtures"
# named as `make gate` names them (the folder), so the console shows one history
SUITES = {"fixtures": FIXTURES, "extra": FIXTURES / "extra", "variants": FIXTURES / "variants"}
RED_TEAM = range(50, 60)  # fixtures/extra/50_… to 59_…: privacy and safety, never cut (D-45)

type Case = tuple[str, Fixture]  # (suite, fixture)


def _numbered(dir: Path) -> list[tuple[int | None, Fixture]]:
    """Each fixture with its file's number; the kit's loader reads the same files in the
    same order."""
    files = sorted(dir.glob("*.yaml"))
    numbers = [int(p.name[:2]) if p.name[:2].isdigit() else None for p in files]
    return list(zip(numbers, load_fixtures(dir), strict=True))


def select(which: str, ids: Sequence[str] = ()) -> list[Case]:
    """'nightly': the given cases and the red team; 'all': every suite, as make gate.
    ids, when given, keeps only those cases."""
    if which not in ("nightly", "all"):
        raise ValueError(f"unknown suites {which!r}: nightly or all")
    out: list[Case] = [("fixtures", f) for f in load_fixtures(SUITES["fixtures"])]
    out += [("extra", f) for n, f in _numbered(SUITES["extra"]) if which == "all" or n in RED_TEAM]
    if which == "all" and SUITES["variants"].is_dir():
        out += [("variants", f) for f in load_fixtures(SUITES["variants"])]
    return [c for c in out if not ids or c[1].id in ids]


class _Turns(logging.Handler):
    """Keeps the agent's telemetry line for each run, under a session id naming the run:
    a turn id repeats across the runs of one case, the session id doesn't."""

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix
        self.fixture = ""
        self.run = 0
        self.lines: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        session = f"{self.prefix}-{self.fixture}-r{self.run}"
        self.lines.append((session, record.getMessage()))


def run_job(
    cases: list[Case],
    llm: LlmClient,
    store: FileStore,
    *,
    trace_dir: Path,
    judge: LlmClient | None = None,
    extract: bool = True,
    which: str = "",
) -> dict[str, Any]:
    """Runs every case GATE_K times, saves what it found, and returns the summary line."""
    turns = _Turns(f"gate-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")

    def counted(t: Transcript, api: ApiClient, model: LlmClient) -> list[Action]:
        turns.run += 1
        return decide(t, api, model)

    runner = Runner(llm=llm, judge=judge, decide=counted, handlers=handlers)
    telemetry = logging.getLogger("pirtis.turn")
    telemetry.addHandler(turns)
    propagate, telemetry.propagate = telemetry.propagate, False  # no log stream sees them
    llm_errors = 0
    try:
        for suite, f in cases:
            runner.trace_dir = str(trace_dir / suite)
            turns.fixture, turns.run = f.id, 0
            result = runner.run(f, GATE_K, GATE_K)
            llm_errors += sum(r.stats.errors for r in result.runs)
    finally:
        telemetry.removeHandler(turns)
        telemetry.propagate = propagate
    for session, line in turns.lines:
        store.put(Turn.from_record(json.loads(line), source="gate", session_id=session))

    extracted: tuple[int, int] | None = None
    extract_error = ""
    ids = {f.id for _, f in cases}
    golds = [g for g in load_gold() if g.fixture in ids] if extract else []
    if golds:
        try:
            results = evaluate(llm, golds, GATE_K)
            extracted = (len(results), sum(r.passed() for r in results))
        except Exception as e:  # the model service failing; the gate itself has the verdict
            extract_error = str(e)[:200]

    run = GateRun.from_rows(
        load(trace_dir), agent_version=AGENT_VERSION, run_ts=datetime.now(UTC), extract=extracted
    )
    store.put(run)
    pruned = store.prune()

    def share(n: int, of: int) -> float:
        return round(n / of, 4) if of else 0.0

    return {
        "event": "gate",
        "passed": run.passed,
        "agent_version": run.agent_version,
        "suites": which,
        "run": run.key(),
        "cases": run.cases,
        "runs": run.runs,
        "k": run.k,
        "pass_k": share(run.passed_cases, run.cases),
        "det_pass_k": share(run.deterministic_passed_cases, run.cases),
        "judge_pass_k": share(run.judge_passed_cases, run.cases),
        "extract_pass_k": share(extracted[1], extracted[0]) if extracted else None,
        **({"extract_error": extract_error} if extract_error else {}),
        "failed": run.failed,
        "llm_errors": llm_errors,
        "model_calls": run.model_calls,
        "est_cost_usd": run.est_cost_usd,
        "p95_ms": run.p95_ms,
        "turns_saved": len(turns.lines),
        "pruned": pruned,
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gate-job", description=__doc__.splitlines()[0])
    p.add_argument(
        "--suites",
        choices=["nightly", "all"],
        default=os.environ.get("PIRTIS_GATE_SUITES", "nightly"),
        help="nightly: the given cases and the red team; all: every suite",
    )
    p.add_argument("--id", default="", help="comma-separated case ids; default every case")
    p.add_argument("--no-extract", action="store_true", help="skip the extractor eval")
    p.add_argument("--trace", default="", help="keep the trace files here; default a temp dir")
    args = p.parse_args(argv)

    if args.trace and Path(args.trace).is_dir() and any(Path(args.trace).iterdir()):
        print(f"{args.trace} is not empty; old results would join this run", file=sys.stderr)
        sys.exit(2)
    load_dotenv(str(PYTHON / ".env"))
    try:
        llm = open_ai_from_env()
    except RuntimeError as e:
        print(f"{e}\nset LLM_API_KEY, and optionally LLM_BASE_URL", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    cases = select(args.suites, [s.strip() for s in args.id.split(",") if s.strip()])
    if not cases:
        print("no cases matched", file=sys.stderr)
        sys.exit(2)

    with tempfile.TemporaryDirectory(prefix="gate-") as tmp:
        line = run_job(
            cases,
            llm,
            store_from_env(),
            trace_dir=Path(args.trace or tmp),
            extract=not args.no_extract,
            which=args.suites,
        )
    print(json.dumps(line), flush=True)
    sys.exit(0 if line["passed"] else 1)


if __name__ == "__main__":
    main()
