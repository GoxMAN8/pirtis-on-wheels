"""Speech: what the receptionist says, written by code.

Every number, date and price is spoken as words, every reply asks at most one
question, and the voice gets calmer when the caller is upset. The model never writes
these lines, so it cannot get a fact wrong in them. Every offer reads back the unit,
the dates, the number of people and the price, which is also what the policy reads
back from the transcript before it books.
"""

from datetime import date

from pirtis.agent.actions import handoff, sms
from pirtis.agent.policy import (
    Ask,
    Booked,
    BookingsRead,
    Cancelled,
    ConfirmCancel,
    ConfirmChange,
    Decision,
    FreeDates,
    Goodbye,
    Handoff,
    Hold,
    Moved,
    NoFit,
    Offer,
    OffTopic,
    OwnClash,
    Price,
    Refuse,
    Reply,
    Sorry,
)
from pirtis.agent.reading import emotion, latest_caller_turn
from pirtis.agent.words import spoken_date, spoken_euros, spoken_number, spoken_range
from pirtis.api.types import Booking, Unit
from pirtis.transcript.types import Action, Transcript, end_call, say, wait

# (emotion, speed) hints for the voice
Voice = tuple[str, float]
NEUTRAL: Voice = ("neutral", 1.0)
CALM: Voice = ("calm", 0.95)
UPSET_AT = 0.5
HOLD_S = 8.0


def tone(t: Transcript) -> Voice:
    """A calmer, slightly slower voice when the caller's latest turn sounds angry or
    distressed. Only the latest turn counts: a caller who has calmed down gets back the
    normal voice."""
    upset = emotion(latest_caller_turn(t), "Anger", "Distress")
    return CALM if upset >= UPSET_AT else NEUTRAL


SORRY = "Sorry, I didn't catch that. Could you say it again?"
OFF_TOPIC = (
    "That's not something I can help with, but I can help you rent one of our saunas. "
    "What would you like to book?"
)

