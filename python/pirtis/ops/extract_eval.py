"""The extractor eval: does the model hear the call right?

For every fixture the model sees, a gold file in pirtis/agent/gold/ holds the call
state it should produce. This runs the live extraction k times per fixture and compares
each run field by field with the gold state. A fixture passes when all k runs match
(extractor pass^k). The result separates "the model misheard" from "the policy decided
wrong" and from "the judge misjudged".

    uv run python -m pirtis.ops.extract_eval --runs 3 --json runs/extract-eval.json
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pirtis.agent.policy import normalise_name
from pirtis.agent.understand import CallState, UnderstandError, caller_lines, extract
from pirtis.api.memory import Memory, load_seed
from pirtis.cmd.harness import load_dotenv
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import Recorder, open_ai_from_env

PYTHON = Path(__file__).resolve().parents[2]
GOLD_DIR = PYTHON / "pirtis" / "agent" / "gold"
FIXTURES = PYTHON.parent / "fixtures"
SUITES = {"given": FIXTURES, "extra": FIXTURES / "extra"}

# compared by presence only: the words may differ, but whether the caller gave a
# reason decides whether a heater preference is a hard filter
PRESENCE = {"heater_reason"}


@dataclass
class Gold:
    fixture: str
    suite: str
    state: dict[str, Any]
    ignore: list[str] = field(default_factory=list)
    one_of: list[list[dict[str, Any]]] = field(default_factory=list)


def load_gold(dir: Path = GOLD_DIR) -> list[Gold]:
    return [Gold(**json.loads(p.read_text())) for p in sorted(dir.glob("*.json"))]


def fixture_for(g: Gold) -> Fixture:
    for f in load_fixtures(SUITES[g.suite]):
        if f.id == g.fixture:
            return f
    raise LookupError(f"{g.suite}/{g.fixture}")


def compare(state: CallState, g: Gold, lines: Sequence[str]) -> list[str]:
    """The fields where the model's state differs from gold; empty means a match.

    Names are compared the way the policy uses them (normalised, any case), presence
    fields by being given or not, and each one_of group needs one alternative to hold.
    """
    got = state.model_dump()
    wrong = []
    for name, want in g.state.items():
        if name in g.ignore:
            continue
        have = got[name]
        if name == "full_name":
            same = (normalise_name(have, list(lines)) or "").lower() == (want or "").lower()
        elif name in PRESENCE:
            same = (have is None) == (want is None)
        else:
            same = have == want
        if not same:
            wrong.append(name)
    for group in g.one_of:
        if not any(all(got.get(k) == v for k, v in alt.items()) for alt in group):
            wrong.append("one of " + " | ".join(",".join(alt) for alt in group))
    return wrong


@dataclass
class Result:
    fixture: str
    runs: list[list[str]] = field(default_factory=list)  # wrong fields per run
    got: list[dict[str, Any]] = field(default_factory=list)  # the wrong fields' values per run

    def passed(self) -> bool:
        return all(not r for r in self.runs)


def evaluate(llm: LlmClient, golds: list[Gold], runs: int) -> list[Result]:
    out = []
    for g in golds:
        f = fixture_for(g)
        units = Memory(load_seed(f.seed_path())).list_units()
        res = Result(g.fixture)
        for _ in range(runs):
            try:
                state = extract(f.transcript, llm, units)
            except UnderstandError as e:
                res.runs.append([f"unreadable: {e}"[:80]])
                res.got.append({})
                continue
            wrong = compare(state, g, caller_lines(f.transcript))
            values = state.model_dump()
            res.runs.append(wrong)
            res.got.append({w: values[w] for w in wrong if w in values})
        out.append(res)
    return out


def field_accuracy(golds: list[Gold], results: list[Result]) -> dict[str, float]:
    """Per field: the share of compared runs that got it right."""
    seen: dict[str, int] = {}
    right: dict[str, int] = {}
    for g, r in zip(golds, results, strict=True):
        for name in g.state:
            if name in g.ignore:
                continue
            for wrong in r.runs:
                seen[name] = seen.get(name, 0) + 1
                right[name] = right.get(name, 0) + (name not in wrong)
    return {name: right[name] / seen[name] for name in sorted(seen)}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="extract-eval", description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--id", default="", help="comma-separated fixture ids; default all")
    p.add_argument("--json", default="", help="also write the results here")
    args = p.parse_args(argv)

    load_dotenv(str(PYTHON / ".env"))
    rec = Recorder(open_ai_from_env())
    golds = [g for g in load_gold() if not args.id or g.fixture in args.id.split(",")]
    results = evaluate(rec, golds, args.runs)

    print(f"{'fixture':<26} {'suite':<6} result  wrong fields per run")
    for g, r in zip(golds, results, strict=True):
        detail = "  ".join(
            "ok" if not w else ",".join(f"{n}={got[n]!r}" if n in got else n for n in w)
            for w, got in zip(r.runs, r.got, strict=True)
        )
        print(f"{g.fixture:<26} {g.suite:<6} {'PASS' if r.passed() else 'FAIL':<6}  {detail}")
    passed = sum(r.passed() for r in results)
    acc = field_accuracy(golds, results)
    stats = rec.stats()
    cost = stats.prompt_tokens * 0.15e-6 + stats.completion_tokens * 0.6e-6
    print(f"\nextractor pass^{args.runs}: {passed}/{len(results)} fixtures")
    print("field accuracy: " + ", ".join(f"{k} {v:.0%}" for k, v in acc.items()))
    print(
        f"model calls {stats.calls}, tokens in/out {stats.prompt_tokens}/"
        f"{stats.completion_tokens}, est. ${cost:.4f}"
    )
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(
                {
                    "runs": args.runs,
                    "pass_k": passed / len(results) if results else 0.0,
                    "fields": acc,
                    "fixtures": {r.fixture: r.runs for r in results},
                },
                indent=2,
            )
        )
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
