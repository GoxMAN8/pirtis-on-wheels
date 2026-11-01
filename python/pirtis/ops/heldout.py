"""The held-out score: the generated variants, run once, by the transform that made them.

The frozen gate is also the set the agent was developed on, so its 46/46 is a
development score. The variants (pirtis/ops/variants.py) were made by code, checked by
the kit's API, and never used to change the agent: scored once, before anything is tuned
on them, they say how the agent does on callers, dates, cars and speech it has not seen.
Split by transform, they also say which kind of change it copes with worst: the noise
ones are the 14 calls written as speech-to-text hears them.

Once a variant has been used to fix something, it is development data too; the next
held-out score needs a fresh batch (make variants).

    uv run python -m pirtis.ops.heldout --dir runs/heldout [--json runs/heldout/summary.json]
"""

import argparse
import json
from pathlib import Path
from typing import Any

from pirtis.ops.gate_report import RunRow, load


def transform(fixture: str) -> str:
    """The transform in a variant's id: happy-path--persona-cus_12 -> persona."""
    tail = fixture.rsplit("--", 1)[-1] if "--" in fixture else "unknown"
    if tail.startswith("persona"):
        return "persona"
    if tail.startswith("shift"):
        return "shift"
    return tail


def by_transform(rows: list[RunRow]) -> dict[str, dict[str, Any]]:
    """Per transform and in total: cases passing every run, split as the gate splits."""
    cases: dict[str, list[RunRow]] = {}
    for r in rows:
        cases.setdefault(r.fixture, []).append(r)
    groups: dict[str, list[list[RunRow]]] = {}
    for fixture, runs in cases.items():
        groups.setdefault(transform(fixture), []).append(runs)
    groups["total"] = list(cases.values())
    return {
        name: {
            "cases": len(v),
            "passed": sum(all(r.passed for r in runs) for runs in v),
            "deterministic": sum(all(r.deterministic_ok for r in runs) for runs in v),
            "judge": sum(all(r.judge_ok for r in runs) for runs in v),
            "failed": sorted(runs[0].fixture for runs in v if not all(r.passed for r in runs)),
        }
        for name, v in sorted(groups.items(), key=lambda kv: (kv[0] == "total", kv[0]))
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="heldout", description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="runs/heldout")
    p.add_argument("--json", default="")
    args = p.parse_args(argv)
    s = by_transform(load(Path(args.dir)))
    print("\nHELD-OUT (generated variants, never tuned on): cases passing every run")
    for name, b in s.items():
        print(
            f"  {name:<9} {b['passed']:>2}/{b['cases']:<2}  deterministic {b['deterministic']}"
            f"  judge {b['judge']}" + (f"  failed: {', '.join(b['failed'])}" if b["failed"] else "")
        )
    if args.json:
        Path(args.json).write_text(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
