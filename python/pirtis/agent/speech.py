"""Speech: what the receptionist says, written by code.

Every number, date and price is spoken as words, and every reply asks at most one
question. The model never writes these lines, so it cannot get a fact wrong in them.
"""

from datetime import date

from pirtis.agent.policy import Ask, Booked, Decision, Handoff, NoFit, Offer
from pirtis.transcript.types import Action, say, transfer

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
]  # fmt: skip
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORDINALS = [
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
    "seventeenth", "eighteenth", "nineteenth", "twentieth",
]  # fmt: skip


def spoken_number(n: int) -> str:
    """0 to 9999 in words, British style: 240 is "two hundred and forty"."""
    if n < 0:
        return "minus " + spoken_number(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("-" + _ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + (" and " + spoken_number(rest) if rest else "")
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        head = _ONES[thousands] + " thousand"
        if not rest:
            return head
        return head + (" and " if rest < 100 else " ") + spoken_number(rest)
    raise ValueError(f"no spoken form for {n}")


def spoken_ordinal(n: int) -> str:
    """1 to 31: "first" to "thirty-first"."""
    if n <= 20:
        return _ORDINALS[n]
    tens, ones = divmod(n, 10)
    if not ones:
        return _TENS[tens][:-1] + "ieth"  # thirty -> thirtieth
    return _TENS[tens] + "-" + _ORDINALS[ones]


def spoken_date(iso: str) -> str:
    """ "Tuesday the sixth of October"."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%A')} the {spoken_ordinal(d.day)} of {d.strftime('%B')}"


def spoken_range(start: str, end: str) -> str:
    """One day, or "Tuesday the sixth to Wednesday the seventh of October", with the
    month said once when both days share it."""
    if start == end:
        return spoken_date(start)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if (s.year, s.month) == (e.year, e.month):
        return (
            f"{s.strftime('%A')} the {spoken_ordinal(s.day)} to "
            f"{e.strftime('%A')} the {spoken_ordinal(e.day)} of {e.strftime('%B')}"
        )
    return f"{spoken_date(start)} to {spoken_date(end)}"


def spoken_euros(n: int) -> str:
    return spoken_number(n) + (" euro" if n == 1 else " euros")


_QUESTIONS = {
    "name": "Can I get your full name, please?",
    "dates": "Which dates were you thinking of?",
    "people": "How many of you will there be?",
    "tow": "How much can your car tow, braked? It's in the car's handbook.",
    "power": "Is there mains power where the sauna is going?",
}

_WHY_NOT = {
    "weight": "{was} is too heavy for your car",
    "power": "{was} needs mains power",
    "taken": "{was} isn't free then",
}


def render(d: Decision) -> list[Action]:
    """The actions for one decision."""
    match d:
        case Ask(slot=slot):
            return [_say(_QUESTIONS[slot])]
        case Offer():
            return [_say(offer_line(d))]
        case Booked(booking=b, unit=u):
            return [
                _say(
                    f"That's booked: {u.name}, {spoken_range(b.start, b.end)}, "
                    f"for {spoken_number(b.people)} people. The total is {spoken_euros(b.total)}."
                )
            ]
        case Handoff(queue=queue, reason=reason):
            return [
                _say("I'll put you through to a colleague who can help."),
                transfer(queue, reason),
            ]
        case NoFit():
            return [_say("I'm sorry, none of our saunas fit that. Would other dates work for you?")]


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


def _say(text: str) -> Action:
    return say(text, "neutral", 1.0)
