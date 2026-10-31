"""The turn gate: decides, without any model call, when not to answer normally.

Speaking while the caller is still talking is the one mistake a phone receptionist
cannot take back, so this runs before anything else. In order:

1. the caller isn't done: still talking, only a filler ("uh"), or asked us to hold
2. the caller wants a person, or cut us off in anger: announce a colleague, transfer
3. we spoke last: wait a moment, check in after a silence, hang up after a second one
"""

import re

from pirtis.agent.policy import Handoff, identified_customer
from pirtis.agent.reading import emotion, last_message, latest_caller_turn
from pirtis.agent.speech import check_in_line, hang_up_line, render, tone
from pirtis.transcript.types import Action, Transcript, end_call, say, wait

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
