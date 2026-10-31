"""Does the frozen gate hold for the agent as it stands? A deploy asks first (D-21).

    uv run python -m pirtis.ops.gate_check

Exit 0 when the newest saved gate run for this agent_version that covered every case of
every suite passed; exit 1 otherwise, and `make deploy` then runs the full gate. A
nightly run covers only some cases, so it never counts. Any change to the agent changes
its version, so a pass for an older version proves nothing.
"""

import sys

from pirtis.agent.version import AGENT_VERSION
from pirtis.ops.gate_job import select
from pirtis.ops.model import GateRun
from pirtis.ops.store import FileStore, store_from_env


def holds(store: FileStore, version: str, cases: set[tuple[str, str]]) -> GateRun | None:
    """The newest full run for this version, if it passed; None if there is none or it
    failed."""
    full = [
        r
        for r in store.query(GateRun, partition=version)
        if cases <= {(row.suite, row.fixture) for row in r.rows}
    ]
    return full[-1] if full and full[-1].passed else None


def main() -> None:
    cases = {(suite, f.id) for suite, f in select("all")}
    run = holds(store_from_env(), AGENT_VERSION, cases)
    if run is None:
        print(f"no passing gate run over all {len(cases)} cases for agent {AGENT_VERSION}")
        sys.exit(1)
    print(
        f"the gate holds for agent {AGENT_VERSION}: {run.passed_cases}/{run.cases} cases, "
        f"{run.runs} runs, saved {run.key()}"
    )


if __name__ == "__main__":
    main()
