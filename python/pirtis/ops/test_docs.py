"""The documents stay true to the repo: every one is linked from the README and has an
up-to-date PDF, the test-case page matches the cases, and the README names the agent
version the code is at.

When one of these fails: link the new document from the README's table; run
`make fixtures-doc` after changing a case; run `python3 docs/tools/make_pdfs.py` after
editing a document (it needs Chrome); say in the README where the new agent version
stands.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from pirtis.agent.version import AGENT_VERSION
from pirtis.ops import fixture_doc

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "README.md").read_text(encoding="utf-8")

# our documents; the kit's other-language READMEs (go/, ts/, rust/) are left as received,
# and the raw files under reports/ are evidence, indexed by reports/README.md
DOCUMENTS = [
    "README.md",
    "SPEC.md",
    "BUILD_LOG.md",
    "deploy/README.md",
    "python/README.md",
    "reports/README.md",
    "tests/human/README.md",
    *sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")),
    *sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs/architecture").glob("*.md")),
]


def _pdf_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_pdfs", ROOT / "docs/tools/make_pdfs.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_document_is_reachable_from_the_readme() -> None:
    missing = [
        d
        for d in DOCUMENTS[1:]
        if f"]({d})" not in README and f"]({Path(d).parent.as_posix()}/)" not in README
    ]
    assert not missing, f"link these from the README's document table: {missing}"


def test_every_document_has_a_pdf() -> None:
    printed = {f for _, files in _pdf_tool().DOCS.values() for f in files}
    missing = [d for d in DOCUMENTS if d not in printed]
    assert not missing, f"add these to DOCS in docs/tools/make_pdfs.py: {missing}"


def test_no_pdf_is_older_than_its_document() -> None:
    tool = _pdf_tool()
    manifest = json.loads((ROOT / "docs/pdf/SOURCES.json").read_text())
    stale = []
    for name, (_, files) in tool.DOCS.items():
        now = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:16] for f in files}
        if manifest.get(name) != now or not (ROOT / "docs/pdf" / name).exists():
            stale.append(name)
    assert not stale, f"rebuild with python3 docs/tools/make_pdfs.py {' '.join(stale)}"


def test_the_test_case_page_matches_the_cases() -> None:
    page = fixture_doc.render()
    assert "**not described**" not in page, "add a line to TESTS in pirtis/ops/fixture_doc.py"
    assert (ROOT / "docs/fixtures.md").read_text(encoding="utf-8") == page, (
        "docs/fixtures.md is out of date: run make fixtures-doc"
    )


def test_the_readme_says_where_the_current_agent_stands() -> None:
    assert AGENT_VERSION in README, (
        f"the agent is now {AGENT_VERSION}: say in the README's 'Where it stands' whether "
        "it has been measured"
    )
