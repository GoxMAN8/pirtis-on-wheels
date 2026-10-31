"""The records we own: five versioned entities, each field with a privacy class.

The rental data (customers, units, bookings) stays in the kit's API; these are what the
playground and the gate job keep about calls, feedback, ratings and gate runs. Each one
is a frozen pydantic model that rejects unknown fields (D-49), carries schema_version,
and says where it lives (partition and key) and how long it is kept.

The privacy class says what a field reveals on its own:
- personal: what a person said, or wrote about a call (transcripts, notes, bookings)
- pseudonymous: links to a person only through another record (ids, the phone hash,
  a rater's initials)
- none: reveals nothing about anyone (counts, versions, times, verdicts)
"""

from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pirtis.ops.gate_report import RunRow, summarise

type Pii = Literal["none", "pseudonymous", "personal"]
PII: tuple[Pii, ...] = ("none", "pseudonymous", "personal")

NONE = Field(json_schema_extra={"pii": "none"})
PSEUDO = Field(json_schema_extra={"pii": "pseudonymous"})
PERSONAL = Field(json_schema_extra={"pii": "personal"})
SCORE = Field(ge=1, le=5)

GATE_K = 3  # the frozen gate is pass^3 = 1.0 (D-21)


def _day(ts: datetime) -> str:
    return ts.astimezone(UTC).date().isoformat()


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    KIND: ClassVar[str]
    RETENTION_DAYS: ClassVar[int]
    DAILY: ClassVar[bool] = False  # partitioned by UTC day, so a date query can skip folders

    schema_version: Annotated[Literal[1], NONE] = 1

    def partition(self) -> str:
        raise NotImplementedError

    def key(self) -> str:
        raise NotImplementedError

    def when(self) -> datetime:
        """The time retention and date queries count from."""
        raise NotImplementedError


class Session(Entity):
    """One playground call, saved after every turn so it survives a restart."""

    KIND = "session"
    RETENTION_DAYS = 30
    DAILY = True

    session_id: Annotated[str, PSEUDO]
    started: Annotated[AwareDatetime, NONE]
    agent_version: Annotated[str, NONE]
    caller_phone_hash: Annotated[str, PSEUDO]
    turns: Annotated[int, NONE]
    status: Annotated[Literal["open", "ended", "transferred"], NONE]
    transcript: Annotated[dict[str, Any], PERSONAL]
    bookings: Annotated[list[dict[str, Any]], PERSONAL]  # the ones this call made or changed
    steps: Annotated[list[dict[str, Any]], PERSONAL] = []  # per turn: state, actions, API calls

    def partition(self) -> str:
        return _day(self.started)

    def key(self) -> str:
        return self.session_id

    def when(self) -> datetime:
        return self.started


class Feedback(Entity):
    """A thumbs up or down on one receptionist turn; a human may promote it to a fixture."""

    KIND = "feedback"
    RETENTION_DAYS = 30

    turn_id: Annotated[str, PSEUDO]
    session_id: Annotated[str, PSEUDO]
    agent_version: Annotated[str, NONE]
    verdict: Annotated[Literal["up", "down"], NONE]
    note: Annotated[str, PERSONAL]
    transcript: Annotated[dict[str, Any], PERSONAL]
    created: Annotated[AwareDatetime, NONE]
    promoted_fixture: Annotated[str, NONE] = ""

    def partition(self) -> str:
        return self.agent_version

    def key(self) -> str:
        return self.turn_id

    def when(self) -> datetime:
        return self.created


class HumanRating(Entity):
    """A tester's rubric for one scenario call: the goal, then four 1-5 scores."""

    KIND = "rating"
    RETENTION_DAYS = 365

    scenario_id: Annotated[str, NONE]
    session_id: Annotated[str, PSEUDO]
    rater: Annotated[str, PSEUDO]  # initials, never a name
    agent_version: Annotated[str, NONE]
    goal_achieved: Annotated[bool, NONE]
    natural: Annotated[int, NONE, SCORE]
    numbers_clear: Annotated[int, NONE, SCORE]
    one_question: Annotated[int, NONE, SCORE]
    turn_taking: Annotated[int, NONE, SCORE]  # silences and interruptions handled
    note: Annotated[str, PSEUDO]  # about a call made with a seed persona
    created: Annotated[AwareDatetime, NONE]

    def partition(self) -> str:
        return self.scenario_id

    def key(self) -> str:
        return f"{self.session_id}~{self.rater}"

    def when(self) -> datetime:
        return self.created


