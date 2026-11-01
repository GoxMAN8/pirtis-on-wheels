"""A second reader, off the call path: re-reads simulator calls after they happen and
flags turns where it hears the caller differently from the agent.

The agent's reading of each turn (the CallState it accepted) is checked against an
independent reading of the same call so far: another prompt, and by default another,
stronger model. It never runs inside decide(), so it adds no silence to a call and the
kit's pin on gpt-4o-mini there doesn't bind it (D-03). Where the two disagree on a fact
that drives a booking, the turn goes to the admin console's Feedback queue with both
readings, for a person to judge; nothing becomes a test case on its own (D-30).

Real calls carry no gold answer, so the agreement rate per field is the closest thing to
an accuracy figure they have: a field whose agreement drops is the place to look first.

    uv run python -m pirtis.ops.review [--days 1] [--model gpt-4o] [--dry-run] [--json out.json]

Needs LLM_API_KEY; REVIEW_MODEL or --model picks the model (with LLM_BASE_URL set to
Azure OpenAI, a deployment name). Calls go to the same model vendor the agent already
uses, and its records stay in the store (docs/architecture/data-flow.md).
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from pirtis.agent.policy import normalise_name
from pirtis.agent.understand import (
    CallState,
    calendar,
    caller_lines,
    fold,
    render_transcript,
    strict_schema,
)
from pirtis.api.memory import load_seed
from pirtis.cmd.harness import load_dotenv
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import (
    Message,
    OpenAI,
    Request,
    Response,
    ResponseFormat,
    RetryableError,
    _from_wire,
    _to_wire,
    open_ai_from_env,
)
from pirtis.ops.model import Feedback, Session
from pirtis.ops.store import FileStore, store_from_env
from pirtis.ops.turns import seen_at
from pirtis.transcript.types import Transcript

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "fixtures" / "seeds" / "base.json"
DEFAULT_MODEL = "gpt-4o"

# facts that change what is booked: a disagreement on one of these flags the turn
FACTS = ["full_name", "people", "start", "end", "tow_kg", "unit", "power", "accepted_offer"]
INTENTS = list(get_args(CallState.model_fields["intent"].annotation))  # the agent's own set


class Reading(BaseModel):
    """What the caller has said by the end of the call so far, as the reviewer hears it."""

    model_config = ConfigDict(extra="forbid")

    intent: str | None = Field(description="What the caller wants in their latest turn.")
    full_name: str | None = Field(description="The caller's own full name, as said.")
    people: int | None = Field(description="How many people; the higher of a hedge.")
    start: str | None = Field(description="First day asked for, YYYY-MM-DD.")
    end: str | None = Field(description="Last day asked for, YYYY-MM-DD.")
    tow_kg: int | None = Field(description="Braked towing capacity in kg the caller stated.")
    unit: str | None = Field(description="The sauna the caller named or described, if any.")
    power: bool | None = Field(description="Mains power at the site, if the caller said.")
    accepted_offer: bool = Field(
        description="True only if the receptionist's last line offered a specific booking "
        "or change and the caller's latest turn plainly agreed to it, with no conditions."
    )


INSTRUCTIONS = """You check the work of a phone receptionist for a mobile-sauna rental.
Read the call below and write down what the caller has said by the end of it: only what
the caller said, never what you think they should want. Null for anything not said in
this call. If they corrected themselves, keep the correction. Dates are YYYY-MM-DD, read
off the calendar you are given. A sauna is one of the ids listed, and only if the caller
named or described it. A towing capacity is a number the caller gave, in kg."""


class ReviewModel(OpenAI):
    """The kit's own client, asking another model. The kit's client always names
    gpt-4o-mini; this one runs after the call, outside decide(), so it may ask any."""

    def __init__(self, inner: OpenAI, model: str) -> None:
        super().__init__(inner.api_key, inner.base_url, inner.timeout)
        self.model = model

    def _chat_once(self, req: Request) -> Response:
        body = json.dumps({**_to_wire(req), "model": self.model}, ensure_ascii=False).encode()
        http = HttpRequest(
            self.base_url + "/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
                "api-key": self.api_key,
            },
        )
        try:
            with urlopen(http, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as e:
            if e.code == 429 or e.code >= 500:
                raise RetryableError(f"review model: status {e.code}") from e
            raise RuntimeError(f"review model: status {e.code}: {e.read()[:300]!r}") from e
        except (URLError, TimeoutError, OSError) as e:
            raise RetryableError(f"review model: {e}") from e
        return _from_wire(json.loads(raw))


def read(llm: LlmClient, t: Transcript, unit_lines: str, unit_ids: list[str]) -> Reading:
    resp = llm.chat(
        Request(
            messages=[
                Message(role="system", content=INSTRUCTIONS + "\n\nThe saunas:\n" + unit_lines),
                Message(
                    role="user",
                    content=calendar(t.today) + "\n\nThe call:\n" + render_transcript(t),
                ),
            ],
            temperature=0.0,
            response_format=ResponseFormat(
                name="reading",
                schema=strict_schema(Reading, {"unit": unit_ids, "intent": INTENTS}),
                strict=True,
            ),
        )
    )
    return Reading.model_validate(json.loads(resp.content))


def _norm(name: str, value: Any, lines: list[str]) -> Any:
    if name == "full_name":
        return fold(normalise_name(value, lines) or "").lower() or None
    if name == "accepted_offer":
        return bool(value)
    return value


@dataclass
class Turn:
    session_id: str
    turn_id: str
    agent_version: str
    differs: dict[str, tuple[Any, Any]]  # field -> (the agent's value, the reviewer's)
    intent: tuple[str, str]


@dataclass
class Review:
    model: str
    sessions: int = 0
    turns: list[Turn] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)  # turn ids put in the Feedback queue
    errors: int = 0

    def agreement(self) -> dict[str, float]:
        """Per field: the share of reviewed turns where both readings agree."""
        n = len(self.turns)
        out = {f: sum(f not in t.differs for t in self.turns) / n for f in FACTS} if n else {}
        if n:
            out["intent"] = sum(t.intent[0] == t.intent[1] for t in self.turns) / n
        return out


def review(
    store: FileStore, llm: LlmClient, model: str, since: datetime, dry_run: bool = False
) -> Review:
    units = load_seed(SEED).units
    unit_ids = [u.id for u in units]
    unit_lines = "\n".join(f"- {u.id}: {u.name}, {u.capacity} people, {u.heater}" for u in units)
    out = Review(model=model)
    for session in store.query(Session, since=since):
        out.sessions += 1
        t = Transcript.from_dict(session.transcript)
        for step in session.steps:
            if not step.get("state"):  # the turn gate settled it: the model wasn't asked
                continue
            seen = replace(t, items=seen_at(t.items, step["at"]), now=step["at"])
            try:
                theirs = read(llm, seen, unit_lines, unit_ids).model_dump()
            except Exception:  # one unreadable turn doesn't stop the review
                out.errors += 1
                continue
            ours = step["state"]
            lines = caller_lines(seen)
            differs = {
                f: (ours.get(f), theirs.get(f))
                for f in FACTS
                if _norm(f, ours.get(f), lines) != _norm(f, theirs.get(f), lines)
            }
            turn = Turn(
                session.session_id,
                step["turn_id"],
                step["agent_version"],
                differs,
                (str(ours.get("intent")), str(theirs.get("intent"))),
            )
            out.turns.append(turn)
            if differs and _flag(store, session, turn, model, dry_run):
                out.flagged.append(turn.turn_id)
    return out


def _flag(store: FileStore, session: Session, turn: Turn, model: str, dry_run: bool) -> bool:
    """Puts the turn in the Feedback queue, unless a person already rated it."""
    if store.get(Feedback, turn.agent_version, turn.turn_id) is not None:
        return False
    if dry_run:
        return True
    shown = "; ".join(
        f"{f}: agent {_show(a)}, reviewer {_show(b)}" for f, (a, b) in turn.differs.items()
    )
    store.put(
        Feedback(
            turn_id=turn.turn_id,
            session_id=session.session_id,
            agent_version=turn.agent_version,
            verdict="down",
            note=f"Reviewer ({model}) heard this turn differently. {shown}",
            transcript=session.transcript,
            created=datetime.now(UTC),
        )
    )
    return True


def _show(v: Any) -> str:
    return "—" if v is None else str(v)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="review", description=__doc__.splitlines()[0])
    p.add_argument("--days", type=float, default=1.0, help="calls from the last N days")
    p.add_argument("--model", default=os.environ.get("REVIEW_MODEL", DEFAULT_MODEL))
    p.add_argument("--dry-run", action="store_true", help="report, but queue nothing")
    p.add_argument("--json", default="")
    args = p.parse_args(argv)

    load_dotenv(".env")
    llm = ReviewModel(open_ai_from_env(), args.model)
    since = datetime.now(UTC) - timedelta(days=args.days)
    started = time.monotonic()
    r = review(store_from_env(), llm, args.model, since, dry_run=args.dry_run)
    took = time.monotonic() - started
    verb = "would queue" if args.dry_run else "queued"
    print(
        f"reviewed {len(r.turns)} model turns in {r.sessions} calls with {r.model} "
        f"({took:.0f} s); {verb} {len(r.flagged)} for a person; {r.errors} unreadable"
    )
    for f, share in r.agreement().items():
        print(f"  {f:<15} agree {share:.0%}")
    for t in r.turns:
        if t.differs:
            shown = "; ".join(f"{f} {_show(a)} vs {_show(b)}" for f, (a, b) in t.differs.items())
            print(f"  {t.session_id} {t.turn_id}: {shown}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "model": r.model,
                    "sessions": r.sessions,
                    "turns": len(r.turns),
                    "flagged": r.flagged,
                    "errors": r.errors,
                    "agreement": r.agreement(),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
