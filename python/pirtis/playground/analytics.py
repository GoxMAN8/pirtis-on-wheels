"""What the admin console shows, from content-free records and the seed.

Every function here reads turn records (no text, no names, the phone only hashed), gate
runs (synthetic fixtures) or the seed's units and calendar, and returns plain JSON.
None of it can name a customer, because none of its inputs holds one.
"""

import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import Any

from pirtis.agent.rules import DAY, days, free_days, violations
from pirtis.api.memory import Memory, Seed
from pirtis.api.types import (
    CODE_BAD_DATES,
    CODE_BAD_PEOPLE,
    CODE_CAPACITY_EXCEEDED,
    CODE_MIN_DAYS_WEEKEND,
    CODE_OVERLAPS_EXISTING,
    CODE_POWER_REQUIRED,
    CODE_TOW_WEIGHT_EXCEEDED,
    CODE_UNAVAILABLE,
    STATUS_BOOKED,
    ApiError,
    AvailabilityQuery,
    BookingRequest,
    Site,
    Unit,
)
from pirtis.ops import cases
from pirtis.ops.gate_report import PRICE_IN, PRICE_OUT, RunRow
from pirtis.ops.human import RUBRIC, Card
from pirtis.ops.model import Calibration, GateRun, HumanRating, Turn

UNKNOWN_CALLER = "no customer matches"  # how the policy words a hand-off of a stranger
INTENTS = ["book", "change", "cancel", "check_booking", "price", "human", "goodbye"]
PSI_WARN, PSI_ALERT = 0.10, 0.25  # the usual bands (D-43)
PSI_MIN_TURNS = 30  # below this, either window is too small for PSI to mean anything
type Groups = Mapping[tuple[str, str], str]  # (suite, fixture) -> group (ops/cases.py)


def funnel(turns: list[Turn]) -> dict[str, Any]:
    """Each call from start to booking, and how calls left early."""
    calls: dict[str, list[Turn]] = {}
    for t in sorted(turns, key=lambda t: t.ts):
        calls.setdefault(t.session_id, []).append(t)

    def identified(ts: list[Turn]) -> bool:
        return any(
            any(c.method == "customers.identify" and c.status == 200 for c in t.api_calls)
            and not t.transfer_reason.startswith(UNKNOWN_CALLER)
            for t in ts
        )

    stages: list[tuple[str, Callable[[list[Turn]], bool]]] = [
        ("started", lambda ts: True),
        ("identified", identified),
        ("offered", lambda ts: any(t.decision == "Offer" for t in ts)),
        ("booked", lambda ts: any(t.decision == "Booked" for t in ts)),
    ]
    return {
        "stages": [
            {"stage": name, "calls": sum(test(ts) for ts in calls.values())}
            for name, test in stages
        ],
        "exits": {
            "transferred": sum(
                any("transfer" in t.action_types for t in ts) for ts in calls.values()
            ),
            "hung_up": sum(any("end_call" in t.action_types for t in ts) for ts in calls.values()),
            "fallback": sum(any(t.fallback for t in ts) for ts in calls.values()),
        },
    }


def intent_group(intent: str) -> str:
    return intent if intent in INTENTS else "other"


def psi(current: dict[str, int], baseline: dict[str, int]) -> dict[str, Any]:
    """Population stability index of the intent mix, current window against baseline."""
    n, m = sum(current.values()), sum(baseline.values())
    if n < PSI_MIN_TURNS or m < PSI_MIN_TURNS:
        return {"psi": None, "band": "too few", "current_turns": n, "baseline_turns": m}
    eps = 1e-4
    value = 0.0
    for k in set(current) | set(baseline):
        p = max(current.get(k, 0) / n, eps)
        q = max(baseline.get(k, 0) / m, eps)
        value += (p - q) * math.log(p / q)
    band = "alert" if value > PSI_ALERT else "warn" if value > PSI_WARN else "stable"
    return {"psi": round(value, 3), "band": band, "current_turns": n, "baseline_turns": m}


def mix(now: dict[str, int], before: dict[str, int]) -> list[dict[str, Any]]:
    """Each intent's share of turns in the current window and in the one before."""
    n, m = sum(now.values()), sum(before.values())
    return [
        {
            "intent": i,
            "now": round(now.get(i, 0) / n, 4) if n else 0.0,
            "before": round(before.get(i, 0) / m, 4) if m else 0.0,
            "now_turns": now.get(i, 0),
            "before_turns": before.get(i, 0),
        }
        for i in [*INTENTS, "other"]
        if now.get(i) or before.get(i)
    ]


def _pct(xs: list[int], p: float) -> int:
    # nearest rank, as the gate report computes it
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))] if xs else 0


def latency(turns: list[Turn]) -> dict[str, Any]:
    """The latency ECDF of turns that called the model; zero-call turns are counted apart."""
    xs = sorted(t.latency_ms for t in turns if t.llm_calls > 0)
    return {
        "n": len(xs),
        "zero_call_turns": sum(t.llm_calls == 0 for t in turns),
        "points": [{"ms": x, "share": round((i + 1) / len(xs), 4)} for i, x in enumerate(xs)],
        "p50": _pct(xs, 0.5),
        "p95": _pct(xs, 0.95),
    }


