"""The turn gate: decides, without any model call, when not to answer normally.

Speaking while the caller is still talking is the one mistake a phone receptionist
cannot take back, so this runs before anything else. In order:

1. the caller isn't done: still talking, only a filler ("uh"), or asked us to hold
2. the caller wants a person, or cut us off in anger: announce a colleague, transfer
3. we spoke last: wait a moment, check in after a silence, hang up after a second one

and two that keep the thread of the call, read from our own last line (D-98):
- after "Is there anything else I can help with?", a short "no, that's all" closes the call
- after "Are you still there?", a short "yes, I'm here" brings back what we last asked
"""

import re

from pirtis.agent.policy import Handoff, identified_customer
from pirtis.agent.reading import emotion, last_message, latest_caller_turn
from pirtis.agent.speech import (
    ANYTHING_ELSE,
    check_in_line,
    goodbye_line,
    hang_up_line,
    render,
    tone,
)
from pirtis.transcript.types import Action, Item, Transcript, end_call, say, wait

WAIT_S = 1.5  # the caller is mid-thought, or we only just spoke
HOLD_S = 8.0  # the caller asked us to hold on
SILENCE_S = 6.0  # this long without a reply after we spoke means check in
ANGER_TRANSFER = 0.7  # with a barge-in; anger alone only calms the voice

_FILLERS = {"uh", "uhh", "um", "umm", "er", "erm", "hm", "hmm", "mm", "mhm", "ah", "eh", "so"}
_HOLD = re.compile(
    r"\b(hang on|hold on|bear with me|let me (ask|check)"
    r"|(one|just a|give me a) (sec|second|moment|minute))\b",
    re.IGNORECASE,
)
# a request for a person, and not "the eight person one" or "the site manager says"
_HUMAN = re.compile(
    r"\b(talk|speak|put me through|transfer me|connect me|get me)\b[^.?!]{0,25}?"
    r"\b(person|human|someone|somebody|manager|operator)\b"
    r"|\b(a|an|real|actual|live)\s+(person|human)\b"
    r"|\b(someone|somebody)\b[^.?!]{0,20}?\b(talk|speak)\b",
    re.IGNORECASE,
)
_STILL_THERE = re.compile(r"\bstill there\b", re.IGNORECASE)
_GOODBYE = re.compile(r"\b(goodbye|bye)\b", re.IGNORECASE)


def gate(t: Transcript) -> list[Action] | None:
    """Actions that settle this turn on their own, or None to go on to understanding."""
    last = last_message(t)
    if last is None:
        return None

    if last.role == "user":
        if not (last.is_final and last.speech_final) or is_filler_only(last.content):
            return [wait(WAIT_S)]
        if is_hold(last.content):
            return [wait(HOLD_S)]
        turn = latest_caller_turn(t)
        if wants_human(" ".join(it.content for it in turn)):
            return _transfer(t, "caller asked for a person", apologise=False)
        if emotion(turn, "Anger") >= ANGER_TRANSFER and barged_in(t):
            return _transfer(t, "caller upset and cut us off", apologise=True)
        said = " ".join(it.content for it in turn)
        ours = _our_lines(t)
        if ours and ANYTHING_ELSE in ours[-1].content and is_closing(said):
            return [say(goodbye_line(), *tone(t)), end_call("caller had nothing else")]
        if ours and ours[-1].content == check_in_line() and is_presence(said):
            before = next((o.content for o in reversed(ours[:-1]) if not o.interrupted), "")
            again = before if before.endswith("?") and before != check_in_line() else _HOW
            return [say(f"Great. {again}", *tone(t))]
        return None

    # we spoke last
    if last.interrupted or t.now - last.end < SILENCE_S:
        return [wait(WAIT_S)]
    if _GOODBYE.search(last.content):
        return [end_call("caller silent after goodbye")]
    voice = tone(t)
    if _STILL_THERE.search(last.content):
        return [say(hang_up_line(), *voice), end_call("no answer after a check-in")]
    return [say(check_in_line(), *voice)]


_HOW = "What can I do for you?"
# a whole turn made only of these words says nothing new
_CLOSING = {
    "no", "nope", "nah", "nothing", "else", "thanks", "thank", "you", "that's", "thats",
    "that", "is", "all", "it", "everything", "for", "now", "i'm", "im", "good", "fine", "set",
    "bye", "goodbye", "cheers", "great", "perfect", "lovely", "ok", "okay", "so", "much",
    "very", "that'll", "be", "we're", "were", "done",
}  # fmt: skip
_CLOSING_MUST = {
    "no",
    "nope",
    "nah",
    "nothing",
    "thanks",
    "thank",
    "bye",
    "goodbye",
    "cheers",
    "done",
}
_PRESENCE = {
    "yes", "yeah", "yep", "yup", "i'm", "im", "i", "am", "here", "still", "sorry", "hello",
    "hi", "ok", "okay", "sure", "go", "on", "ahead", "there",
}  # fmt: skip


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("’", "'"))


def is_closing(text: str) -> bool:
    """A short turn with nothing new but "no, that's all, thanks"."""
    w = _words(text)
    return 0 < len(w) <= 8 and set(w) <= _CLOSING and bool(set(w) & _CLOSING_MUST)


def is_presence(text: str) -> bool:
    """A short "yes, I'm here" after a check-in, with nothing new in it."""
    w = _words(text)
    return 0 < len(w) <= 6 and set(w) <= _PRESENCE


def _our_lines(t: Transcript) -> list[Item]:
    """Our lines before the caller's latest turn."""
    turn = latest_caller_turn(t)
    first = turn[0] if turn else None
    out: list[Item] = []
    for it in t.items:
        if it is first:
            break
        if it.type == "message" and it.role == "assistant":
            out.append(it)
    return out


def wants_human(text: str) -> bool:
    return bool(_HUMAN.search(text))


def is_filler_only(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.lower())
    return bool(words) and all(w in _FILLERS for w in words)


def is_hold(text: str) -> bool:
    """A short request to wait. "Hang on, could we push it a week?" is a question, not a
    hold, so a question mark or a long line rules it out."""
    return bool(_HOLD.search(text)) and "?" not in text and len(text.split()) <= 10


def barged_in(t: Transcript) -> bool:
    """True when the caller's latest turn cut into our previous line."""
    turn = latest_caller_turn(t)
    if not turn:
        return False
    ours = None
    for it in t.items:
        if it is turn[0]:
            break
        if it.type == "message" and it.role == "assistant":
            ours = it
    return ours is not None and (ours.interrupted or turn[0].start < ours.end)


def _transfer(t: Transcript, reason: str, apologise: bool) -> list[Action]:
    customer = identified_customer(t) or "not identified"
    said = " ".join(it.content for it in latest_caller_turn(t))
    summary = f'Caller {customer}. {reason}. Last said: "{said[:160]}"'
    return render(Handoff("front_desk", reason, summary, apologise), tone(t))
