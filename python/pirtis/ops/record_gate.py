"""Saves a gate run's trace files as a GateRun record, so the admin console can show it.

    uv run python -m pirtis.ops.record_gate --dir runs/gate
    uv run python -m pirtis.ops.record_gate --dir runs/baseline --suite fixtures \\
        --agent-version naive-baseline

`make gate` runs the first after every gate. The agent version is read from the note
our agent leaves on every turn; traces from before notes existed need --agent-version.
The run time is the newest trace file's.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pirtis.ops.gate_report import load
from pirtis.ops.model import GateRun
from pirtis.ops.store import FileStore, store_from_env


def agent_version_of(results: list[Path]) -> str:
    """The agent version most turns in these results noted, or ""."""
    seen: Counter[str] = Counter()
    for p in results:
        for e in json.loads(p.read_text()).get("effects") or []:
            if e.get("type") == "note" and e.get("agent_version"):
                seen[str(e["agent_version"])] += 1
    return seen.most_common(1)[0][0] if seen else ""


def record(
    dir: Path, store: FileStore, *, suite: str = "", agent_version: str = ""
) -> GateRun | None:
    results = sorted(dir.glob("*/run-*/result.json" if suite else "*/*/run-*/result.json"))
    if not results:
        return None
    run = GateRun.from_rows(
        load(dir, suite or None),
        agent_version=agent_version or agent_version_of(results) or "unversioned",
        run_ts=datetime.fromtimestamp(max(p.stat().st_mtime for p in results), UTC),
    )
    store.put(run)
    return run


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="record-gate", description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="runs/gate")
    p.add_argument("--suite", default="", help="--dir is this one suite's folder")
    p.add_argument("--agent-version", default="", help="for traces without notes")
    args = p.parse_args(argv)
    run = record(
        Path(args.dir), store_from_env(), suite=args.suite, agent_version=args.agent_version
    )
    if run is None:
        print(f"no trace files under {args.dir}", file=sys.stderr)
        sys.exit(2)
    print(
        f"saved gate run {run.agent_version} {run.key()}: "
        f"{run.passed_cases}/{run.cases} cases passing all {run.k} runs, passed={run.passed}"
    )


if __name__ == "__main__":
    main()
