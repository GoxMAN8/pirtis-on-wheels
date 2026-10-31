"""Speech: what the receptionist says, written by code.

Every number, date and price is spoken as words, every reply asks at most one
question, and the voice gets calmer when the caller is upset. The model never writes
these lines, so it cannot get a fact wrong in them.
"""

from datetime import date

from pirtis.agent.actions import handoff, sms
from pirtis.agent.policy import Ask, Booked, Decision, Handoff, NoFit, Offer
from pirtis.agent.reading import emotion, latest_caller_turn
from pirtis.agent.words import spoken_euros, spoken_number, spoken_range
from pirtis.api.types import Booking, Unit
from pirtis.transcript.types import Action, Transcript, say

# (emotion, speed) hints for the voice
Voice = tuple[str, float]
NEUTRAL: Voice = ("neutral", 1.0)
CALM: Voice = ("calm", 0.95)
UPSET_AT = 0.5


def tone(t: Transcript) -> Voice:
    """A calmer, slightly slower voice when the caller's latest turn sounds angry or
    distressed. Only the latest turn counts: a caller who has calmed down gets back the
    normal voice."""
    upset = emotion(latest_caller_turn(t), "Anger", "Distress")
    return CALM if upset >= UPSET_AT else NEUTRAL


QUESTIONS = {
    # says out loud that we're deferring, so "does not say whether…" can't be misread
    "name": "I can help with that once I know who's calling. Can I get your full name, please?",
    "dates": "Which dates were you thinking of?",
    "people": "How many of you will there be?",
    # names both the car and the figure, so either reading of the question is covered
    "tow": "What car will you tow it with, and how much can it tow, braked?",
    "power": "Is there mains power where the sauna is going?",
}

HANDOFF_LINES = {
    "new_customers": "I can't find an account under that name and number, and new "
    "customers are set up by a colleague. I'll put you through now.",
    "front_desk": "Of course. I'll put you through to a colleague now.",
    "bookings_support": "Sorry, something went wrong on my side. I'll put you through to "
    "a colleague who can sort it out.",
}
HANDOFF_DEFAULT = "I'll put you through to a colleague now."
HANDOFF_SORRY = "I'm sorry about this. I'll put you through to a colleague now."

NO_FIT = "I'm sorry, none of our saunas fit that. Would other dates work for you?"

_WHY_NOT = {
    "weight": "{was} is too heavy for your car",
    "power": "{was} needs mains power",
    "taken": "{was} isn't free then",
}


def check_in_line() -> str:
    """After a silence: two short sentences, no pressure."""
    return "Are you still there? No rush if you need a moment."


def goodbye_line() -> str:
    return "Thanks for calling Pirtis on Wheels. Goodbye!"


def hang_up_line() -> str:
    """After a check-in went unanswered too."""
    return "I'll let you go for now. Call us back any time, goodbye!"


def render(d: Decision, voice: Voice = NEUTRAL) -> list[Action]:
    """The actions for one decision, spoken in the given voice."""
    match d:
        case Ask(slot=slot):
            return [_say(QUESTIONS[slot], voice)]
        case Offer():
            return [_say(offer_line(d), voice)]
        case Booked(booking=b, unit=u):
            return [_say(booked_line(b, u), voice), sms(sms_text(b, u))]
        case Handoff(queue=queue, reason=reason, summary=summary, apologise=apologise):
            line = HANDOFF_SORRY if apologise else HANDOFF_LINES.get(queue, HANDOFF_DEFAULT)
            return [_say(line, voice), handoff(queue, reason, summary)]
        case NoFit():
            return [_say(NO_FIT, voice)]


def offer_line(o: Offer) -> str:
    when = spoken_range(o.start, o.end)
    if o.instead_of is None:
        first = f"I can offer {o.unit.name}, {when}, for {spoken_number(o.people)} people."
    elif o.why_not == "capacity":
        first = (
            f"{o.instead_of.name} only takes {spoken_number(o.instead_of.capacity)}, so for "
            f"{spoken_number(o.people)} of you I can offer {o.unit.name}, {when}."
        )
    else:
        reason = _WHY_NOT[o.why_not].format(was=o.instead_of.name)
        first = f"{reason}, so I can offer {o.unit.name}, {when}."
    return f"{first} That's {spoken_euros(o.total)}. Shall I book it?"


def booked_line(b: Booking, u: Unit) -> str:
    return (
        f"That's booked: {u.name}, {spoken_range(b.start, b.end)}, for "
        f"{spoken_number(b.people)} people. The total is {spoken_euros(b.total)}, and I'll "
        "text you the details."
    )


def sms_text(b: Booking, u: Unit) -> str:
    """The written confirmation. A text is read, not heard, so figures are fine here."""
    s, e = date.fromisoformat(b.start), date.fromisoformat(b.end)
    return (
        f"Pirtis on Wheels: {u.name} is booked from {_short(s)} to {_short(e)} {e.year}, "
        f"{b.people} people, total €{b.total}. Booking {b.id}."
    )


def _short(d: date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _say(text: str, voice: Voice) -> Action:
    return say(text, voice[0], voice[1])
