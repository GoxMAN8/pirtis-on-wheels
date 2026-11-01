"""The turn gate decides, with no model call, when the receptionist must not simply
answer: the caller is still talking or holding, has gone quiet, or wants a person."""

import json
from pathlib import Path

import pytest

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.agent.turn import gate, wants_human
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.run import Runner
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.transcript.types import Action, Emotion, Item, Prosody, Transcript

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
ALL = load_fixtures(FIXTURES) + load_fixtures(FIXTURES / "extra")
GATED = {
    "interim-speech": "wait",
    "silence": "say",
    "rising-frustration": "transfer",
    "silence-after-check-in": "end_call",
    "hang-on-let-me-ask": "wait",
}


def by_id(id: str) -> Fixture:
    return next(f for f in ALL if f.id == id)


def types(actions: list[Action] | None) -> list[str]:
    return [str(a["type"]) for a in actions or []]


@pytest.mark.parametrize("id", sorted(GATED))
def test_gated_fixtures(id: str) -> None:
    actions = gate(by_id(id).transcript)
    assert actions is not None
    assert GATED[id] in types(actions)
    if GATED[id] == "wait":
        assert types(actions) == ["wait"]


@pytest.mark.parametrize("f", [f for f in ALL if f.id not in GATED], ids=lambda f: f.id)
def test_every_other_fixture_goes_on_to_understanding(f: Fixture) -> None:
    assert gate(f.transcript) is None


def test_silence_check_in_is_the_check_in_line() -> None:
    actions = gate(by_id("silence").transcript) or []
    assert types(actions) == ["say"]
    assert "still there" in str(actions[0]["text"])


def test_second_silence_says_goodbye_then_hangs_up() -> None:
    actions = gate(by_id("silence-after-check-in").transcript) or []
    assert types(actions) == ["say", "end_call"]
    assert "bye" in str(actions[0]["text"]).lower()


def test_transfer_is_announced_calm_and_carries_who_called() -> None:
    actions = gate(by_id("rising-frustration").transcript) or []
    assert types(actions) == ["say", "transfer"]
    assert "colleague" in str(actions[0]["text"])
    assert actions[0]["generation_config"]["emotion"] == "calm"
    assert "cus_12" in str(actions[1]["summary"])


@pytest.mark.parametrize("id", sorted(GATED))
def test_gated_turns_spend_no_model_call(id: str) -> None:
    judge = Fake(
        default=Response(
            content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in (1, 2)})
        )
    )
    runner = Runner(llm=Fake(), judge=judge, decide=decide, handlers=handlers)
    r = runner.run(by_id(id)).runs[0]
    assert r.passed(), r.failures
    assert r.stats.calls == 0


@pytest.mark.parametrize(
    "line",
    [
        "can I just talk to a person? an actual person",
        "can I speak to someone please",
        "put me through to a manager",
        "get me a human",
        "I want a real person",
        "is there somebody I can talk to",
        "transfer me to an operator",
    ],
)
def test_wants_human_yes(line: str) -> None:
    assert wants_human(line)


@pytest.mark.parametrize(
    "line",
    [
        "we want the big one. the eight person one",
        "the site manager says there's power",
        "it's for a hen do, my sister's",
        "my husband thinks he cancelled it",
        "four people, it's for my dad",
        "someone said you had a small one",
    ],
)
def test_wants_human_no(line: str) -> None:
    assert not wants_human(line)


# synthetic calls for the edges the fixtures don't cover


def _us(content: str, start: float, end: float, interrupted: bool = False) -> Item:
    return Item(
        type="message",
        role="assistant",
        start=start,
        end=end,
        content=content,
        interrupted=interrupted,
    )


def _them(content: str, start: float, end: float, final: bool = True, **emo: float) -> Item:
    return Item(
        type="message",
        role="user",
        start=start,
        end=end,
        content=content,
        is_final=final,
        speech_final=final,
        prosody=Prosody([Emotion(k, v) for k, v in emo.items()]),
    )


def _call(now: float, *items: Item) -> Transcript:
    return Transcript(today="2026-09-28", now=now, items=list(items))


GREETING = _us("Pirtis on Wheels, hello?", 0.5, 2.4)


def test_we_just_spoke_so_wait() -> None:
    t = _call(12.0, GREETING, _them("hi, I'd like a sauna", 3, 6), _us("Lovely. Your name?", 7, 9))
    assert types(gate(t)) == ["wait"]


