"""The held-out score is split by the transform that made each variant."""

from pirtis.ops.gate_report import RunRow
from pirtis.ops.heldout import by_transform, transform


def row(fixture: str, run: int, passed: bool = True, judge_ok: bool = True) -> RunRow:
    det = passed or not judge_ok
    return RunRow("variants-candidates", fixture, run, passed, det, judge_ok, 1, 1, 1, 1)


def test_the_transform_is_read_from_the_id() -> None:
    assert transform("happy-path--persona-cus_12") == "persona"
    assert transform("capacity--shift14") == "shift"
    assert transform("silence--noise") == "noise"
    assert transform("tow-weight--tow") == "tow"
    assert transform("happy-path") == "unknown"


def test_a_case_passes_only_if_every_run_does() -> None:
    rows = [
        *[row("silence--noise", r) for r in (1, 2, 3)],
        row("capacity--noise", 1),
        row("capacity--noise", 2, passed=False, judge_ok=False),  # the judge only
        *[row("power--tow", r) for r in (1, 2, 3)],
    ]
    s = by_transform(rows)
    assert s["noise"] == {
        "cases": 2, "passed": 1, "deterministic": 2, "judge": 1, "failed": ["capacity--noise"]
    }  # fmt: skip
    assert s["tow"]["passed"] == 1
    assert s["total"]["cases"] == 3 and s["total"]["passed"] == 2
    assert list(s)[-1] == "total"
