"""The gate report reads the harness's own trace files, so it is tested on trace files:
pass^k overall and split into deterministic checks and the judge, cost and latency."""

import json
from pathlib import Path
from typing import Any

from pirtis.ops.gate_report import load, summarise


def _run(
    root: Path,
    suite: str,
    fixture: str,
    run: int,
    failures: list[str] | None = None,
    calls: int = 1,
    wall_ms: int = 1500,
) -> None:
    d = root / suite / fixture / f"run-{run}"
    d.mkdir(parents=True)
    result: dict[str, Any] = {
        "fixture": fixture,
        "run": run,
        "passed": not failures,
        "llm": {"calls": calls, "prompt_tokens": 2000 * calls, "completion_tokens": 100 * calls},
        "wall_ms": wall_ms if calls else 0,
    }
    if failures:
        result["failures"] = failures
    (d / "result.json").write_text(json.dumps(result))


def test_pass_k_is_split_into_deterministic_checks_and_the_judge(tmp_path: Path) -> None:
    for r in (1, 2, 3):
        _run(tmp_path, "fixtures", "happy-path", r)
        _run(tmp_path, "fixtures", "silence", r, calls=0)
        _run(tmp_path, "extra", "nine-people", r, ["judge: x: y"] if r == 2 else None)
        _run(tmp_path, "extra", "cancel-booking", r, ["bookings: expected …"] if r == 3 else None)
    s = summarise(load(tmp_path))
    total = s["total"]
    assert (total["cases"], total["runs"], total["k"]) == (4, 12, 3)
    assert total["passed_cases"] == 2  # nine-people and cancel-booking each failed a run
    assert total["deterministic_passed_cases"] == 3  # only cancel-booking broke a check
    assert total["judge_passed_cases"] == 3  # only nine-people lost a judge verdict
    assert s["fixtures"]["passed_cases"] == 2 and s["extra"]["passed_cases"] == 0


def test_cost_and_latency(tmp_path: Path) -> None:
    for r in (1, 2):
        _run(tmp_path, "fixtures", "happy-path", r, wall_ms=1000 * r)
        _run(tmp_path, "fixtures", "silence", r, calls=0)
    total = summarise(load(tmp_path))["total"]
    assert (total["model_calls"], total["zero_call_runs"]) == (2, 2)
    assert (total["input_tokens"], total["output_tokens"]) == (4000, 200)
    assert total["est_cost_usd"] == round(4000 * 0.15e-6 + 200 * 0.6e-6, 4)
    assert (total["p50_ms"], total["p95_ms"]) == (1000, 2000)
