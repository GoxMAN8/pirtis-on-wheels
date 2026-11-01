"""The prompt quotes no test case: what the model always sees shares no run of words with
what any test case's caller says.

An example in the prompt copied from a test case teaches the model that one answer and
flatters its score: the prompt once carried "the wood smoke sets off my asthma", the
words of the kit's own turnaround case. This checks the system prompt and the schema's
field descriptions against the caller lines of every suite, the generated variants
included, since those are kept as a held-out set (docs/data-science.md §3).
"""

import json
import re
from pathlib import Path

from pirtis.agent.understand import HEATERS, CallState, strict_schema, system_prompt
from pirtis.api.memory import load_seed
from pirtis.harness.fixture import load_fixtures

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
SUITES = ["", "extra", "variants", "variants-candidates"]
N = 4  # four words in a row: long enough to be a quote, short enough to catch one


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def _runs(s: str) -> set[str]:
    w = _words(s)
    return {" ".join(w[i : i + N]) for i in range(len(w) - N + 1)}


def _prompt() -> str:
    units = load_seed(FIXTURES / "seeds" / "base.json").units
    schema = strict_schema(CallState, {"unit": [u.id for u in units], "heater_pref": HEATERS})
    return system_prompt(units) + "\n" + json.dumps(schema)


def _caller_lines() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for suite in SUITES:
        folder = FIXTURES / suite if suite else FIXTURES
        if not folder.is_dir():
            continue
        for f in load_fixtures(folder):
            lines = [
                it.content
                for it in f.transcript.items
                if it.type == "message" and it.role == "user"
            ]
            out[f"{suite or 'given'}/{f.id}"] = lines
    return out


def test_the_prompt_quotes_no_test_case() -> None:
    prompt = _runs(_prompt())
    shared = {
        case: sorted(prompt & _runs(line))
        for case, lines in _caller_lines().items()
        for line in lines
        if prompt & _runs(line)
    }
    assert not shared, f"the prompt quotes these test cases: {shared}"


def test_the_check_would_catch_the_old_example() -> None:
    old = "Heater reason (e.g. 'the wood smoke sets off my asthma')."
    turnaround = _caller_lines()["given/turnaround"]
    assert any(_runs(old) & _runs(line) for line in turnaround)
