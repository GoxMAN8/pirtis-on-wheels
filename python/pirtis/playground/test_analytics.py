"""What the admin console shows, computed from content-free records and the seed: the
call funnel, intent drift, latency, the fleet calendar, the what-if explanations and
the thermal view of gate runs."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pirtis.agent.rules import free_days, violations
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import AvailabilityQuery, BookingRequest, Site
from pirtis.ops.gate_report import RunRow
from pirtis.ops.model import ApiCall, GateRun, Turn
from pirtis.playground.analytics import (
    conversations,
    fleet,
    funnel,
    gate_runs_view,
    latency,
    psi,
    thermal,
    what_if,
)

SEED = Path(__file__).resolve().parents[3] / "fixtures" / "seeds" / "base.json"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
NO_MATCH = "no customer matches this phone and name"


def turn(session: str, n: int, **kw: Any) -> Turn:
    base: dict[str, Any] = {
        "telemetry_version": 1,
        "ts": NOW - timedelta(minutes=10) + timedelta(seconds=n),
        "source": "playground",
        "session_id": session,
        "call_id": session,
        "turn_id": f"{session}-{n}",
        "agent_version": "v1",
        "gate": "none",
        "intent": "book",
        "decision": "Ask",
        "action_types": ["say", "note"],
        "llm_calls": 1,
        "input_tokens": 2000,
        "output_tokens": 150,
        "latency_ms": 1500,
        "api_calls": [],
        "fallback": False,
        "transfer_reason": "",
        "phone_hash": "abc",
    }
    return Turn(**{**base, **kw})


IDENTIFY = [ApiCall(method="customers.identify", status=200, code="")]

CALLS = [
    turn("a", 1, api_calls=IDENTIFY),
    turn("a", 2, decision="Offer"),
    turn("a", 3, decision="Booked", action_types=["say", "sms", "note"]),
    turn("b", 1, api_calls=IDENTIFY),
    turn("b", 2, decision="Offer"),
    turn("b", 3, gate="hang_up", decision="", intent="", llm_calls=0, action_types=["end_call"]),
    turn(
        "c",
        1,
        api_calls=IDENTIFY,
        decision="Handoff",
        action_types=["say", "transfer", "note"],
        transfer_reason=NO_MATCH,
    ),
]


def test_the_funnel_follows_each_call_from_start_to_booking() -> None:
    f = funnel(CALLS)
    assert f["stages"] == [
        {"stage": "started", "calls": 3},
        {"stage": "identified", "calls": 2},
        {"stage": "offered", "calls": 2},
        {"stage": "booked", "calls": 1},
    ]
    assert f["exits"] == {"transferred": 1, "hung_up": 1, "fallback": 0}


def test_psi_is_zero_for_the_same_mix_and_alerts_on_a_shift() -> None:
    same = psi({"book": 60, "price": 40}, {"book": 60, "price": 40})
    assert same["psi"] == 0 and same["band"] == "stable"
    shifted = psi({"book": 10, "price": 90}, {"book": 90, "price": 10})
    assert shifted["psi"] > 0.25 and shifted["band"] == "alert"
    assert psi({"book": 3}, {"price": 2})["band"] == "too few"  # PSI on a handful is noise


def test_latency_counts_only_turns_that_called_the_model() -> None:
    lat = latency(CALLS)
    assert lat["n"] == 6 and lat["zero_call_turns"] == 1
    xs = [p["ms"] for p in lat["points"]]
    assert xs == sorted(xs) and lat["points"][-1]["share"] == 1.0
    assert lat["p50"] == 1500 and lat["p95"] == 1500


def test_the_intent_mix_compares_this_window_with_the_one_before() -> None:
    old = [turn("z", n, ts=NOW - timedelta(days=9), intent="price") for n in range(4)]
    out = conversations(CALLS + old, now=NOW, days=7)
    shares = {m["intent"]: (m["now"], m["before"]) for m in out["mix"]}
    assert shares["book"][1] == 0 and shares["price"] == (0, 1.0)
    assert round(sum(m["now"] for m in out["mix"]), 3) == 1.0
    assert out["drift"]["band"] == "too few"


def test_conversations_never_carry_a_phone_hash_or_a_name() -> None:
    body = json.dumps(conversations(CALLS, now=NOW, days=7))
    assert "abc" not in body and "phone" not in body


def test_the_fleet_shows_busy_days_and_never_who() -> None:
    grid = fleet(load_seed(SEED), date(2026, 9, 28), 14)
    vid = next(r for r in grid["units"] if r["id"] == "unit_vidutine")
    cell = {c["date"]: c["state"] for c in vid["days"]}
    assert cell["2026-09-29"] == "turnaround" and cell["2026-10-03"] == "turnaround"
    assert {cell[d] for d in ("2026-09-30", "2026-10-01", "2026-10-02")} == {"booked"}
    stat = next(r for r in grid["units"] if r["id"] == "unit_statine")
    assert {c["date"]: c["state"] for c in stat["days"]}["2026-10-09"] == "blocked"
    assert "cus_" not in json.dumps(grid) and "bk_" not in json.dumps(grid)


def test_what_if_explains_each_sauna_in_words_matching_the_rules() -> None:
    seed = load_seed(SEED)
    people, tow, start, end = 6, 1500, "2026-10-13", "2026-10-14"
    out = what_if(seed, people=people, tow_kg=tow, power=False, start=start, end=end)
    mem = Memory(load_seed(SEED))
    free = {
        a.unit_id: free_days(a.free)
        for a in mem.availability(AvailabilityQuery(from_=start, to=end))
    }
    for u in seed.units:
        req = BookingRequest("", u.id, start, end, people, Site(False), tow)
        row = next(r for r in out["units"] if r["id"] == u.id)
        assert row["codes"] == violations(req, u, free[u.id], [])
        assert row["fits"] == (not row["codes"]) and len(row["why"]) == len(row["codes"])
    did = next(r for r in out["units"] if r["id"] == "unit_didzioji")
    assert did["why"] == ["It weighs 1600 kg; the car tows 1500 kg."]


def test_what_if_says_the_weekend_rule_and_that_a_fitting_sauna_fits() -> None:
    out = what_if(
        load_seed(SEED), people=4, tow_kg=2000, power=True, start="2026-10-10", end="2026-10-10"
    )
    assert all("two days minimum" in " ".join(r["why"]) for r in out["units"])
    out = what_if(
        load_seed(SEED), people=4, tow_kg=2000, power=True, start="2026-10-13", end="2026-10-14"
    )
    assert {r["id"] for r in out["units"] if r["fits"]} >= {"unit_mazoji", "unit_statine"}


def row(fixture: str, run: int, passed: bool = True, judge_ok: bool = True) -> RunRow:
    det = passed or not judge_ok
    return RunRow("fixtures", fixture, run, passed, det, judge_ok, 1, 2000, 150, 1500)


def test_the_thermal_view_is_fixtures_by_runs_grouped_by_version() -> None:
    old = GateRun.from_rows(
        [row("happy-path", 1, passed=False), row("silence", 1)],
        agent_version="v0",
        run_ts=NOW - timedelta(days=1),
    )
    new = GateRun.from_rows(
        [row(f, r, passed=(f, r) != ("silence", 2), judge_ok=(f, r) != ("silence", 2))
         for f in ("happy-path", "silence") for r in (1, 2, 3)],
        agent_version="v1",
        run_ts=NOW,
    )  # fmt: skip
    m = thermal([new, old])
    assert [c["agent_version"] for c in m["columns"]] == ["v0", "v1", "v1", "v1"]  # oldest first
    assert [r["fixture"] for r in m["rows"]] == ["happy-path", "silence"]
    silence = m["rows"][1]["cells"]
    assert silence == ["pass", "pass", "judge", "pass"]  # a judge-only failure is its own state
    assert m["rows"][0]["cells"][0] == "fail"


def test_the_kits_own_cases_come_first() -> None:
    rows = [RunRow(suite, f, 1, True, True, True, 1, 1, 1, 1) for suite, f in
            [("extra", "a-ours"), ("fixtures", "z-given")]]  # fmt: skip
    m = thermal([GateRun.from_rows(rows, agent_version="v1", run_ts=NOW)])
    assert [r["suite"] for r in m["rows"]] == ["fixtures", "extra"]


def test_thermal_rows_come_grouped_by_where_the_case_comes_from() -> None:
    rows = [RunRow(suite, f, 1, True, True, True, 1, 1, 1, 1) for suite, f in
            [("extra", "zz-red"), ("extra", "aa-edge"), ("fixtures", "mm-given"),
             ("variants", "aa-variant")]]  # fmt: skip
    groups = {("extra", "zz-red"): "red_team", ("extra", "aa-edge"): "edge",
              ("fixtures", "mm-given"): "given",
              ("variants", "aa-variant"): "variants"}  # fmt: skip
    m = thermal([GateRun.from_rows(rows, agent_version="v1", run_ts=NOW)], groups)
    assert [(r["group"], r["fixture"]) for r in m["rows"]] == [
        ("given", "mm-given"), ("edge", "aa-edge"), ("red_team", "zz-red"),
        ("variants", "aa-variant"),
    ]  # fmt: skip
    assert m["families"]["given"] == "Given by the kit"
    assert {g["key"] for g in m["groups"]} >= {"given", "edge", "red_team", "variants"}


def test_a_gate_run_is_split_into_the_kits_cases_and_ours() -> None:
    rows = [
        *[RunRow("fixtures", "given-ok", r, True, True, True, 1, 1, 1, 1) for r in (1, 2, 3)],
        *[RunRow("extra", "ours-ok", r, True, True, True, 1, 1, 1, 1) for r in (1, 2, 3)],
        RunRow("extra", "ours-judge", 1, True, True, True, 1, 1, 1, 1),
        RunRow("extra", "ours-judge", 2, False, True, False, 1, 1, 1, 1),  # the judge only
    ]
    groups = {("fixtures", "given-ok"): "given", ("extra", "ours-ok"): "edge",
              ("extra", "ours-judge"): "red_team"}  # fmt: skip
    [view] = gate_runs_view([GateRun.from_rows(rows, agent_version="v1", run_ts=NOW)], groups)
    assert view["by_family"] == [
        {"family": "given", "label": "Given by the kit", "cases": 1, "passed_cases": 1,
         "deterministic_passed_cases": 1, "judge_passed_cases": 1, "available": 1},
        {"family": "ours", "label": "Written by us", "cases": 2, "passed_cases": 1,
         "deterministic_passed_cases": 2, "judge_passed_cases": 1, "available": 2},
    ]  # fmt: skip
    assert "rows" not in view
