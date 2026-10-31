"""Judge calibration: does the kit's judge agree with people about the same calls?

For every rated call, each rubric item's statement (pirtis/ops/human.py) goes to the
kit's own judge for every receptionist reply in the call, and the call passes the item
only if every reply does; the goal is asked once, on the last reply, as the card's
success sentence. People pass an item at a score of 4 or more. Per item and overall we
report how often the two agree and Cohen's kappa (agreement beyond chance), and kappa
between raters who rated the same call. The judge is the kit's and is never changed
(D-47); this measures how far its verdicts can be trusted.

    uv run python -m pirtis.ops.calibrate
"""

import itertools
from dataclasses import replace
from datetime import UTC, datetime

from pirtis.cmd.harness import load_dotenv
from pirtis.harness.judge import judge
from pirtis.llm.client import MODEL, Recorder, open_ai_from_env
from pirtis.llm.client import Client as LlmClient
from pirtis.ops.human import GOOD, RUBRIC, Card, load_cards
from pirtis.ops.model import Calibration, HumanRating, Session
from pirtis.ops.store import FileStore, store_from_env
from pirtis.ops.turns import seen_at
from pirtis.transcript.types import Transcript

ITEMS = ["goal", *RUBRIC]


def kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa for two yes/no raters; None when it's undefined (both constant)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance == 1 else round((observed - chance) / (1 - chance), 3)


def human_says(r: HumanRating, item: str) -> bool:
    return r.goal_achieved if item == "goal" else int(getattr(r, item)) >= GOOD


def judge_says(llm: LlmClient, session: Session, statement: str, last_only: bool) -> bool:
    """True when the judge passes the statement on every receptionist reply (or the last)."""
    t = Transcript.from_dict(session.transcript)
    replies = [s for s in session.steps if any(e.get("type") == "speech" for e in s["effects"])]
    for step in replies[-1:] if last_only else replies:
        seen = replace(t, items=seen_at(t.items, step["at"]), now=step["at"])
        [verdict] = judge(llm, seen, step["effects"], [statement])
        if not verdict.passed:
            return False
    return True


def calibrate(store: FileStore, llm: LlmClient, cards: dict[str, Card]) -> Calibration:
    rated: list[tuple[HumanRating, Session, Card]] = []
    for r in store.query(HumanRating):
        day = f"{r.session_id[:4]}-{r.session_id[4:6]}-{r.session_id[6:8]}"
        session = store.get(Session, day, r.session_id)
        if session is not None and r.scenario_id in cards:  # an expired call can't be judged
            rated.append((r, session, cards[r.scenario_id]))

    verdicts: dict[tuple[str, str], bool] = {}  # one judge pass per call and item
    people: dict[str, list[bool]] = {i: [] for i in ITEMS}
    machine: dict[str, list[bool]] = {i: [] for i in ITEMS}
    for r, session, card in rated:
        for item in ITEMS:
            k = (session.session_id, item)
            if k not in verdicts:
                statement = (
                    f"The call has reached this outcome: {card.success}"
                    if item == "goal"
                    else RUBRIC[item]
                )
                verdicts[k] = judge_says(llm, session, statement, last_only=item == "goal")
            people[item].append(human_says(r, item))
            machine[item].append(verdicts[k])

    items: dict[str, dict[str, float | int | None]] = {}
    for item in ITEMS:
        a, b = people[item], machine[item]
        agree = round(sum(x == y for x, y in zip(a, b, strict=True)) / len(a), 3) if a else None
        items[item] = {"n": len(a), "agree": agree, "kappa": kappa(a, b)}
    pooled_a = [x for i in ITEMS for x in people[i]]
    pooled_b = [x for i in ITEMS for x in machine[i]]

    # raters who rated the same call, compared item by item
    by_call: dict[str, list[HumanRating]] = {}
    for r, _, _ in rated:
        by_call.setdefault(r.session_id, []).append(r)
    ra: list[bool] = []
    rb: list[bool] = []
    pairs = 0
    for rs in by_call.values():
        for x, y in itertools.combinations(rs, 2):
            pairs += 1
            ra += [human_says(x, i) for i in ITEMS]
            rb += [human_says(y, i) for i in ITEMS]
    overall = (
        round(sum(x == y for x, y in zip(pooled_a, pooled_b, strict=True)) / len(pooled_a), 3)
        if pooled_a
        else None
    )
    return Calibration(
        run_ts=datetime.now(UTC),
        judge_model=MODEL,
        ratings=len(rated),
        items=items,
        overall_agree=overall,
        overall_kappa=kappa(pooled_a, pooled_b),
        rater_pairs=pairs,
        rater_kappa=kappa(ra, rb),
    )


def main() -> None:
    load_dotenv(".env")
    llm = Recorder(open_ai_from_env())
    store = store_from_env()
    result = calibrate(store, llm, {c.id: c for c in load_cards()})
    store.put(result)
    print(f"judge calibration on {result.ratings} rated calls ({llm.stats().calls} judge calls)")
    for item, row in result.items.items():
        print(f"  {item:<14} n={row['n']:<3} agree={row['agree']}  kappa={row['kappa']}")
    print(f"  overall        agree={result.overall_agree}  kappa={result.overall_kappa}")
    print(f"  raters         pairs={result.rater_pairs}  kappa={result.rater_kappa}")


if __name__ == "__main__":
    main()
