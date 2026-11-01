"""Live calls in the playground: the caller types or speaks, decide answers, and the call
is kept on disk after every turn. A scripted model stands in for gpt-4o-mini, so every
test here spends zero tokens."""

import json
from pathlib import Path
from typing import Any

import pytest

from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.ops.model import Feedback, Session, Turn
from pirtis.ops.store import FileStore
from pirtis.playground.analytics import funnel
from pirtis.playground.calls import GREETING, UNKNOWN_PHONE, CallClosed, Calls

SEED = Path(__file__).resolve().parents[3] / "fixtures" / "seeds" / "base.json"
JONAS = "+37062223344"


def state(**kw: Any) -> dict[str, Any]:
    """A CallState as the model would write it: everything it hasn't heard is null."""
    base: dict[str, Any] = {
        "gist": "",
        "intent": "book",
        "full_name": None,
        "people": None,
        "start": None,
        "end": None,
        "shift_days": None,
        "change_target_start": None,
        "tow_quote": None,
        "tow_kg": None,
        "power": None,
        "unit_quote": None,
        "unit": None,
        "unit_is_must": False,
        "heater_pref": None,
        "heater_reason": None,
        "answer": None,
        "consent_quote": None,
        "accepted_offer": False,
        "go_ahead": False,
    }
    return {**base, **kw}


T1 = state(full_name="Jonas Jonaitis", start="2026-10-06", end="2026-10-07")
T2 = {**T1, "people": 4}
TOW = "The car can tow fifteen hundred kilos braked."
T3 = {**T2, "tow_quote": TOW, "tow_kg": 1500}
YES = "Yes, book it."
T4 = {**T3, "consent_quote": YES, "accepted_offer": True, "go_ahead": True}
LINES = [
    "Hi, this is Jonas Jonaitis. Could we get a sauna on the sixth and seventh of October?",
    "Four of us.",
    TOW,
    YES,
]


def scripted(*states: dict[str, Any]) -> Fake:
    return Fake(script=[Response(content=json.dumps(s)) for s in states])


def make(tmp_path: Path, llm: Fake) -> Calls:
    return Calls(FileStore(tmp_path), llm, seed=SEED, today="2026-09-28")


def said(view: dict[str, Any]) -> str:
    return str(view["items"][-1]["content"])


def booked_call(tmp_path: Path) -> tuple[Calls, str]:
    calls = make(tmp_path, scripted(T1, T2, T3, T4))
    call = calls.start(JONAS)
    for line in LINES:
        calls.say(call, line)
    return calls, call.session_id


def test_a_call_opens_with_the_greeting_and_no_model_call(tmp_path: Path) -> None:
    llm = Fake()
    calls = make(tmp_path, llm)
    view = calls.view(calls.start(JONAS))
    assert [i["content"] for i in view["items"]] == [GREETING]
    assert view["status"] == "open" and llm.i == 0


def test_a_booking_spoken_through_to_the_end(tmp_path: Path) -> None:
    calls, sid = booked_call(tmp_path)
    call = calls.get(sid)
    assert call is not None
    view = calls.view(call)
    decisions = [s["decision"] for s in call.steps]
    assert decisions == ["Ask", "Ask", "Offer", "Booked"]
    assert view["card"]["status"] == "booked"
    assert view["card"]["booking"]["unit"] == "Mažoji"
    assert "bk_101" in view["card"]["sms"]
    assert view["card"]["identified"] is True

    store = FileStore(tmp_path)
    [session] = store.query(Session)
    assert session.turns == 4 and session.bookings[0]["id"] == "bk_101"
    turns = store.query(Turn)
    assert len(turns) == 4 and {t.session_id for t in turns} == {sid}
    assert all(t.source == "playground" for t in turns)


