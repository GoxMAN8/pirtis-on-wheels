"""pass^k from n runs, the unbiased way."""

from pirtis.ops.gate_report import RunRow
from pirtis.ops.reliability import curve, pass_k


def test_pass_k_is_the_chance_k_runs_in_a_row_pass() -> None:
    assert pass_k(5, 5, 3) == 1.0
    assert pass_k(4, 5, 1) == 0.8
    assert pass_k(4, 5, 5) == 0.0  # one failure in five: five in a row never all pass
    assert pass_k(3, 5, 2) == 0.3  # C(3,2) / C(5,2) = 3 / 10
    assert pass_k(2, 5, 3) == 0.0


def rows(suite: str, fixture: str, results: list[bool]) -> list[RunRow]:
    return [RunRow(suite, fixture, i + 1, ok, ok, True, 1, 1, 1, 1) for i, ok in enumerate(results)]


def test_a_flaky_case_pulls_the_curve_down_as_k_grows() -> None:
    data = rows("extra", "solid", [True] * 5) + rows(
        "extra", "flaky", [True, True, True, True, False]
    )
    c = curve(data, 5)
    assert [pt["pass_k"] for pt in c["extra"]] == [0.9, 0.8, 0.7, 0.6, 0.5]
    assert c["total"] == c["extra"]