def test_cut_off_with_nothing_said_yet_waits() -> None:
    t = _call(10.0, GREETING, _them("hi", 3, 4), _us("Thanks. Which dates were", 5, 7, True))
    assert types(gate(t)) == ["wait"]


def test_silence_after_a_goodbye_hangs_up_without_speaking() -> None:
    t = _call(
        20.0,
        GREETING,
        _them("ok that's all, thanks, bye", 3, 6),
        _us("Thanks for calling Pirtis on Wheels. Goodbye!", 7, 10),
    )
    assert types(gate(t)) == ["end_call"]


def test_filler_only_waits() -> None:
    t = _call(
        8.0,
        GREETING,
        _them("hi, um, so", 3, 5),
        _us("Take your time.", 5.5, 6.5),
        _them("uh", 7, 7.5),
    )
    assert types(gate(t)) == ["wait"]


def test_trailing_filler_after_real_words_does_not_wait() -> None:
    t = _call(8.0, GREETING, _them("my husband thinks he cancelled it, so, uh", 3, 7.5))
    assert gate(t) is None


@pytest.mark.parametrize(
    "line",
    ["hang on, let me ask my husband", "one sec", "just a moment please", "bear with me"],
)
def test_hold_waits(line: str) -> None:
    t = _call(9.0, GREETING, _them(line, 3, 8.5))
    assert types(gate(t)) == ["wait"]


def test_hang_on_with_a_real_question_is_not_a_hold() -> None:
    t = _call(9.0, GREETING, _them("hang on, could we push it a week?", 3, 8.5))
    assert gate(t) is None


def test_anger_alone_does_not_transfer() -> None:
    t = _call(9.0, GREETING, _them("this is the third time I've called", 3, 8.5, Anger=0.8))
    assert gate(t) is None


def test_anger_with_a_barge_in_transfers() -> None:
    t = _call(
        12.0,
        GREETING,
        _them("six of us, no power", 3, 6),
        _us("For six I can offer Vidutinė, which needs", 6.5, 10.0, True),
        _them("no! I said no power", 8.0, 11.5, Anger=0.75),
    )
    assert types(gate(t)) == ["say", "transfer"]


def test_mild_annoyance_with_a_barge_in_does_not_transfer() -> None:
    t = _call(
        12.0,
        GREETING,
        _them("six of us", 3, 6),
        _us("For six I can offer", 6.5, 10.0, True),
        _them("sorry, it's eight now", 8.0, 11.5, Anger=0.3),
    )
    assert gate(t) is None


# ---- keeping the thread of the call (D-98) ----


def call(*lines: tuple[str, str], base: str = "silence") -> Transcript:
    """A kit transcript continued by these (role, text) lines, the caller's last."""
    from dataclasses import replace

    t = by_id(base).transcript
    items, at = list(t.items), max(it.end for it in t.items) + 0.5
    for role, text in lines:
        items.append(Item(type="message", role=role, content=text, start=at, end=at + 1.5,
                          is_final=True, speech_final=True))  # fmt: skip
        at += 2.0
    return replace(t, items=items, now=at)


def test_a_finished_booking_closes_on_no_thats_all() -> None:
    from pirtis.agent.speech import ANYTHING_ELSE

    booked = f"That's booked: Mažoji. I'll text you the details. {ANYTHING_ELSE}"
    actions = gate(call(("assistant", booked), ("user", "no, that's all, thanks"))) or []
    assert types(actions) == ["say", "end_call"]
    assert "goodbye" in str(actions[0]["text"]).lower()


def test_anything_else_with_something_else_goes_on() -> None:
    from pirtis.agent.speech import ANYTHING_ELSE

    t = call(("assistant", f"Done. {ANYTHING_ELSE}"), ("user", "yes, can I move my other one?"))
    assert gate(t) is None


def test_after_a_check_in_yes_im_here_brings_back_the_question() -> None:
    from pirtis.agent.speech import check_in_line

    t = call(("assistant", "How many of you will there be?"), ("assistant", check_in_line()),
             ("user", "yes, sorry, I'm here"))  # fmt: skip
    actions = gate(t) or []
    assert types(actions) == ["say"]
    assert actions[0]["text"] == "Great. How many of you will there be?"


def test_after_a_check_in_an_answer_goes_to_understanding() -> None:
    from pirtis.agent.speech import check_in_line

    t = call(("assistant", "How many of you will there be?"), ("assistant", check_in_line()),
             ("user", "four of us"))  # fmt: skip
    assert gate(t) is None
