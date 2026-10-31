"""The human evaluation's scenario cards and rubric (tests/human/README.md).

Cards are read once and checked against the seed: every persona is a seed customer or
the playground's unknown number, every outcome is one the call's own record can show.
The rubric's four scored items carry the statement the judge is asked about the same
calls (pirtis/ops/calibrate.py); the goal is asked as the card's own success sentence.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pirtis.api.memory import load_seed

ROOT = Path(__file__).resolve().parents[3]
CARDS = ROOT / "tests" / "human" / "scenarios.yaml"
SEED = ROOT / "fixtures" / "seeds" / "base.json"
UNKNOWN = "unknown"

# an item a person scores 1-5, and the statement the judge checks on each reply
RUBRIC: dict[str, str] = {
    "natural": "The reply sounds like a capable person speaking on the phone.",
    "numbers_clear": "Every price, date and figure in the reply is easy to follow when heard.",
    "one_question": "The reply asks at most one question.",
    "turn_taking": "The reply fits the moment: it answers what the caller has just said.",
}
GOOD = 4  # a score of 4 or 5 counts as "yes" when people and the judge are compared

type Outcome = Literal["booked", "moved", "cancelled", "transferred", "none"]


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    persona: str  # a seed customer's full name, or "unknown"
    goal: str
    facts: list[str]
    twist: str
    outcome: Outcome
    success: str


def load_cards(path: Path = CARDS) -> list[Card]:
    cards = TypeAdapter(list[Card]).validate_python(yaml.safe_load(path.read_text()))
    names = {c.name for c in load_seed(SEED).customers}
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise ValueError("card ids must be unique")
    for c in cards:
        if c.persona != UNKNOWN and c.persona not in names:
            raise ValueError(f"{c.id}: {c.persona!r} is not a seed customer")
    return cards


_WROTE: dict[str, Outcome] = {"booked": "booked", "moved": "moved", "cancelled": "cancelled"}


def outcome_of(status: str, card_status: str) -> Outcome:
    """What a call ended in, from its own record: the call's status and its card's."""
    if card_status in _WROTE:
        return _WROTE[card_status]
    return "transferred" if status == "transferred" else "none"
