"""Judge calibration on synthetic ratings: the human evaluation hasn't run yet, so the
arithmetic and the plumbing are proven here with a scripted judge and made-up raters."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.ops.calibrate import ITEMS, calibrate, kappa
from pirtis.ops.human import RUBRIC, load_cards
from pirtis.ops.model import HumanRating
from pirtis.ops.store import FileStore
from pirtis.playground.test_calls import JONAS, LINES, T1, T2, T3, T4, make, scripted

CARDS = {c.id: c for c in load_cards()}


@pytest.mark.parametrize(
    "a, b, k",
    [
        ([True, True, False, False], [True, True, False, False], 1.0),
        ([True, True, False, False], [True, False, False, False], 0.5),  # textbook: po .75, pe .5
        ([True, False, True, False], [False, True, False, True], -1.0),
        ([True, True, True], [True, True, True], None),  # both constant: undefined
        ([], [], None),
    ],
)
def test_kappa(a: list[bool], b: list[bool], k: float | None) -> None:
    assert kappa(a, b) == k


def judge_saying(passes: bool) -> Fake:
    verdict = {"criterion_1": {"reason": "scripted", "pass": passes}}
    return Fake(default=Response(content=json.dumps(verdict)))


def rating(session_id: str, rater: str, **scores: Any) -> HumanRating:
    base: dict[str, Any] = {"natural": 5, "numbers_clear": 5, "one_question": 5, "turn_taking": 5}
    return HumanRating(
        scenario_id="first-booking", session_id=session_id, rater=rater, agent_version="v1",
        goal_achieved=scores.pop("goal", True), note="", created=datetime.now(UTC),
        **{**base, **scores},
    )  # fmt: skip


def booked_session(tmp_path: Path) -> str:
    calls = make(tmp_path, scripted(T1, T2, T3, T4))
    call = calls.start(JONAS)
    for line in LINES:
        calls.say(call, line)
    return call.session_id


def test_agreement_and_kappa_per_item_and_between_raters(tmp_path: Path) -> None:
    sid = booked_session(tmp_path)
    store = FileStore(tmp_path)
    store.put(rating(sid, "AB", turn_taking=2))  # one person says turn-taking was poor
    store.put(rating(sid, "CD", turn_taking=5))
    llm = judge_saying(True)
    result = calibrate(store, llm, CARDS)
    assert result.ratings == 2 and set(result.items) == set(ITEMS)
    assert result.items["natural"] == {"n": 2, "agree": 1.0, "kappa": None}  # all yes: undefined
    assert result.items["turn_taking"]["agree"] == 0.5
    assert result.rater_pairs == 1
    # one judge pass per call and item, however many people rated it: 4 replies x 4 items + goal
    assert llm.i == 4 * len(RUBRIC) + 1


def test_the_judge_fails_a_call_when_any_reply_fails(tmp_path: Path) -> None:
    sid = booked_session(tmp_path)
    store = FileStore(tmp_path)
    store.put(rating(sid, "AB"))
    result = calibrate(store, judge_saying(False), CARDS)
    assert result.items["natural"]["agree"] == 0.0 and result.overall_agree == 0.0


def test_a_rating_of_an_expired_call_is_left_out(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.put(rating("20260101-deadbeef", "AB"))
    assert calibrate(store, judge_saying(True), CARDS).ratings == 0


def test_with_no_rated_calls_nothing_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty calibration in the Human eval view would read as a result."""
    from pirtis.ops import calibrate as module
    from pirtis.ops.model import Calibration

    store = FileStore(tmp_path)
    monkeypatch.setattr(module, "store_from_env", lambda: store)
    monkeypatch.setattr(module, "open_ai_from_env", lambda: Fake())
    module.main()
    assert store.query(Calibration) == []
    assert "nothing to calibrate" in capsys.readouterr().out