def conversations(turns: list[Turn], *, now: datetime, days: int) -> dict[str, Any]:
    """The Conversations view: the last `days`, with the `days` before as the drift
    baseline."""
    since, before = now - timedelta(days=days), now - timedelta(days=2 * days)
    cur = [t for t in turns if t.ts >= since]
    base = [t for t in turns if before <= t.ts < since]
    now_mix = Counter(intent_group(t.intent) for t in cur if t.intent)
    before_mix = Counter(intent_group(t.intent) for t in base if t.intent)
    model = [t for t in cur if t.llm_calls > 0]
    tin = sum(t.input_tokens for t in cur)
    tout = sum(t.output_tokens for t in cur)
    api = Counter((c.method, c.status) for t in cur for c in t.api_calls)
    return {
        "days": days,
        "turns": len(cur),
        "calls": len({t.session_id for t in cur}),
        "funnel": funnel(cur),
        "mix": mix(now_mix, before_mix),
        "drift": psi(now_mix, before_mix),
        "latency": latency(cur),
        "tokens": [t.input_tokens + t.output_tokens for t in model],
        "zero_call_share": round(1 - len(model) / len(cur), 3) if cur else None,
        "est_cost_usd": round(tin * PRICE_IN + tout * PRICE_OUT, 4),
        "api": [{"method": m, "status": s, "calls": n} for (m, s), n in sorted(api.items())],
    }


def turns_view(turns: list[Turn]) -> list[dict[str, Any]]:
    """Turn records for listing, newest first, without even the hashed phone."""
    ordered = sorted(turns, key=lambda t: t.ts, reverse=True)
    return [t.model_dump(mode="json", exclude={"phone_hash", "call_id"}) for t in ordered]


def fleet(seed: Seed, start: date, n: int) -> dict[str, Any]:
    """Each unit's next n days: free, booked, a turnaround day, or blocked. Never who."""
    window = days(start, start + (n - 1) * DAY)
    rows = []
    for u in seed.units:
        state = dict.fromkeys(window, "free")
        for b in seed.bookings:
            if b.unit_id != u.id or b.status != STATUS_BOOKED:
                continue
            bs, be = date.fromisoformat(b.start), date.fromisoformat(b.end)
            for d in (bs - DAY, be + DAY):
                if state.get(d) == "free":
                    state[d] = "turnaround"
            for d in days(bs, be):
                if d in state:
                    state[d] = "booked"
        for bl in seed.blocked:
            if bl.unit_id == u.id:
                for d in days(date.fromisoformat(bl.start), date.fromisoformat(bl.end)):
                    if d in state and state[d] != "booked":
                        state[d] = "blocked"
        rows.append(
            {
                **_unit(u),
                "days": [{"date": d.isoformat(), "state": state[d]} for d in window],
            }
        )
    return {"from": start.isoformat(), "days": n, "units": rows}


def _unit(u: Unit) -> dict[str, Any]:
    return {
        "id": u.id,
        "name": u.name,
        "capacity": u.capacity,
        "weight_kg": u.gross_weight_kg,
        "heater": u.heater,
        "needs_power": u.needs_power,
        "price_per_day": u.price_per_day,
    }


def _why(code: str, u: Unit, req: BookingRequest) -> str:
    return {
        CODE_CAPACITY_EXCEEDED: f"It seats {u.capacity}; you need {req.people}.",
        CODE_TOW_WEIGHT_EXCEEDED: (
            f"It weighs {u.gross_weight_kg} kg; the car tows {req.tow_capacity_kg} kg."
        ),
        CODE_POWER_REQUIRED: "Its electric heater needs mains power at the site.",
        CODE_MIN_DAYS_WEEKEND: "A booking that touches a weekend is two days minimum.",
        CODE_UNAVAILABLE: "It's booked, between bookings or in for service on some of those days.",
        CODE_OVERLAPS_EXISTING: "The caller already has a booking on those days.",
        CODE_BAD_DATES: "Those dates don't make a range.",
        CODE_BAD_PEOPLE: "It needs at least one person.",
    }.get(code, code)


def what_if(
    seed: Seed, *, people: int, tow_kg: int, power: bool, start: str, end: str
) -> dict[str, Any]:
    """Would each sauna take this booking, and if not, why not, in words. The decision
    engine's own rule mirror answers; nothing here is a second copy of the rules."""
    mem = Memory(seed)
    try:
        free = {
            a.unit_id: free_days(a.free)
            for a in mem.availability(AvailabilityQuery(from_=start, to=end))
        }
    except ApiError:  # dates that don't parse; violations() names the problem
        free = {}
    rows = []
    for u in seed.units:
        req = BookingRequest("", u.id, start, end, people, Site(power), tow_kg)
        codes = violations(req, u, free.get(u.id, set()), [])
        rows.append(
            {**_unit(u), "fits": not codes, "codes": codes, "why": [_why(c, u, req) for c in codes]}
        )
    return {"units": rows}


