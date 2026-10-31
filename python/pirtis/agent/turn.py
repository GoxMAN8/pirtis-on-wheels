"""The turn gate: decides, without any model call, when not to answer normally.

Speaking while the caller is still talking is the one mistake a phone receptionist
cannot take back, so this runs before anything else.
"""

from pirtis.transcript.types import Action, Item, Transcript, wait

WAIT_S = 1.5


def gate(t: Transcript) -> list[Action] | None:
    """Actions that settle this turn on their own, or None to go on to understanding."""
    last = last_message(t)
    if last is not None and last.role == "user" and not (last.is_final and last.speech_final):
        return [wait(WAIT_S)]
    return None


def last_message(t: Transcript) -> Item | None:
    for it in reversed(t.items):
        if it.type == "message":
            return it
    return None
