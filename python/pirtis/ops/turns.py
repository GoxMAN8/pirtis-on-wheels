"""Reading a stored call turn by turn."""

from pirtis.transcript.types import Item


def seen_at(items: list[Item], at: float) -> list[Item]:
    """The items decide saw on the turn that ran at `at`: everything before that turn's
    own API calls and replies (an output item carries only its end time)."""
    return [i for i in items if (i.end if i.type == "function_call_output" else i.start) < at]
