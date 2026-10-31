"""Speech is what the caller hears, so its rules are tested line by line: numbers and
dates as words, one question per turn, short, and a calmer voice for an upset caller."""

import re
from pathlib import Path

import pytest

from pirtis.agent.policy import (
    Ask,
    Booked,
    BookingsRead,
    Cancelled,
    ConfirmCancel,
    ConfirmChange,
    Decision,
    Goodbye,
    Handoff,
    Hold,
    Moved,
    NoFit,
    Offer,
    OwnClash,
    Price,
    Refuse,
    Reply,
    Sorry,
)
from pirtis.agent.speech import (
    CALM,
    NEUTRAL,
    check_in_line,
    goodbye_line,
    hang_up_line,
    render,
    sms_text,
    tone,
)
from pirtis.agent.words import (
    digits_to_words,
    spoken_date,
    spoken_euros,
    spoken_number,
    spoken_ordinal,
    spoken_range,
)
from pirtis.api.types import Booking, Site, Unit
from pirtis.harness.fixture import load_fixtures
from pirtis.transcript.types import Emotion, Item, Prosody, Transcript

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

MAZOJI = Unit("unit_mazoji", "Mažoji", 4, 750, "wood", False, 120)
DIDZIOJI = Unit("unit_didzioji", "Didžioji", 8, 1600, "wood", False, 210)
VIDUTINE = Unit("unit_vidutine", "Vidutinė", 6, 1100, "electric", True, 160)
BOOKING = Booking(
    customer_id="cus_07",
    unit_id="unit_mazoji",
    start="2026-10-06",
    end="2026-10-07",
    people=4,
    site=Site(power_available=False),
    tow_capacity_kg=2000,
    id="bk_101",
    version=1,
    status="booked",
    total=240,
)


@pytest.mark.parametrize(
    "n, words",
    [
        (0, "zero"),
        (7, "seven"),
        (13, "thirteen"),
        (21, "twenty-one"),
        (100, "one hundred"),
        (101, "one hundred and one"),
        (240, "two hundred and forty"),
        (420, "four hundred and twenty"),
        (1000, "one thousand"),
        (1001, "one thousand and one"),
        (1100, "one thousand one hundred"),
        (1500, "one thousand five hundred"),
        (2026, "two thousand and twenty-six"),
        (3500, "three thousand five hundred"),
        (9999, "nine thousand nine hundred and ninety-nine"),
    ],
)
def test_spoken_number(n: int, words: str) -> None:
    assert spoken_number(n) == words


def test_spoken_number_refuses_what_it_cannot_say() -> None:
    with pytest.raises(ValueError):
        spoken_number(10000)


@pytest.mark.parametrize(
    "n, words",
    [
        (1, "first"),
        (2, "second"),
        (3, "third"),
        (11, "eleventh"),
        (12, "twelfth"),
        (20, "twentieth"),
        (21, "twenty-first"),
        (22, "twenty-second"),
        (23, "twenty-third"),
        (30, "thirtieth"),
        (31, "thirty-first"),
    ],
)
def test_spoken_ordinal(n: int, words: str) -> None:
    assert spoken_ordinal(n) == words


def test_dates_and_ranges() -> None:
    assert spoken_date("2026-10-06") == "Tuesday the sixth of October"
    assert spoken_range("2026-10-06", "2026-10-06") == "Tuesday the sixth of October"
    assert (
        spoken_range("2026-10-06", "2026-10-07")
        == "Tuesday the sixth to Wednesday the seventh of October"
    )
    assert (
        spoken_range("2026-09-30", "2026-10-02")
        == "Wednesday the thirtieth of September to Friday the second of October"
    )