QUESTIONS = {
    # says out loud that we're deferring, so "does not say whether…" can't be misread
    "name": "I can help with that once I know who's calling. Can I get your full name, please?",
    # about a booking they already have: says outright that it can't look yet (D-87)
    "name_booking": (
        "I can't look at your bookings until I know who's calling. "
        "Can I get your full name, please?"
    ),
    "dates": "Which dates were you thinking of?",
    "past_dates": "That date has already passed. Which dates were you thinking of?",
    "people": "How many of you will there be?",
    # names both the car and the figure, so either reading of the question is covered
    # one question (manual testing found the two-part form hard to answer); the second
    # sentence keeps both readings the kit's case 05 checks for (D-51, D-96)
    "tow": "What car will you tow it with? I need its braked towing limit.",
    "power": "Is there mains power where the sauna is going?",
    "which_booking": "Which of your bookings do you mean? The dates are enough.",
    "new_dates": "Which dates would you like to move it to?",
    "need": "Thanks, I have your details. What can I do for you today?",
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

_WHY_NOT = {
    "weight": "{was} is too heavy for your car",
    "power": "{was} needs mains power",
    "taken": "{was} isn't free then",
}


def check_in_line() -> str:
    """After a silence: two short sentences, no pressure."""
    return "Are you still there? No rush if you need a moment."


# how a finished task ends: the caller can go on, or close the call (the turn gate hears a
# short "no, that's all" after it and says goodbye, with no model call)
ANYTHING_ELSE = "Is there anything else I can help with?"


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
        case OwnClash(booking=b, unit=u):
            return [
                _say(
                    f"You already have {u.name} booked for {spoken_range(b.start, b.end)}. "
                    "Would you like to change that booking instead?",
                    voice,
                )
            ]
        case Refuse(unit=u):
            follow = (
                f"Would other dates for {u.name} work for you?"
                if u
                else ("Is there anything else I can help with?")
            )
            return [
                _say(
                    "I'm sorry, I can't share anything about other customers' bookings or "
                    f"their details. {follow}",
                    voice,
                )
            ]
        case ConfirmChange(booking=b, unit=u, start=start, end=end):
            return [
                _say(f"I can move {u.name} to {spoken_range(start, end)}. Shall I do that?", voice)
            ]
        case Moved(booking=b, unit=u):
            return [
                _say(
                    f"Done: {u.name} is moved to {spoken_range(b.start, b.end)}. I'll text you "
                    f"the new dates. {ANYTHING_ELSE}",
                    voice,
                ),
                sms(f"Pirtis on Wheels: {u.name} is now booked {_dates(b)}. Booking {b.id}."),
            ]
        case ConfirmCancel(booking=b, unit=u):
            return [
                _say(f"That's {u.name}, {spoken_range(b.start, b.end)}. Shall I cancel it?", voice)
            ]
        case Cancelled(booking=b, unit=u):
            return [
                _say(
                    f"Done: {u.name} for {spoken_range(b.start, b.end)} is cancelled. I'll text "
                    f"you a confirmation. {ANYTHING_ELSE}",
                    voice,
                ),
                sms(f"Pirtis on Wheels: {u.name} {_dates(b)} is cancelled. Booking {b.id}."),
            ]
        case BookingsRead(bookings=found):
            return [_say(bookings_line(found), voice)]
        case Price():
            return [_say(price_line(d), voice)]
        case Goodbye():
            return [_say(goodbye_line(), voice), end_call("caller said goodbye")]
        case FreeDates():
            return [_say(free_dates_line(d), voice)]
        case Hold():
            return [wait(HOLD_S)]
        case Reply(text=text):
            return [_say(text, voice)]
        case Sorry():
            return [_say(SORRY, voice)]
        case OffTopic():
            return [_say(OFF_TOPIC, voice)]
        case Handoff(queue=queue, reason=reason, summary=summary, apologise=apologise):
            line = HANDOFF_SORRY if apologise else HANDOFF_LINES.get(queue, HANDOFF_DEFAULT)
            return [_say(line, voice), handoff(queue, reason, summary)]
        case NoFit():
            return [_say(no_fit_line(d), voice)]


def offer_line(o: Offer) -> str:
    """Unit, dates, people, price, then the one question. At most two statements."""
    when = spoken_range(o.start, o.end)
    people = f"for {spoken_number(o.people)} people"
    if o.blocked_day is not None:
        day = spoken_date(o.blocked_day).rsplit(" of ", 1)[0]  # "Saturday the third"
        first = f"{o.unit.name} isn't free on {day}, but I can do {when}, {people}."
    elif o.instead_of is not None and o.why_not == "capacity":
        first = (
            f"{o.instead_of.name} only takes {spoken_number(o.instead_of.capacity)}, so for "
            f"{spoken_number(o.people)} of you I can offer {o.unit.name}, {when}."
        )
    elif o.instead_of is not None:
        reason = _WHY_NOT.get(o.why_not, _WHY_NOT["taken"]).format(was=o.instead_of.name)
        first = f"{reason}, so I can offer {o.unit.name} {people}, {when}."
    elif o.weekend_min:
        first = (
            f"Weekend rentals are two days minimum, so I can offer {o.unit.name} {people}, {when}."
        )
    else:
        first = f"I can offer {o.unit.name}, {when}, {people}."
    return f"{first} That's {spoken_euros(o.total)}. Shall I book it?"


def booked_line(b: Booking, u: Unit) -> str:
    return (
        f"That's booked: {u.name}, {spoken_range(b.start, b.end)}, for "
        f"{spoken_number(b.people)} people. The total is {spoken_euros(b.total)}, and I'll "
        f"text you the details. {ANYTHING_ELSE}"
    )


def bookings_line(found: tuple[tuple[Booking, Unit], ...]) -> str:
    if not found:
        return "I can't see any upcoming bookings under your name. Can I help with anything else?"
    said = [f"{u.name} for {spoken_range(b.start, b.end)}" for b, u in found[:2]]
    return f"You have {' and '.join(said)}. Can I help with anything else?"


def price_line(p: Price) -> str:
    if p.unit is not None and p.start is not None and p.end is not None:
        return (
            f"{p.unit.name} for {spoken_range(p.start, p.end)} comes to {spoken_euros(p.total)}. "
            "Would you like me to check those dates?"
        )
    if p.unit is not None:
        return (
            f"{p.unit.name} is {spoken_euros(p.unit.price_per_day)} a day. Which dates were you "
            "thinking of?"
        )
    low = min(p.units, key=lambda u: u.price_per_day)
    high = max(p.units, key=lambda u: u.price_per_day)
    return (
        f"Our saunas cost from {spoken_euros(low.price_per_day)} a day for {low.name} to "
        f"{spoken_euros(high.price_per_day)} for {high.name}. Which dates were you thinking of?"
    )


# a refusal said once already closes the topic instead of asking the same again (D-98)
_CLOSING_NO_FIT = {
    "too_many": "I'm sorry, none of our saunas can take that many people, so I can't book "
    "this one.",
    "too_light": "I'm sorry, none of our saunas is light enough for that car, so I can't "
    "book this one.",
    "nothing_fits": "I'm sorry, none of our saunas fits all of that.",
    "no_dates": "I'm sorry, I can't find dates that work for that.",
    "no_booking": "I'm sorry, I can't find a booking of yours to change.",
}


def free_dates_line(f: FreeDates) -> str:
    """The coming weekends, how many saunas are free on each, then the one question."""

    def free(n: int) -> str:
        if n == 0:
            return "everything is booked"
        if n == f.units:
            return f"all {spoken_number(n)} saunas are free"
        return "one sauna is free" if n == 1 else f"{spoken_number(n)} saunas are free"

    weekends = "; ".join(f"{spoken_range(a, b)}, {free(n)}" for a, b, n in f.weekends)
    return f"Weekends go first. {weekends}. Weekdays are mostly free. Which dates would you like?"


def no_fit_line(n: NoFit) -> str:
    if n.closing:
        return f"{_CLOSING_NO_FIT.get(n.reason, _CLOSING_NO_FIT['nothing_fits'])} {ANYTHING_ELSE}"
    match n.reason:
        case "too_many":
            return (
                f"I'm sorry, our biggest sauna takes {spoken_number(n.limit)} people, so none "
                "of them can take that many. Could you make it fewer?"
            )
        case "too_light":
            return (
                f"I'm sorry, our lightest sauna weighs {spoken_number(n.limit)} kilos, so your "
                "car can't tow any of them. Is there another car you could use?"
            )
        case "no_booking":
            return bookings_line(())
        case "no_dates":
            return (
                "I'm sorry, nothing that fits is free around those dates. Would other dates work?"
            )
        case _:
            return "I'm sorry, none of our saunas fit that. Would other dates work for you?"


def sms_text(b: Booking, u: Unit) -> str:
    """The written confirmation. A text is read, not heard, so figures are fine here."""
    return (
        f"Pirtis on Wheels: {u.name} is booked {_dates(b)}, {b.people} people, total "
        f"€{b.total}. Booking {b.id}."
    )


def _dates(b: Booking) -> str:
    s, e = date.fromisoformat(b.start), date.fromisoformat(b.end)
    return f"from {_short(s)} to {_short(e)} {e.year}"


def _short(d: date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _say(text: str, voice: Voice) -> Action:
    return say(text, voice[0], voice[1])