def _group(groups: Groups, suite: str, fixture: str) -> str:
    """A case's group from the index; a case no longer on disk falls back to its folder."""
    return groups.get((suite, fixture)) or cases.group_of(suite, None)


def by_family(rows: list[RunRow], groups: Groups) -> list[dict[str, Any]]:
    """A gate run's result split by where its cases come from, so the kit's own cases are
    never mixed with ours: per family, cases passing all runs, deterministic and judge."""
    by_case: dict[tuple[str, str], list[RunRow]] = {}
    for x in rows:
        by_case.setdefault((x.suite, x.fixture), []).append(x)
    out = []
    for family, label in cases.FAMILIES.items():
        mine = [v for k, v in by_case.items() if cases.BY_KEY[_group(groups, *k)].family == family]
        if mine:
            out.append(
                {
                    "family": family,
                    "label": label,
                    "cases": len(mine),
                    "passed_cases": sum(all(x.passed for x in v) for v in mine),
                    "deterministic_passed_cases": sum(
                        all(x.deterministic_ok for x in v) for v in mine
                    ),
                    "judge_passed_cases": sum(all(x.judge_ok for x in v) for v in mine),
                }
            )
    return out


def gate_runs_view(runs: list[GateRun], groups: Groups | None = None) -> list[dict[str, Any]]:
    """Gate run summaries, newest first, without the per-run rows but split by family."""
    ordered = sorted(runs, key=lambda r: r.run_ts, reverse=True)
    return [
        {
            **r.model_dump(mode="json", exclude={"rows"}),
            "key": r.key(),
            "by_family": by_family(r.rows, groups or {}),
        }
        for r in ordered
    ]


def thermal(runs: list[GateRun], groups: Groups | None = None) -> dict[str, Any]:
    """Rows are fixtures, columns are runs grouped by version, oldest first; each cell is
    pass, fail, judge (only the judge failed it) or none (not in that run). Rows come in
    groups, the kit's own cases first (ops/cases.py)."""
    groups = groups or {}
    columns: list[dict[str, Any]] = []
    cells: dict[tuple[str, str], dict[int, str]] = {}
    for r in sorted(runs, key=lambda r: r.run_ts):
        for n, rows in groupby(sorted(r.rows, key=lambda x: x.run), key=lambda x: x.run):
            col = len(columns)
            columns.append(
                {"agent_version": r.agent_version, "run_ts": r.run_ts.isoformat(), "run": n}
            )
            for x in rows:
                state = "pass" if x.passed else "judge" if x.deterministic_ok else "fail"
                cells.setdefault((x.suite, x.fixture), {})[col] = state

    def order(k: tuple[str, str]) -> tuple[int, str, str]:
        return (cases.ORDER[_group(groups, *k)], k[1], k[0])

    return {
        "columns": columns,
        "groups": cases.groups_view(),
        "families": cases.FAMILIES,
        "rows": [
            {
                "suite": suite,
                "fixture": fixture,
                "group": _group(groups, suite, fixture),
                "cells": [cells[(suite, fixture)].get(i, "none") for i in range(len(columns))],
            }
            for suite, fixture in sorted(cells, key=order)
        ],
    }


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def human_eval(
    ratings: list[HumanRating],
    cards: list[Card],
    outcomes: dict[str, str | None],
    calibration: Calibration | None,
) -> dict[str, Any]:
    """The Human eval view: per card, how people rated the calls and whether each call's
    own record reached the card's outcome; the rubric's spread; the latest calibration."""
    rows = []
    for c in cards:
        rs = [r for r in ratings if r.scenario_id == c.id]
        known = [outcomes[r.session_id] for r in rs if outcomes.get(r.session_id) is not None]
        rows.append(
            {
                "id": c.id,
                "title": c.title,
                "persona": c.persona,
                "outcome": c.outcome,
                "ratings": len(rs),
                "raters": len({r.rater for r in rs}),
                "goal": _mean([float(r.goal_achieved) for r in rs]),
                "system_outcome": _mean([float(o == c.outcome) for o in known]),
                **{item: _mean([float(getattr(r, item)) for r in rs]) for item in RUBRIC},
                "sessions": sorted({r.session_id for r in rs}),
            }
        )
    spread = [
        {
            "item": item,
            "mean": _mean([float(getattr(r, item)) for r in ratings]),
            "min": min(int(getattr(r, item)) for r in ratings),
            "max": max(int(getattr(r, item)) for r in ratings),
        }
        for item in RUBRIC
        if ratings
    ]
    return {
        "ratings": len(ratings),
        "raters": len({r.rater for r in ratings}),
        "scenarios": len(cards),
        "scenarios_rated": sum(1 for row in rows if row["ratings"]),
        "goal": _mean([float(r.goal_achieved) for r in ratings]),
        "cards": rows,
        "items": spread,
        "notes": [
            {
                "scenario": r.scenario_id,
                "rater": r.rater,
                "note": r.note,
                "created": r.created.isoformat(),
            }
            for r in sorted(ratings, key=lambda r: r.created, reverse=True)
            if r.note
        ],
        "calibration": calibration.model_dump(mode="json") if calibration else None,
    }
