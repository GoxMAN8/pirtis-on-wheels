"""The gate report: what a gate run says, from the harness's own trace files.

For each suite and in total: cases and runs; pass^k (every run of a case passed), and
the same split into the deterministic checks (action, bookings, API invariants) and the
judge's verdicts; model calls, tokens and estimated cost (the agent's calls only; the
judge's are not in the traces); and the latency of turns that called the model.

    uv run python -m pirtis.ops.gate_report --dir runs/gate [--json runs/gate/summary.json]
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PRICE_IN = 0.15e-6  # gpt-4o-mini, USD per input token
PRICE_OUT = 0.6e-6  # per output token


@dataclass(frozen=True)
class RunRow:
    suite: str
    fixture: str
    run: int
    passed: bool
    deterministic_ok: bool
    judge_ok: bool
    calls: int
    input_tokens: int
    output_tokens: int
    wall_ms: int


def load(dir: Path) -> list[RunRow]:
    """One row per run from <dir>/<suite>/<fixture>/run-<n>/result.json."""
    rows = []
    for p in sorted(dir.glob("*/*/run-*/result.json")):
        r = json.loads(p.read_text())
        failures = [str(f) for f in r.get("failures") or []]
        judge = [f for f in failures if f.startswith("judge:")]
        llm = r.get("llm") or {}
        rows.append(
            RunRow(
                suite=p.parents[2].name,
                fixture=str(r.get("fixture", p.parents[1].name)),
                run=int(r.get("run", 0)),
                passed=bool(r.get("passed")),
                deterministic_ok=not r.get("error") and len(judge) == len(failures),
                judge_ok=not judge,
                calls=int(llm.get("calls", 0)),
                input_tokens=int(llm.get("prompt_tokens", 0)),
                output_tokens=int(llm.get("completion_tokens", 0)),
                wall_ms=int(r.get("wall_ms", 0)),
            )
        )
    return rows


def _block(rows: list[RunRow]) -> dict[str, Any]:
    cases: dict[tuple[str, str], list[RunRow]] = {}
    for row in rows:
        cases.setdefault((row.suite, row.fixture), []).append(row)
    walls = sorted(r.wall_ms for r in rows if r.calls > 0)

    def pct(p: float) -> int:
        return walls[min(len(walls) - 1, round(p * (len(walls) - 1)))] if walls else 0

    tin = sum(r.input_tokens for r in rows)
    tout = sum(r.output_tokens for r in rows)
    return {
        "cases": len(cases),
        "runs": len(rows),
        "k": max((len(v) for v in cases.values()), default=0),
        "passed_cases": sum(all(r.passed for r in v) for v in cases.values()),
        "deterministic_passed_cases": sum(
            all(r.deterministic_ok for r in v) for v in cases.values()
        ),
        "judge_passed_cases": sum(all(r.judge_ok for r in v) for v in cases.values()),
        "failed": sorted(f for (_, f), v in cases.items() if not all(r.passed for r in v)),
        "model_calls": sum(r.calls for r in rows),
        "zero_call_runs": sum(r.calls == 0 for r in rows),
        "input_tokens": tin,
        "output_tokens": tout,
        "est_cost_usd": round(tin * PRICE_IN + tout * PRICE_OUT, 4),
        "p50_ms": pct(0.5),
        "p95_ms": pct(0.95),
    }


def summarise(rows: list[RunRow]) -> dict[str, dict[str, Any]]:
    suites = sorted({r.suite for r in rows})
    out = {suite: _block([r for r in rows if r.suite == suite]) for suite in suites}
    out["total"] = _block(rows)
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gate-report", description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="runs/gate")
    p.add_argument("--json", default="")
    args = p.parse_args(argv)
    s = summarise(load(Path(args.dir)))
    t = s["total"]
    print(
        f"\nGATE: {t['passed_cases']}/{t['cases']} cases passed all {t['k']} runs "
        f"({t['runs']} runs)"
    )
    for name, b in s.items():
        print(
            f"  {name:<9} {b['passed_cases']:>2}/{b['cases']:<2} cases  "
            f"deterministic {b['deterministic_passed_cases']}/{b['cases']}  "
            f"judge {b['judge_passed_cases']}/{b['cases']}  "
            f"model calls {b['model_calls']} (0 on {b['zero_call_runs']} runs)  "
            f"tokens {b['input_tokens']}/{b['output_tokens']}  ≈ ${b['est_cost_usd']:.4f}  "
            f"p50 {b['p50_ms'] / 1000:.2f}s p95 {b['p95_ms'] / 1000:.2f}s"
        )
    if t["failed"]:
        print("  failed: " + ", ".join(t["failed"]))
    print("  (cost is the agent's model calls only; the judge's calls are not traced)")
    if args.json:
        Path(args.json).write_text(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
