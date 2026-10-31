"""pass^k: the chance that k runs of a case in a row all pass, per suite, from n runs.

For a case with c passes in n runs, C(c, k) / C(n, k) is an unbiased estimate of pass^k
(the estimator tau-bench uses); a suite's pass^k is the mean over its cases. k = 1 is the
plain pass rate. How far the curve falls from k = 1 to k = n shows how much a single
lucky run would hide; the frozen gate asks for pass^3 = 1.0 (D-21).

    uv run python -m pirtis.ops.reliability --dir runs/reliability [--json curve.json]
"""

import argparse
import json
from math import comb
from pathlib import Path

from pirtis.ops.gate_report import RunRow, load


def pass_k(passes: int, runs: int, k: int) -> float | None:
    return comb(passes, k) / comb(runs, k) if 0 < k <= runs else None


def curve(rows: list[RunRow], k_max: int) -> dict[str, list[dict[str, float | int | None]]]:
    """Per suite and in total: pass^k for k = 1..k_max, over the cases with k_max runs."""
    cases: dict[tuple[str, str], list[bool]] = {}
    for r in rows:
        cases.setdefault((r.suite, r.fixture), []).append(r.passed)
    suites = sorted({s for s, _ in cases}) + ["total"]
    out: dict[str, list[dict[str, float | int | None]]] = {}
    for suite in suites:
        runs = [v for (s, _), v in cases.items() if suite in (s, "total") and len(v) >= k_max]
        out[suite] = [
            {
                "k": k,
                "cases": len(runs),
                "pass_k": round(sum(pass_k(sum(v), len(v), k) or 0 for v in runs) / len(runs), 4)
                if runs
                else None,
            }
            for k in range(1, k_max + 1)
        ]
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="reliability", description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="runs/reliability")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--json", default="")
    args = p.parse_args(argv)
    c = curve(load(Path(args.dir)), args.k)
    print(f"pass^k for k = 1..{args.k}")
    for suite, points in c.items():
        cells = "  ".join(f"k={pt['k']}: {pt['pass_k']}" for pt in points)
        print(f"  {suite:<22} {points[0]['cases']:>3} cases  {cells}")
    if args.json:
        Path(args.json).write_text(json.dumps(c, indent=2))


if __name__ == "__main__":
    main()
