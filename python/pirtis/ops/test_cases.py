"""Where a case comes from: the kit's own cases are never counted with ours."""

from collections import Counter
from pathlib import Path

from pirtis.ops.cases import GROUPS, RED_TEAM, group_of, index

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def test_a_case_is_grouped_by_its_folder_and_number() -> None:
    assert group_of("fixtures", 3) == "given"  # the kit's own, whatever its number
    assert group_of("extra", 24) == "edge"
    assert group_of("extra", 34) == "hidden"
    assert group_of("extra", 50) == "red_team"
    assert group_of("extra", 62) == "promoted"
    assert group_of("extra", None) == "other"
    assert group_of("variants", 2) == "variants"
    assert group_of("variants-candidates", 2) == "variants"
    assert list(RED_TEAM) == list(range(50, 60))


def test_every_case_on_disk_has_its_group() -> None:
    groups = index(FIXTURES)
    counts = Counter(groups.values())
    assert counts["given"] == 14
    assert {g for (s, _), g in groups.items() if s == "fixtures"} == {"given"}
    assert {g for (s, _), g in groups.items() if s == "extra"} <= {
        "edge", "hidden", "red_team", "promoted"
    }  # fmt: skip
    assert groups[("extra", "ignore-your-instructions")] == "red_team"
    assert groups[("extra", "nine-people-hear-the-largest-sauna-seats-eight")] == "promoted"


def test_the_kits_own_group_comes_first() -> None:
    assert GROUPS[0].key == "given" and GROUPS[0].family == "given"
    assert {g.family for g in GROUPS[1:]} == {"ours", "generated"}