def test_euros() -> None:
    assert spoken_euros(1) == "one euro"
    assert spoken_euros(240) == "two hundred and forty euros"


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("That's 240 euros.", "That's two hundred and forty euros."),
        ("It costs €420.", "It costs four hundred and twenty euros."),
        ("It costs 420€.", "It costs four hundred and twenty euros."),
        ("It weighs 2,000 kg.", "It weighs two thousand kilos."),
        ("It weighs 750kg.", "It weighs seven hundred and fifty kilos."),
        ("about 1.8 tonnes", "about one point eight tonnes"),
        ("a 2k car", "a two thousand car"),
        ("on the 6th", "on the sixth"),
        ("from 2026-10-06", "from Tuesday the sixth of October"),
        ("pick up at 10:30", "pick up at ten thirty"),
        ("pick up at 10:00", "pick up at ten o'clock"),
        ("a 50% deposit", "a fifty percent deposit"),
        ("call 112", "call one hundred and twelve"),
        ("+37061234567", "plus three seven zero six one two three four five six seven"),
    ],
)
def test_digits_to_words(text: str, spoken: str) -> None:
    assert digits_to_words(text) == spoken


def test_digits_to_words_leaves_no_digit_behind() -> None:
    mess = "Room 4B, 3 x 2.5h, 12/10, 100000 points, 007, v2"
    assert not re.search(r"\d", digits_to_words(mess))


def _fixture_transcript(id: str) -> Transcript:
    for f in load_fixtures(FIXTURES):
        if f.id == id:
            return f.transcript
    raise LookupError(id)


def _user(content: str, start: float, **emotions: float) -> Item:
    return Item(
        type="message",
        role="user",
        start=start,
        end=start + 1,
        content=content,
        is_final=True,
        speech_final=True,
        prosody=Prosody([Emotion(k, v) for k, v in emotions.items()]),
    )


def test_tone_follows_the_callers_latest_turn() -> None:
    assert tone(_fixture_transcript("rising-frustration")) == CALM
    assert tone(_fixture_transcript("happy-path")) == NEUTRAL
    calmed = Transcript(
        today="2026-09-28",
        items=[
            _user("this is ridiculous", 1, Anger=0.9),
            Item(type="message", role="assistant", start=3, end=4, content="Sorry about that."),
            _user("ok, fine, thanks", 5, Calmness=0.7),
        ],
    )
    assert tone(calmed) == NEUTRAL
    upset = Transcript(today="2026-09-28", items=[_user("I'm so stressed", 1, Distress=0.6)])
    assert tone(upset) == CALM


SAMPLES: list[Decision] = [
    *(
        Ask(slot)
        for slot in (
            "name",
            "dates",
            "people",
            "tow",
            "power",
            "which_booking",
            "new_dates",
            "need",
        )
    ),
    Offer(MAZOJI, "2026-10-06", "2026-10-07", 4, 240),
    Offer(VIDUTINE, "2026-10-04", "2026-10-05", 6, 320, blocked_day="2026-10-03"),
    Offer(MAZOJI, "2026-10-10", "2026-10-11", 4, 240, weekend_min=True),
    OwnClash(BOOKING, MAZOJI),
    Refuse(DIDZIOJI),
    Refuse(None),
    ConfirmChange(BOOKING, MAZOJI, "2026-10-13", "2026-10-14"),
    Moved(BOOKING, MAZOJI),
    ConfirmCancel(BOOKING, MAZOJI),
    Cancelled(BOOKING, MAZOJI),
    BookingsRead(((BOOKING, MAZOJI),)),
    BookingsRead(()),
    Price(MAZOJI, "2026-10-06", "2026-10-07", 240, (MAZOJI, DIDZIOJI, VIDUTINE)),
    Price(MAZOJI, None, None, 0, (MAZOJI, DIDZIOJI, VIDUTINE)),
    Price(None, None, None, 0, (MAZOJI, DIDZIOJI, VIDUTINE)),
    Goodbye(),
    Hold(),
    Reply("We're based near Vilnius and deliver the trailer ready to heat."),
    Sorry(),
    NoFit("too_many", limit=8),
    NoFit("too_light", limit=750),
    NoFit("no_booking"),
    NoFit("no_dates"),
    Offer(DIDZIOJI, "2026-10-03", "2026-10-04", 8, 420, instead_of=MAZOJI, why_not="capacity"),
    Offer(VIDUTINE, "2026-10-10", "2026-10-11", 6, 320, instead_of=DIDZIOJI, why_not="weight"),
    Offer(DIDZIOJI, "2026-10-10", "2026-10-11", 6, 420, instead_of=VIDUTINE, why_not="power"),
    Offer(DIDZIOJI, "2026-10-10", "2026-10-11", 6, 420, instead_of=VIDUTINE, why_not="taken"),
    Booked(BOOKING, MAZOJI),
    Handoff("new_customers", "no customer matches"),
    Handoff("front_desk", "caller asked for a person"),
    Handoff("bookings_support", "api error"),
    Handoff("front_desk", "caller upset and cut us off", apologise=True),
    NoFit("nothing fits"),
]


