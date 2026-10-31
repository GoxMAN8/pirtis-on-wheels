"""The eval is only trustworthy if its gold and its comparison are: every gold file is
a valid call state for a real fixture, matches itself, and a wrong field is caught."""

import json

import pytest

from pirtis.agent.understand import CallState
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.ops.extract_eval import Gold, compare, evaluate, field_accuracy, fixture_for, load_gold

GOLDS = load_gold()


def test_there_is_gold_for_every_fixture_the_model_sees() -> None:
    assert len(GOLDS) == 20
    assert {g.suite for g in GOLDS} == {"given", "extra"}


@pytest.mark.parametrize("g", GOLDS, ids=lambda g: g.fixture)
def test_gold_is_a_valid_state_for_a_real_fixture(g: Gold) -> None:
    fixture_for(g)
    state = CallState.model_validate(g.state)
    for name in g.ignore + [k for group in g.one_of for alt in group for k in alt]:
        assert name in g.state, name
    assert compare(state, g, []) == []


def _gold(fid: str) -> Gold:
    return next(g for g in GOLDS if g.fixture == fid)


def test_a_wrong_field_is_caught() -> None:
    g = _gold("weekend-minimum")
    applied_the_rule_itself = CallState.model_validate({**g.state, "end": "2026-10-11"})
    assert compare(applied_the_rule_itself, g, []) == ["end"]


def test_names_are_compared_as_the_policy_uses_them() -> None:
    g = _gold("capacity")
    lost_diacritics = CallState.model_validate({**g.state, "full_name": "Egle Butkute"})
    assert compare(lost_diacritics, g, ["Eglė Butkutė"]) == []  # restored from the call
    assert compare(lost_diacritics, g, []) == ["full_name"]  # nothing to restore from


def test_one_of_accepts_either_alternative() -> None:
    g = _gold("change-of-plan")
    by_dates = CallState.model_validate(
        {**g.state, "shift_days": None, "start": "2026-10-10", "end": "2026-10-11"}
    )
    assert compare(by_dates, g, []) == []
    neither = CallState.model_validate({**g.state, "shift_days": None})
    assert compare(neither, g, [])[0].startswith("one of")


def test_heater_reason_counts_by_presence() -> None:
    g = _gold("turnaround")
    reworded = CallState.model_validate({**g.state, "heater_reason": "asthma"})
    missing = CallState.model_validate({**g.state, "heater_reason": None})
    assert compare(reworded, g, []) == []
    assert compare(missing, g, []) == ["heater_reason"]


def test_a_perfect_model_scores_full_marks() -> None:
    golds = GOLDS[:3]
    llm = Fake(script=[Response(content=json.dumps(g.state)) for g in golds for _ in range(2)])
    results = evaluate(llm, golds, runs=2)
    assert all(r.passed() for r in results)
    assert set(field_accuracy(golds, results).values()) == {1.0}
