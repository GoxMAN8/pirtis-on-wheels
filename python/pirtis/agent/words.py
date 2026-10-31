"""Numbers, dates and prices as spoken words. Pure functions, no imports from the rest of
the agent, so the speech templates and the say handler's digit guard share one source."""

import re
from datetime import date

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


# The digit guard. Patterns run in this order, most specific first, so "2,000 kg" is
# read as a weight and "€420" as a price before any bare number is touched.
_NUM = r"\d[\d,]*(?:\.\d+)?"
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_EUR_BEFORE = re.compile(r"€\s?(" + _NUM + ")")
_EUR_AFTER = re.compile(r"(" + _NUM + r")\s?(?:€|\bEUR\b|\beuros?\b)")
_PERCENT = re.compile(r"(" + _NUM + r")\s?%")
_KG = re.compile(r"(" + _NUM + r")\s?(?:kg|kgs|kilos?|kilograms?)\b")
_THOUSAND = re.compile(r"\b(\d+(?:\.\d+)?)k\b")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_NUMBER = re.compile(r"\+?" + _NUM)
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+")


def digits_to_words(text: str) -> str:
    """Rewrites every digit in text as words, so nothing reaches the voice as a figure.
    Long digit strings and codes are read out digit by digit."""
    text = _ISO_DATE.sub(_date_words, text)
    text = _TIME.sub(_time_words, text)
    text = _EUR_BEFORE.sub(lambda m: _euros(m[1]), text)
    text = _EUR_AFTER.sub(lambda m: _euros(m[1]), text)
    text = _PERCENT.sub(lambda m: _amount(m[1]) + " percent", text)
    text = _KG.sub(lambda m: _amount(m[1]) + " kilos", text)
    text = _THOUSAND.sub(lambda m: _amount(m[1], scale=1000), text)
    text = _ORDINAL.sub(lambda m: _ordinal_words(int(m[1])), text)
    return _NUMBER.sub(lambda m: _amount(m[0]), text)


def _date_words(m: re.Match[str]) -> str:
    try:
        return spoken_date(m[0])
    except ValueError:
        return _spell(m[0])


def _time_words(m: re.Match[str]) -> str:
    hours, minutes = int(m[1]), int(m[2])
    if minutes == 0:
        return spoken_number(hours) + " o'clock"
    return spoken_number(hours) + " " + ("oh " if minutes < 10 else "") + spoken_number(minutes)


def _euros(s: str) -> str:
    plain = s.replace(",", "") if _GROUPED.fullmatch(s) else s
    if plain.isdigit() and int(plain) <= 9999 and not (len(plain) > 1 and plain[0] == "0"):
        return spoken_euros(int(plain))
    return _amount(s) + " euros"


def _ordinal_words(n: int) -> str:
    return spoken_ordinal(n) if 1 <= n <= 31 else spoken_number(n)


def _amount(s: str, scale: int = 1) -> str:
    """One number as words: grouped thousands, decimals, a k suffix, or digit by digit
    when it is too long or starts with zero (a code, not a quantity)."""
    if s.startswith("+"):
        return "plus " + _spell(s[1:])
    plain = s.replace(",", "") if _GROUPED.fullmatch(s) else s
    if "," in plain:
        return " comma ".join(_amount(part) for part in plain.split(",") if part)
    if "." in plain:
        value = float(plain) * scale
        if scale != 1 and value.is_integer() and value <= 9999:
            return spoken_number(int(value))
        whole, frac = plain.split(".", 1)
        return _amount(whole) + " point " + _spell(frac)
    if len(plain) > 1 and plain[0] == "0":
        return _spell(plain)
    n = int(plain) * scale
    return spoken_number(n) if n <= 9999 else _spell(str(n))


def _spell(digits: str) -> str:
    return " ".join(_ONES[int(c)] for c in digits if c.isdigit())