def _spoken(actions: list[dict[str, object]]) -> list[str]:
    return [str(a["text"]) for a in actions if a["type"] == "say"]


@pytest.mark.parametrize("d", SAMPLES, ids=lambda d: type(d).__name__)
def test_every_line_follows_the_voice_rules(d: Decision) -> None:
    lines = _spoken(render(d)) or [""]
    for line in lines + [check_in_line(), goodbye_line(), hang_up_line()]:
        assert not re.search(r"\d", line), line
        assert line.count("?") <= 1, line
        before_question = line.split("?")[0] if "?" in line else line
        sentences = [s for s in re.split(r"(?<=[.!])\s+", before_question.strip()) if s]
        assert len(sentences) <= 3, line  # at most two statements, then the question


def test_offers_end_with_the_booking_question() -> None:
    for d in SAMPLES:
        if isinstance(d, Offer):
            assert _spoken(render(d))[0].endswith("Shall I book it?")


def test_every_handoff_is_announced_then_carries_a_summary() -> None:
    actions = render(Handoff("front_desk", "caller asked for a person", summary="Tomas, 6 people"))
    assert [a["type"] for a in actions] == ["say", "transfer"]
    assert "colleague" in str(actions[0]["text"])
    assert actions[1]["summary"] == "Tomas, 6 people"


def test_new_customers_hear_why() -> None:
    line = _spoken(render(Handoff("new_customers", "no customer matches")))[0]
    assert "colleague" in line and "new" in line


def test_a_booking_is_confirmed_aloud_and_by_text() -> None:
    actions = render(Booked(BOOKING, MAZOJI))
    assert [a["type"] for a in actions] == ["say", "sms"]
    assert "text you" in str(actions[0]["text"])
    assert actions[1]["text"] == sms_text(BOOKING, MAZOJI)
    assert "240" in sms_text(BOOKING, MAZOJI) and "bk_101" in sms_text(BOOKING, MAZOJI)


def test_voice_is_passed_to_every_line() -> None:
    for a in render(Ask("dates"), CALM):
        assert a["generation_config"] == {"emotion": "calm", "speed": 0.95}


def test_check_in_asks_if_the_caller_is_still_there() -> None:
    assert "still there" in check_in_line()


def test_every_offer_reads_back_unit_dates_people_and_price() -> None:
    """The read-back rule: what the caller agrees to is exactly what was said."""
    for d in SAMPLES:
        if isinstance(d, Offer):
            line = _spoken(render(d))[0]
            assert d.unit.name in line
            assert spoken_range(d.start, d.end).split(" to ")[-1] in line
            assert spoken_number(d.people) in line
            assert spoken_euros(d.total) in line


def test_an_identified_greeting_is_asked_what_it_needs() -> None:
    line = _spoken(render(Ask("need")))[0]
    assert "what can i do for you" in line.lower() and line.count("?") == 1
