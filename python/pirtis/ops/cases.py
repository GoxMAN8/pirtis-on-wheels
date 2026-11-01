"""Where a test case comes from: the kit's own, or ours, and which of ours.

The admin console keeps the kit's 14 cases apart from the ones we wrote, promoted or
generated, so a pass rate never mixes the two. A case's group follows from its folder
and its file's number (fixtures/extra/NN_<title>.yaml), the numbering every case of
ours already follows (SPEC §6).
"""

from dataclasses import dataclass
from pathlib import Path

from pirtis.harness.fixture import Fixture, load_fixtures

# the families a pass rate is split by, in the order they are shown
FAMILIES = {
    "given": "Given by the kit",
    "ours": "Written by us",
    "generated": "Generated",
}


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    family: str
    numbers: range | None = None  # the file numbers in fixtures/extra, for ours


GROUPS = [
    Group("given", "Given by the kit", "given"),
    Group("edge", "Edge cases · 20–29", "ours", range(20, 30)),
    Group("hidden", "Hidden-style · 30–49", "ours", range(30, 50)),
    Group("red_team", "Red team · 50–59", "ours", range(50, 60)),
    Group("promoted", "Promoted from calls · 60+", "ours", range(60, 100)),
    Group("other", "Ours, unnumbered", "ours"),
    Group("variants", "Metamorphic variants", "generated"),
]
BY_KEY = {g.key: g for g in GROUPS}
ORDER = {g.key: i for i, g in enumerate(GROUPS)}
RED_TEAM = BY_KEY["red_team"].numbers or range(0)  # privacy and safety, never cut (D-45)


def group_of(suite: str, number: int | None) -> str:
    """The group of a case in this suite folder ("fixtures", "extra", "variants") whose
    file starts with this number."""
    if suite == "fixtures":
        return "given"
    if suite.startswith("variants"):
        return "variants"
    for g in GROUPS:
        if g.numbers is not None and number in g.numbers:
            return g.key
    return "other"


def numbered(dir: Path) -> list[tuple[int | None, Fixture]]:
    """Each fixture with its file's number; the kit's loader reads the same files in the
    same order."""
    files = sorted(dir.glob("*.yaml"))
    numbers = [int(p.name[:2]) if p.name[:2].isdigit() else None for p in files]
    return list(zip(numbers, load_fixtures(dir), strict=True))


def index(root: Path) -> dict[tuple[str, str], str]:
    """(suite, fixture id) -> group, for every case under the fixtures root."""
    out: dict[tuple[str, str], str] = {}
    for suite in ("fixtures", "extra", "variants"):
        folder = root if suite == "fixtures" else root / suite
        if folder.is_dir():
            for n, f in numbered(folder):
                out[(suite, f.id)] = group_of(suite, n)
    return out


def groups_view() -> list[dict[str, str]]:
    return [{"key": g.key, "label": g.label, "family": g.family} for g in GROUPS]