class GateRun(Entity):
    """One run of the frozen gate: the totals the gate report prints, and every run."""

    KIND = "gate_run"
    RETENTION_DAYS = 365

    run_ts: Annotated[AwareDatetime, NONE]
    agent_version: Annotated[str, NONE]
    passed: Annotated[bool, NONE]
    k: Annotated[int, NONE]
    cases: Annotated[int, NONE]
    runs: Annotated[int, NONE]
    passed_cases: Annotated[int, NONE]
    deterministic_passed_cases: Annotated[int, NONE]
    judge_passed_cases: Annotated[int, NONE]
    model_calls: Annotated[int, NONE]
    zero_call_runs: Annotated[int, NONE]
    input_tokens: Annotated[int, NONE]
    output_tokens: Annotated[int, NONE]
    est_cost_usd: Annotated[float, NONE]
    p50_ms: Annotated[int, NONE]
    p95_ms: Annotated[int, NONE]
    failed: Annotated[list[str], NONE]
    extract_cases: Annotated[int | None, NONE] = None
    extract_passed_cases: Annotated[int | None, NONE] = None
    rows: Annotated[list[RunRow], NONE] = []

    @classmethod
    def from_rows(
        cls,
        rows: list[RunRow],
        *,
        agent_version: str,
        run_ts: datetime,
        extract: tuple[int, int] | None = None,
    ) -> "GateRun":
        """Passed means the frozen gate held: every case passed all of at least GATE_K runs.
        extract is the extractor eval's (cases, cases passing all runs), when it ran; it is
        reported beside the gate, never part of it (§7)."""
        t = summarise(rows)["total"]
        passed = t["cases"] > 0 and t["passed_cases"] == t["cases"] and t["k"] >= GATE_K
        cases, ok = extract if extract else (None, None)
        return cls(
            run_ts=run_ts,
            agent_version=agent_version,
            passed=passed,
            rows=rows,
            extract_cases=cases,
            extract_passed_cases=ok,
            **t,
        )

    def partition(self) -> str:
        return self.agent_version

    def key(self) -> str:
        return self.run_ts.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def when(self) -> datetime:
        return self.run_ts


class ApiCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str
    status: int
    code: str


class Turn(Entity):
    """The agent's own telemetry record for one turn (no content), plus where it ran."""

    KIND = "turn"
    RETENTION_DAYS = 90
    DAILY = True

    telemetry_version: Annotated[int, NONE]
    ts: Annotated[AwareDatetime, NONE]
    source: Annotated[Literal["playground", "gate"], NONE]
    session_id: Annotated[str, PSEUDO, Field(min_length=1)]
    call_id: Annotated[str, PSEUDO]
    turn_id: Annotated[str, PSEUDO]
    agent_version: Annotated[str, NONE]
    gate: Annotated[str, NONE]
    intent: Annotated[str, NONE]
    decision: Annotated[str, NONE]
    action_types: Annotated[list[str], NONE]
    llm_calls: Annotated[int, NONE]
    input_tokens: Annotated[int, NONE]
    output_tokens: Annotated[int, NONE]
    latency_ms: Annotated[int, NONE]
    api_calls: Annotated[list[ApiCall], NONE]
    fallback: Annotated[bool, NONE]
    transfer_reason: Annotated[str, NONE]
    phone_hash: Annotated[str, PSEUDO]

    @classmethod
    def from_record(cls, record: dict[str, Any], *, source: str, session_id: str) -> "Turn":
        """From the JSON line the agent logs on "pirtis.turn". A field the agent adds and
        this model doesn't know fails here, so the two can't drift apart silently."""
        fields = {k: v for k, v in record.items() if k not in ("event", "otel")}
        return cls.model_validate({**fields, "source": source, "session_id": session_id})

    def partition(self) -> str:
        return _day(self.ts)

    def key(self) -> str:
        # a turn id repeats across the gate's runs of one fixture; the session doesn't
        return f"{self.session_id}~{self.turn_id}"

    def when(self) -> datetime:
        return self.ts


class Calibration(Entity):
    """One judge calibration: how often the kit's judge agrees with people about the same
    calls, item by item, and how often raters agree with each other."""

    KIND = "calibration"
    RETENTION_DAYS = 365

    run_ts: Annotated[AwareDatetime, NONE]
    judge_model: Annotated[str, NONE]
    ratings: Annotated[int, NONE]
    items: Annotated[dict[str, dict[str, float | int | None]], NONE]  # item: n, agree, kappa
    overall_agree: Annotated[float | None, NONE]
    overall_kappa: Annotated[float | None, NONE]
    rater_pairs: Annotated[int, NONE]
    rater_kappa: Annotated[float | None, NONE]

    def partition(self) -> str:
        return _day(self.run_ts)

    def key(self) -> str:
        return self.run_ts.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def when(self) -> datetime:
        return self.run_ts


ENTITIES: tuple[type[Entity], ...] = (
    Session, Feedback, HumanRating, GateRun, Turn, Calibration,
)  # fmt: skip
