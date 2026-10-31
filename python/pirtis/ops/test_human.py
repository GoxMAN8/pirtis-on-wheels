"""The scenario cards are checked against the seed before anyone calls with them."""

from pathlib import Path

import pytest

from pirtis.ops.human import RUBRIC, load_cards, outcome_of


def test_fifteen_cards_each_a_seed_persona_or_a_stranger() -> None:
    cards = load_cards()
    assert len(cards) == 15
    assert {c.persona for c in cards} <= {
        "Jonas Jonaitis", "Tomas Petrauskas", "Eglė Butkutė", "Rasa Paulauskienė", "unknown",
    }  # fmt: skip
    assert {c.outcome for c in cards} == {"booked", "moved", "cancelled", "transferred", "none"}


def test_a_card_with_a_real_person_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "cards.yaml"
    bad.write_text(
        "- {id: x, title: t, persona: Gediminas Someone, goal: g, facts: [], twist: w,"
        " outcome: none, success: s}\n"
    )
    with pytest.raises(ValueError, match="not a seed customer"):
        load_cards(bad)


def test_rubric_statements_are_positive() -> None:
    """The judge can flip the polarity of a negated statement (D-51)."""
    assert all(" not " not in s and "n't" not in s for s in RUBRIC.values())


def test_a_calls_outcome_comes_from_its_own_record() -> None:
    assert outcome_of("ended", "booked") == "booked"
    assert outcome_of("transferred", "gathering") == "transferred"
    assert outcome_of("ended", "gathering") == "none"


def test_every_weight_on_a_card_is_a_cars_figure() -> None:
    """A YAML flow list splits "2,000 kg" at the comma unless it's quoted."""
    import re

    for card in load_cards():
        for fact in card.facts:
            for kg in re.findall(r"([\d,]+)\s*kg", fact):
                assert int(kg.replace(",", "")) >= 500, (card.id, fact)
