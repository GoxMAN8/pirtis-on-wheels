"""The playground server over real HTTP: a scripted model, a store in a temp folder, and
a privacy scan of everything the admin console reads."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pirtis.llm.fake import Fake
from pirtis.ops.gate_report import RunRow
from pirtis.ops.model import GateRun
from pirtis.ops.store import FileStore
from pirtis.playground.server import make_server
from pirtis.playground.test_calls import JONAS, LINES, T1, T2, T3, T4, scripted

SEED_JSON = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures" / "seeds" / "base.json").read_text()
)
PRIVATE = [p for c in SEED_JSON["customers"] for p in [*c["name"].split(), c["phone"], c["id"]]]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read().decode()
                ctype = r.headers.get("Content-Type", "")
                return r.status, json.loads(raw) if "json" in ctype else raw
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")

    def get(self, path: str) -> tuple[int, Any]:
        return self.call("GET", path)

    def post(self, path: str, body: Any) -> tuple[int, Any]:
        return self.call("POST", path, body)


def serve(
    tmp_path: Path, llm: Fake, rate: int = 100, promote_dir: Path | None = None
) -> Iterator[Client]:
    srv = make_server(
        FileStore(tmp_path),
        llm,
        host="127.0.0.1",
        port=0,
        rate_per_min=rate,
        promote_dir=promote_dir,
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield Client(f"http://127.0.0.1:{srv.server_address[1]}")
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    yield from serve(tmp_path, scripted(T1, T2, T3, T4))


def book(c: Client) -> str:
    status, view = c.post("/api/session", {"phone": JONAS})
    assert status == 201, view
    sid = str(view["session_id"])
    for line in LINES:
        status, view = c.post("/api/turn", {"session_id": sid, "text": line})
        assert status == 200, view
    return sid


def test_pages_and_static_files_are_served(client: Client) -> None:
    for path in ("/", "/admin", "/static/app.css"):
        status, body = client.get(path)
        assert status == 200 and len(body) > 100, path


def test_no_path_escapes_the_static_folder(client: Client) -> None:
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/", "/nope"):
        assert client.get(path)[0] == 404, path


def test_a_call_through_the_api_ends_booked(client: Client) -> None:
    sid = book(client)
    status, view = client.get(f"/api/session/{sid}")
    assert status == 200 and view["card"]["status"] == "booked"
    assert view["last"]["decision"] == "Booked" and view["last"]["llm"]["calls"] == 1


def test_bad_requests_say_what_was_wrong(client: Client) -> None:
    status, err = client.post("/api/session", {"phone": JONAS, "extra": 1})
    assert status == 400 and "extra" in err["error"]
    _, view = client.post("/api/session", {"phone": JONAS})
    status, err = client.post(
        "/api/turn", {"session_id": view["session_id"], "text": "hi", "anger": 3}
    )
    assert status == 400 and "anger" in err["error"]
    assert client.get("/api/session/20261031-zzzz")[0] == 404
    assert client.get("/api/session/..%2f..%2fetc")[0] == 404


def test_the_admin_console_never_receives_a_name_or_a_phone(tmp_path: Path) -> None:
    for c in serve(tmp_path, scripted(T1, T2, T3, T4)):
        book(c)
        rows = [
            RunRow("fixtures", "happy-path", r, True, True, True, 1, 1, 1, 1) for r in (1, 2, 3)
        ]
        run_ts = datetime(2026, 10, 31, tzinfo=UTC)
        FileStore(tmp_path).put(GateRun.from_rows(rows, agent_version="v1", run_ts=run_ts))
        what_if: dict[str, object] = {"people": 4, "tow_kg": 1500, "power": False}
        what_if |= {"start": "2026-10-06", "end": "2026-10-07"}
        reads = [
            c.get("/api/conversations?days=7"),
            c.get("/api/turns?days=7"),
            c.get("/api/gate-runs"),
            c.get("/api/thermal"),
            c.get("/api/fleet"),
            c.post("/api/what-if", what_if),
        ]
        for status, body in reads:
            assert status == 200, body
            text = json.dumps(body, ensure_ascii=False)
            leaked = [p for p in PRIVATE if p in text]
            assert not leaked, leaked
        assert json.loads(json.dumps(reads[0][1]))["funnel"]["stages"][-1]["calls"] == 1


def test_a_trace_joins_the_turn_record_to_its_step(client: Client) -> None:
    sid = book(client)
    _, turns = client.get("/api/turns?days=7")
    t = next(x for x in turns["turns"] if x["decision"] == "Offer")
    status, trace = client.get(f"/api/trace/{sid}/{t['turn_id']}")
    assert status == 200
    assert trace["turn"]["turn_id"] == t["turn_id"]
    assert trace["step"]["state"]["tow_kg"] == 1500
    assert trace["transcript"][-1]["role"] == "user"  # the call as decide saw it


def test_feedback_is_kept_and_listed(client: Client) -> None:
    sid = book(client)
    _, view = client.get(f"/api/session/{sid}")
    tid = view["steps"][-1]["turn_id"]
    status, _ = client.post(
        "/api/feedback", {"session_id": sid, "turn_id": tid, "verdict": "up", "note": ""}
    )
    assert status == 201
    _, listed = client.get("/api/feedback")
    assert [f["turn_id"] for f in listed["feedback"]] == [tid]


def test_too_many_turns_a_minute_are_refused(tmp_path: Path) -> None:
    for c in serve(tmp_path, Fake(), rate=2):
        _, view = c.post("/api/session", {"phone": JONAS})
        sid = view["session_id"]
        codes = [c.post("/api/silence", {"session_id": sid})[0] for _ in range(3)]
        assert codes[:2] == [200, 200] and codes[2] == 429


def test_a_fixture_replays_through_the_real_harness(client: Client) -> None:
    status, out = client.post("/api/replay", {"fixture": "interim-speech"})
    assert status == 200 and out["passed"] is True and out["llm"]["calls"] == 0
    assert client.post("/api/replay", {"fixture": "no-such-fixture"})[0] == 404


def test_personas_are_the_seed_customers_and_an_unknown_number(client: Client) -> None:
    _, out = client.get("/api/personas")
    assert len(out["personas"]) == len(SEED_JSON["customers"]) + 1
    assert out["personas"][-1]["kind"] == "unknown"


def test_a_reviewed_turn_is_saved_as_a_test_case(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "seeds").symlink_to(Path(__file__).resolve().parents[3] / "fixtures" / "seeds")
    for c in serve(tmp_path / "data", scripted(T1, T2, T3, T4), promote_dir=cases):
        sid = book(c)
        _, view = c.get(f"/api/session/{sid}")
        offer = next(s for s in view["steps"] if s["decision"] == "Offer")["turn_id"]
        body = {
            "session_id": sid,
            "turn_id": offer,
            "title": "Offers Mažoji for four",
            "action": "say",
            "judge": ["The reply offers Mažoji for the sixth and seventh of October."],
            "confirm": True,
        }
        assert c.post("/api/promote", body)[0] == 409  # nobody has reviewed it yet
        c.post("/api/feedback", {"session_id": sid, "turn_id": offer, "verdict": "up", "note": ""})
        assert c.post("/api/promote", {**body, "confirm": False})[0] == 400
        status, out = c.post("/api/promote", body)
        assert status == 201 and out["file"] == "60_offers-mazoji-for-four.yaml"
        assert (cases / out["file"]).read_text() == out["yaml"]
        _, listed = c.get("/api/feedback")
        assert listed["feedback"][0]["promoted_fixture"] == out["file"]
        _, fixtures = c.get("/api/fixtures")
        assert "offers-mazoji-for-four" in {f["id"] for f in fixtures["fixtures"]}
        status, err = c.post("/api/promote", body)
        assert status == 400 and "already" in err["error"]


def test_without_a_folder_a_case_is_only_downloaded(tmp_path: Path) -> None:
    for c in serve(tmp_path, scripted(T1, T2, T3, T4)):
        sid = book(c)
        _, view = c.get(f"/api/session/{sid}")
        tid = view["steps"][-1]["turn_id"]
        c.post("/api/feedback", {"session_id": sid, "turn_id": tid, "verdict": "up", "note": ""})
        body = {"session_id": sid, "turn_id": tid, "title": "Books Mažoji", "action": "say",
                "judge": ["The reply confirms the booking."], "confirm": True}  # fmt: skip
        status, out = c.post("/api/promote", body)
        assert status == 201 and out["written"] is False and out["yaml"].startswith("# Promoted")
