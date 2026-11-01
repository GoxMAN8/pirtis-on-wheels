"""The held-out variants through the real agent, at zero tokens: do the rules hold on
callers, dates and cars nobody tuned on, when the caller was heard right?

`make heldout` runs the variants with the live model and judge, so a failure there can be
the model mishearing, the policy deciding wrong, or the judge. This separates the second:
each variant's model answer is scripted as the source case's gold reading, moved by the
same transform that made the variant (another caller's name, dates a week or two later,
another tow figure, each quote taken from the variant's own caller lines), and the kit's
runner checks the action, the bookings and its invariants. Judge statements are left to
the live run. On top of the kit's checks, a variant must decide as its source did (the
metamorphic relation): the same kind of decision, the same saunas named, the same kinds
of action, since only the caller, the dates, the car or the noise changed.

    uv run python -m pirtis.ops.variant_replay
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.agent.understand import caller_lines, fold, quote_in
from pirtis.harness.fixture import Fixture
from pirtis.harness.run import Runner, RunResult
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.ops.heldout import transform
from pirtis.ops.variants import SEED, Variant, _fixture, generate

QUOTES = ("tow_quote", "unit_quote", "consent_quote", "heater_reason")

# passes every criterion: the judge is the live run's business, not this one's
PASS_JUDGE = Fake(
    default=Response(
        content=json.dumps({f"criterion_{i}": {"reason": "ok", "pass": True} for i in range(1, 9)})
    )
)


def reading(v: Variant, src: Fixture, var: Fixture) -> dict[str, Any] | None:
    """The source's gold reading, moved the way the variant was; None when the source's
    turn never reaches the model (waiting, silence, a transfer)."""
    if v.ask is None:
        return None
    state = dict(v.ask)
    for tag in v.transforms:
        if tag.startswith("persona-") and state.get("full_name"):
            cid = tag.removeprefix("persona-")
            state["full_name"] = next(c.name for c in SEED.customers if c.id == cid)
        if tag.startswith("shift") and state.get("change_target_start"):
            k = int(tag.removeprefix("shift"))
            moved = date.fromisoformat(state["change_target_start"]) + timedelta(days=k)
            state["change_target_start"] = moved.isoformat()
    ours, theirs = caller_lines(src.transcript), caller_lines(var.transcript)
    for field in QUOTES:
        quote = state.get(field)
        if not quote:
            continue
        at = next((i for i, line in enumerate(ours) if quote_in(quote, [line])), None)
        if at is not None and at < len(theirs):
            state[field] = theirs[at]  # the same line, as this caller said it
    return state


@dataclass
class Outcome:
    variant: str
    transform: str
    passed: bool
    why: str


def _run(f: Fixture, state: dict[str, Any] | None, dir: Any) -> RunResult:
    llm = Fake(script=[Response(content=json.dumps(state))] if state else [])
    runner = Runner(llm=llm, judge=PASS_JUDGE, decide=decide, handlers=handlers)
    return runner.run(replace(f, dir=dir)).runs[0]


def shape(r: RunResult) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """What a transform must not change: the kind of decision, the saunas the reply
    names, and the kinds of action. The caller's name and the dates may change."""
    decision = next((str(e.get("decision")) for e in r.effects if e.get("type") == "note"), "")
    said = fold(" ".join(str(e.get("text")) for e in r.effects if e.get("type") == "speech"))
    units = tuple(sorted(u.name for u in SEED.units if fold(u.name) in said))
    kinds = tuple(str(e.get("type")) for e in r.effects if e.get("type") != "note")
    return decision, units, kinds


def replay() -> list[Outcome]:
    variants, sources, asks = generate()
    baseline: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    out = []
    for v in variants:
        if v.verdict:  # the kit's API dropped it: not a held-out case
            continue
        src = sources[v.source]
        if v.source not in baseline:
            baseline[v.source] = shape(_run(src, asks[v.source], src.dir))
        var = _fixture(v.data)
        r = _run(var, reading(v, src, var), src.dir)
        why = r.error or "; ".join(r.failures)
        if not why and shape(r) != baseline[v.source]:
            why = f"decided differently from its source: {shape(r)} vs {baseline[v.source]}"
        out.append(Outcome(v.id, transform(v.id), not why, why))
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="variant-replay", description=__doc__.splitlines()[0])
    p.add_argument("--json", default="")
    args = p.parse_args(argv)
    out = replay()
    total, passed = Counter(o.transform for o in out), Counter(o.transform for o in out if o.passed)
    print(
        "THE RULES ON THE HELD-OUT VARIANTS (zero tokens: heard right, judge left to the live run)"
    )
    for name in sorted(total):
        print(f"  {name:<9} {passed[name]:>2}/{total[name]}")
    print(f"  {'total':<9} {sum(passed.values()):>2}/{len(out)}")
    for o in out:
        if not o.passed:
            print(f"  FAIL {o.variant}: {o.why[:300]}")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump([o.__dict__ for o in out], fh, indent=2)
    sys.exit(0 if all(o.passed for o in out) else 1)


if __name__ == "__main__":
    main()