def test_the_transcript_records_what_the_agent_needs_next_turn(tmp_path: Path) -> None:
    """The agent is stateless: turn two only knows Jonas is identified because turn one's
    identify call is in the transcript."""
    calls, sid = booked_call(tmp_path)
    call = calls.get(sid)
    assert call is not None
    names = [i.name for i in call.t.items if i.type == "function_call"]
    # identified once, then carried; own bookings are re-read before every search (D-14)
    assert names == ["customers.identify", "bookings.list", "bookings.list", "bookings.create"]


def test_the_card_shows_only_what_passed_the_agents_own_checks(tmp_path: Path) -> None:
    """A tow figure with no number in the caller's words is dropped by the agent's quote
    check, so the card leaves it fogged and the receptionist asks again."""
    vague = {**T2, "tow_quote": "it's a big car", "tow_kg": 1500}
    calls = make(tmp_path, scripted(T1, T2, vague))
    call = calls.start(JONAS)
    for line in [LINES[0], LINES[1], "Oh, it's a big car, it'll be fine."]:
        calls.say(call, line)
    card = calls.view(call)["card"]
    assert card["tow_kg"] is None and card["next"] == "tow"
    assert "tow" in said(calls.view(call)).lower()


def test_the_card_says_power_is_asked_next_when_an_electric_sauna_fits(tmp_path: Path) -> None:
    six = {**T1, "people": 6}
    calls = make(tmp_path, scripted(T1, six, {**six, "tow_quote": TOW, "tow_kg": 1500}))
    call = calls.start(JONAS)
    for line in [LINES[0], "Six of us.", TOW]:
        calls.say(call, line)
    assert calls.view(call)["card"]["next"] == "power"
    assert "power" in said(calls.view(call)).lower()


def test_words_the_caller_never_said_are_never_shown_as_their_evidence(tmp_path: Path) -> None:
    """The model copied the sauna's name from our offer, not from the caller: the agent
    drops the sauna, and the card must not say "you said Didžioji"."""
    borrowed = {**T3, "unit_quote": "Mažoji", "unit": "unit_mazoji"}
    calls = make(tmp_path, scripted(T1, T2, borrowed))
    call = calls.start(JONAS)
    for line in LINES[:3]:
        calls.say(call, line)
    card = calls.view(call)["card"]
    assert card["unit"] is None and card["evidence"]["unit"] is None
    assert card["evidence"]["tow"] == TOW


def test_an_offer_shows_on_the_card_before_the_caller_says_yes(tmp_path: Path) -> None:
    calls = make(tmp_path, scripted(T1, T2, T3))
    call = calls.start(JONAS)
    for line in LINES[:3]:
        calls.say(call, line)
    card = calls.view(call)["card"]
    assert card["status"] == "offered"
    assert card["offer"] == {
        "unit": "Mažoji",
        "start": "2026-10-06",
        "end": "2026-10-07",
        "people": 4,
        "total": 240,
    }
    assert card["evidence"]["tow"] == TOW


def test_silence_checks_in_then_hangs_up_without_the_model(tmp_path: Path) -> None:
    llm = Fake()
    calls = make(tmp_path, llm)
    call = calls.start(JONAS)
    calls.silence(call, 8)
    assert "still there" in said(calls.view(call))
    calls.silence(call, 8)
    assert call.status == "ended" and llm.i == 0


def test_a_barge_in_cuts_our_line_where_the_caller_came_in(tmp_path: Path) -> None:
    calls = make(tmp_path, scripted(T1, {**T1, "people": 8}))
    call = calls.start(JONAS)
    calls.say(call, LINES[0])
    ours = call.t.items[-1]
    full = ours.content
    calls.say(call, "eight of us", barge_in=True)
    cut = next(i for i in call.t.items if i.type == "message" and i.interrupted)
    caller = next(i for i in reversed(call.t.items) if i.role == "user")
    assert cut is ours and full.startswith(cut.content) and len(cut.content) < len(full)
    assert cut.start < caller.start < cut.end  # the kit's definition of a barge-in


