"""The off-path reviewer: a second reading of each model turn, disagreements queued for a
person, people's own ratings never overwritten, and the reviewer's model really used."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pirtis.llm.client import OpenAI, Request, Response
from pirtis.llm.fake import Fake
from pirtis.ops import review as rv
from pirtis.ops.model import Feedback
from pirtis.ops.store import FileStore
from pirtis.playground.test_calls import JONAS, LINES, T1, T2, T3, make, scripted

SINCE = datetime.now(UTC) - timedelta(days=1)


def reading(**kw: Any) -> str:
    base: dict[str, Any] = {
        "intent": "book",
        "full_name": "Jonas Jonaitis",
        "people": None,
        "start": "2026-10-06",
        "end": "2026-10-07",
        "tow_kg": None,
        "unit": None,
        "power": None,
        "accepted_offer": False,
    }
    return json.dumps({**base, **kw})


def a_call(tmp_path: Path) -> tuple[FileStore, list[str]]:
    """Three model turns of a real simulator call: name and dates, four people, the tow."""
    calls = make(tmp_path, scripted(T1, T2, T3))
    call = calls.start(JONAS)
    for line in LINES[:3]:
        calls.say(call, line)
    turns = [s["turn_id"] for s in call.steps if s["state"]]
    return calls.store, turns


def reviewer(*readings: str) -> Fake:
    return Fake(script=[Response(content=r) for r in readings])


def test_a_turn_heard_differently_goes_to_the_feedback_queue(tmp_path: Path) -> None:
    store, turns = a_call(tmp_path)
    llm = reviewer(reading(), reading(people=5), reading(people=4, tow_kg=1500))
    r = rv.review(store, llm, "gpt-4o", SINCE)
    assert len(r.turns) == 3 and r.flagged == [turns[1]]
    [fb] = store.query(Feedback)
    assert fb.turn_id == turns[1] and fb.verdict == "down"
    assert "people: agent 4, reviewer 5" in fb.note and "gpt-4o" in fb.note
    agree = r.agreement()
    assert agree["people"] == pytest.approx(2 / 3) and agree["tow_kg"] == 1.0


def test_a_persons_rating_is_never_overwritten(tmp_path: Path) -> None:
    store, turns = a_call(tmp_path)
    assert store.query(Feedback) == []
    first = rv.review(store, reviewer(reading(), reading(people=5), reading(people=4, tow_kg=1500)),
                      "gpt-4o", SINCE)  # fmt: skip
    [queued] = store.query(Feedback)
    person = queued.model_copy(update={"verdict": "up", "note": "fine by me"})
    store.put(person)
    again = rv.review(store, reviewer(reading(), reading(people=5), reading(people=4, tow_kg=1500)),
                      "gpt-4o", SINCE)  # fmt: skip
    assert first.flagged == [turns[1]] and again.flagged == []
    [kept] = store.query(Feedback)
    assert kept.note == "fine by me"


def test_a_dry_run_queues_nothing(tmp_path: Path) -> None:
    store, turns = a_call(tmp_path)
    r = rv.review(store, reviewer(reading(), reading(people=5), reading(people=4, tow_kg=1500)),
                  "gpt-4o", SINCE, dry_run=True)  # fmt: skip
    assert r.flagged == [turns[1]] and store.query(Feedback) == []


def test_names_are_compared_as_the_policy_uses_them(tmp_path: Path) -> None:
    store, _ = a_call(tmp_path)
    llm = reviewer(reading(full_name="jonas JONAITIS"), reading(people=4), reading(people=4,
                   tow_kg=1500))  # fmt: skip
    assert rv.review(store, llm, "gpt-4o", SINCE).flagged == []


def test_an_unreadable_answer_is_counted_not_fatal(tmp_path: Path) -> None:
    store, _ = a_call(tmp_path)
    llm = reviewer("not json", reading(people=4), reading(people=4, tow_kg=1500))
    r = rv.review(store, llm, "gpt-4o", SINCE)
    assert r.errors == 1 and len(r.turns) == 2


def test_the_review_model_asks_for_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class Reply(io.BytesIO):
        def __enter__(self) -> "Reply":
            return self

        def __exit__(self, *a: object) -> None:
            pass

    def fake_urlopen(req: Any, timeout: float) -> Reply:
        sent.update(json.loads(req.data))
        body = {"choices": [{"message": {"role": "assistant", "content": "{}"}}], "usage": {}}
        return Reply(json.dumps(body).encode())

    monkeypatch.setattr(rv, "urlopen", fake_urlopen)
    llm = rv.ReviewModel(OpenAI("key"), "gpt-4o")
    llm.chat(Request(messages=[]))
    assert sent["model"] == "gpt-4o"
