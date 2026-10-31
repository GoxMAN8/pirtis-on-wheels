"""Our own fixtures are hand-written test data, so they get checked like code: they
load the way the harness loads them, and every API result a transcript claims is what
the API really returns for that seed."""

import json
from pathlib import Path

import pytest

from pirtis.api import client as api_client
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import BookingQuery
from pirtis.harness.fixture import Fixture, load_fixtures

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
EXTRA = FIXTURES / "extra"


def extra() -> list[Fixture]:
    return load_fixtures(EXTRA)


def test_extra_fixtures_load() -> None:
    fs = extra()
    assert len(fs) >= 10
    ids = [f.id for f in load_fixtures(FIXTURES)] + [f.id for f in fs]
    assert len(ids) == len(set(ids)), "fixture ids must be unique across suites"
    for f in fs:
        last = f.transcript.items[-1]
        assert last.type == "message" and last.end > 0, f.id
        assert f.transcript.now >= last.end, f.id


@pytest.mark.parametrize("f", extra(), ids=lambda f: f.id)
def test_messages_are_in_time_order(f: Fixture) -> None:
    starts = [it.start for it in f.transcript.items if it.type == "message"]
    assert starts == sorted(starts)


@pytest.mark.parametrize("f", extra(), ids=lambda f: f.id)
def test_transcript_matches_the_api(f: Fixture) -> None:
    seed = load_seed(f.seed_path())
    seed.bookings.extend(sb.booking() for sb in f.setup.bookings)
    api = Memory(seed)
    calls = {it.call_id: it for it in f.transcript.items if it.type == "function_call"}
    for it in f.transcript.items:
        if it.type != "function_call_output":
            continue
        call = calls[it.call_id]
        args = json.loads(call.arguments)
        claimed = json.loads(it.output)
        match call.name:
            case api_client.METHOD_CUSTOMERS_IDENTIFY:
                got = api.identify_customer(args["phone"], args["name"])
                assert claimed.get("ok") == got.ok
                assert claimed.get("customer_id", "") == got.customer_id
            case api_client.METHOD_BOOKINGS_LIST:
                rows = api.list_bookings(BookingQuery(customer_id=args["customer_id"]))
                assert [(b["id"], b["version"], b["status"]) for b in claimed] == [
                    (b.id, b.version, b.status) for b in rows
                ]
            case _:
                pytest.fail(f"{f.id}: no check for API call {call.name}")