def test_an_angry_caller_who_cuts_in_goes_to_a_person_without_the_model(
    tmp_path: Path,
) -> None:
    llm = Fake()
    calls = make(tmp_path, llm)
    call = calls.start(JONAS)
    calls.say(call, "I've been waiting all morning for this", anger=0.9, barge_in=True)
    assert call.status == "transferred" and llm.i == 0
    assert calls.view(call)["card"]["status"] == "transferred"


def test_an_unknown_number_is_passed_to_new_customers(tmp_path: Path) -> None:
    calls = make(tmp_path, scripted(state(full_name="Ona Onaitė")))
    call = calls.start(UNKNOWN_PHONE)
    calls.say(call, "Hi, I'm Ona Onaitė, I'd like to rent a sauna.")
    assert call.status == "transferred"
    assert call.steps[-1]["effects"][-2]["to"] == "new_customers"
    # the funnel reads the same hand-off from the content-free turn record
    stages = funnel(FileStore(tmp_path).query(Turn))["stages"]
    assert [s["calls"] for s in stages] == [1, 0, 0, 0]


def test_a_call_resumes_from_disk_with_its_booking_intact(tmp_path: Path) -> None:
    """After a restart the cache is empty; the call is rebuilt from the stored transcript
    by replaying its own writes, so booking ids and versions come out the same."""
    _, sid = booked_call(tmp_path)
    bye = {**T3, "intent": "goodbye"}
    fresh = make(tmp_path, scripted(bye))
    call = fresh.get(sid)
    assert call is not None and call.status == "open"
    mine = [b for b in call.api.bookings() if b.customer_id == "cus_07"]
    assert {b.id for b in mine} == {"bk_65", "bk_101"}
    fresh.say(call, "Great, thanks, bye!")
    assert fresh.view(call)["status"] == "ended"


def test_an_ended_call_takes_no_more_turns(tmp_path: Path) -> None:
    calls = make(tmp_path, Fake())
    call = calls.start(JONAS)
    calls.silence(call, 8)
    calls.silence(call, 8)
    with pytest.raises(CallClosed):
        calls.say(call, "hello?")


def test_hanging_up_ends_the_call_without_asking_the_receptionist(tmp_path: Path) -> None:
    llm = Fake()
    calls = make(tmp_path, llm)
    call = calls.start(JONAS)
    calls.hang_up(call)
    [saved] = FileStore(tmp_path).query(Session)
    assert (saved.status, saved.turns, llm.i) == ("ended", 0, 0)


def test_feedback_keeps_the_turn_and_a_snapshot_of_the_call(tmp_path: Path) -> None:
    calls, sid = booked_call(tmp_path)
    call = calls.get(sid)
    assert call is not None
    turn_id = call.steps[2]["turn_id"]
    calls.feedback(call, turn_id, "down", "Too long")
    [fb] = FileStore(tmp_path).query(Feedback)
    assert (fb.turn_id, fb.verdict, fb.note) == (turn_id, "down", "Too long")
    assert len(fb.transcript["items"]) == len(call.t.items)


def test_feedback_on_a_turn_the_call_never_had_is_refused(tmp_path: Path) -> None:
    calls, sid = booked_call(tmp_path)
    call = calls.get(sid)
    assert call is not None
    with pytest.raises(KeyError):
        calls.feedback(call, "nope", "down", "")


def test_a_name_heard_with_combining_accents_still_finds_the_customer(tmp_path: Path) -> None:
    """Manual testing, B4: "Eglė" can arrive as "e" plus a combining dot (NFD), and the
    model echoes it back that way; the kit's API compares letters exactly, so Eglė was
    put through as a stranger. Every line is normalised to one spelling (NFC) first."""
    import unicodedata

    line = unicodedata.normalize("NFD", "Hi, it's Eglė Butkutė.")
    heard = state(intent="greeting", full_name=unicodedata.normalize("NFD", "Eglė Butkutė"))
    calls = make(tmp_path, scripted(heard))
    call = calls.start("+37068881122")
    calls.say(call, line)
    assert call.steps[-1]["decision"] != "Handoff"
    assert call.t.items[-1].content == "Thanks, I have your details. What can I do for you today?"
