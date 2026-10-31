"""Variants make "the same rules, other callers, cars and dates" from the given cases. The
transforms must move every fact consistently, and the oracle (the kit's own API, never
our policy) must keep only variants whose right answer is still knowable."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pirtis.harness.fixture import load_fixtures
from pirtis.ops.variants import (
    GIVEN,
    Variant,
    generate,
    noise,
    oracle,
    persona,
    read,
    shift,
    shift_spoken,
    tow,
    write,
)

TODAY = date(2026, 9, 28)


def raw(fid: str) -> dict[str, Any]:
    return read(next(p for p in GIVEN.glob("[0-9][0-9]_*.yaml") if f"id: {fid}\n" in p.read_text()))


def users(d: dict[str, Any]) -> list[str]:
    return [str(i["content"]) for i in d["transcript"]["items"] if i.get("role") == "user"]


@pytest.mark.parametrize(
    "said, week_later",
    [
        (
            "Saturday the third to Sunday the fourth of October",
            "Saturday the tenth to Sunday the eleventh of October",
        ),
        (
            "the thirtieth of September to the second of October",
            "the seventh of October to the ninth of October",
        ),
        ("the, um, sixth and seventh. of October", "the, um, thirteenth and fourteenth of October"),
        ("the tenth, eleventh. six of us", "the seventeenth, eighteenth. six of us"),
        (
            "the sixth and seventh, the Tuesday Wednesday",
            "the thirteenth and fourteenth, the Tuesday Wednesday",
        ),
    ],
)
def test_spoken_dates_move_with_their_weekdays(said: str, week_later: str) -> None:
    assert shift_spoken(said, TODAY, 7) == week_later


def test_a_shift_moves_today_every_written_date_and_the_expectation() -> None:
    moved = shift(raw("change-of-plan"), 7)
    assert moved is not None and moved["transcript"]["today"] == "2026-10-05"
    text = json.dumps(moved)
    assert "2026-10-03" not in text and "2026-10-10" in text  # bk_91 set up a week later


def test_a_call_naming_a_seed_booking_never_shifts() -> None:
    d = raw("happy-path")
    d["transcript"]["items"][0]["content"] += " (bk_77)"
    assert shift(d, 7) is None


def test_a_persona_swap_moves_phone_name_spelling_and_id_together() -> None:
    swapped = persona(raw("happy-path"), "cus_19")
    assert swapped is not None
    text = json.dumps(swapped, ensure_ascii=False)
    assert "Jonas" not in text and "J O N A I T I S" not in text and "+37062223344" not in text
    assert "Eglė Butkutė. B U T K U T Ė" in users(swapped) and "cus_19" in text


def test_noise_keeps_names_and_the_facts() -> None:
    rough = noise(raw("tow-weight"))
    lines = " ".join(users(rough))
    assert (
        "Tomas Petrauskas" in lines and "1500" in lines and lines == lines.replace("Golf", "golf")
    )
    assert ",," not in lines and noise(raw("tow-weight")) == rough  # repeatable


def test_a_tow_change_stays_in_its_band() -> None:
    moved = tow(raw("tow-weight"))
    assert (
        moved is not None and moved[1] == 1400 and "fourteen hundred" in " ".join(users(moved[0]))
    )


def test_the_oracle_drops_a_variant_the_api_would_refuse() -> None:
    d = raw("slot-stolen")
    d["expect"]["bookings"][0].update(unit="unit_vidutine", start="2026-09-30", end="2026-10-01")
    v = Variant("x", "slot-stolen", ["test"], d, None)
    [src] = [f for f in load_fixtures(GIVEN) if f.id == "slot-stolen"]
    assert "refuses" in oracle(src, None, v)


def test_kept_variants_load_in_the_kit_and_every_drop_says_why(tmp_path: Path) -> None:
    variants, _, _ = generate()
    paths = write(variants, tmp_path)
    assert len(load_fixtures(tmp_path)) == len(paths) >= 40
    assert all(
        v.verdict
        for v in variants
        if not any(v.id.replace("--", "__").replace("-", "_") in p.name for p in paths)
    )
    review = (tmp_path / "REVIEW.md").read_text()
    assert f"Kept {len(paths)} of {len(variants)}" in review
