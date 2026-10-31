"""Small helpers that read a transcript. Shared by the turn gate, the policy and speech,
so the meaning of "the caller's latest turn" is defined once."""

from pirtis.transcript.types import Item, Transcript


def last_message(t: Transcript) -> Item | None:
    """The last spoken line, from either side."""
    for it in reversed(t.items):
        if it.type == "message":
            return it
    return None


def latest_caller_turn(t: Transcript) -> list[Item]:
    """The caller's lines since we last spoke. A caller who pauses and goes on leaves
    several lines in a row; together they are one turn."""
    turn: list[Item] = []
    for it in reversed(t.items):
        if it.type != "message":
            continue
        if it.role == "assistant":
            break
        turn.append(it)
    return list(reversed(turn))


def emotion(items: list[Item], *names: str) -> float:
    """The strongest score of any of the named emotions across these lines, 0 if none."""
    scores = [e.score for it in items if it.prosody for e in it.prosody.emotions if e.name in names]
    return max(scores, default=0.0)
