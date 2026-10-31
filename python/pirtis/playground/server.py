"""The playground: one stdlib HTTP server for the caller simulator (/) and the admin
console (/admin), over a small JSON API.

    uv run playground [--host 127.0.0.1] [--port 8000]

Locally it listens on 127.0.0.1; in the container on 0.0.0.0, behind the ingress
allow-list (D-28). Requests that can spend model tokens share a per-minute cap, and
every request body is a pydantic model that refuses unknown fields (D-49).

The admin console's read endpoints return turn records, gate runs and the seed's
calendar, none of which holds a name or a phone. Only two return what was said, the
trace of a stored call and the feedback queue, and both read a stored call (30 days).
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pirtis.agent.actions import handlers
from pirtis.agent.agent import decide
from pirtis.agent.version import AGENT_VERSION
from pirtis.cmd.harness import load_dotenv
from pirtis.harness.fixture import Fixture, load_fixtures
from pirtis.harness.run import Runner
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import open_ai_from_env
from pirtis.ops.human import load_cards, outcome_of
from pirtis.ops.model import Calibration, Feedback, GateRun, HumanRating, Turn
from pirtis.ops.store import FileStore, store_from_env
from pirtis.ops.turns import seen_at
from pirtis.playground import analytics
from pirtis.playground.calls import UNKNOWN_PHONE, CallClosed, Calls, LiveCall, plain
from pirtis.playground.promote import Expected, PromoteError, build, save
from pirtis.transcript.types import Item

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(os.environ.get("PIRTIS_FIXTURES") or ROOT / "fixtures")
TODAY = os.environ.get("PIRTIS_TODAY", "2026-09-28")  # the seed's calendar is set around it
STATIC = Path(__file__).parent / "static"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}
MAX_BODY = 16_000

log = logging.getLogger("pirtis.playground")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartBody(_Body):
    phone: str = Field(pattern=r"^\+?\d{6,15}$")


class TurnBody(_Body):
    session_id: str = Field(max_length=32)
    text: str = Field(min_length=1, max_length=500)
    anger: float = Field(default=0.0, ge=0, le=1)
    barge_in: bool = False


class SilenceBody(_Body):
    session_id: str = Field(max_length=32)
    seconds: float = Field(default=8.0, ge=6, le=30)


class HangupBody(_Body):
    session_id: str = Field(max_length=32)


class FeedbackBody(_Body):
    session_id: str = Field(max_length=32)
    turn_id: str = Field(max_length=64)
    verdict: Literal["up", "down"]
    note: str = Field(default="", max_length=500)


class ExpectBookingBody(_Body):
    id: str = Field(default="", pattern=r"^(bk_\d{1,4})?$")
    unit: str = Field(default="", max_length=40)
    start: date | None = None
    end: date | None = None
    people: int | None = Field(default=None, ge=1, le=20)
    status: Literal["", "booked", "cancelled"] = ""


class PromoteBody(_Body):
    session_id: str = Field(max_length=32)
    turn_id: str = Field(max_length=64)
    title: str = Field(min_length=3, max_length=60)
    action: Literal["say", "wait", "transfer", "end_call"]
    bookings: list[ExpectBookingBody] = Field(default_factory=list, max_length=4)
    judge: list[Annotated[str, Field(min_length=5, max_length=300)]] = Field(
        min_length=1, max_length=8
    )
    why: str = Field(default="", max_length=300)
    confirm: Literal[True]  # the reviewer vouches that everyone in the call is made up


class RatingBody(_Body):
    session_id: str = Field(max_length=32)
    scenario_id: str = Field(max_length=40)
    rater: str = Field(pattern=r"^[A-Za-z]{2,4}$")  # initials, never a name
    goal_achieved: bool
    natural: int = Field(ge=1, le=5)
    numbers_clear: int = Field(ge=1, le=5)
    one_question: int = Field(ge=1, le=5)
    turn_taking: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=500)


class ReplayBody(_Body):
    fixture: str = Field(max_length=80)


class WhatIfBody(_Body):
    people: int = Field(ge=1, le=20)
    tow_kg: int = Field(ge=0, le=10_000)
    power: bool
    start: date
    end: date

    @model_validator(mode="after")
    def _short_enough(self) -> "WhatIfBody":
        if (self.end - self.start).days > 60:
            raise ValueError("a range of at most 60 days")
        return self


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class RateLimit:
    """At most n requests in any 60 seconds."""

    def __init__(self, per_min: int) -> None:
        self.per_min = per_min
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def take(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > 60:
                self._times.popleft()
            if len(self._times) >= self.per_min:
                return False
            self._times.append(now)
            return True


class App:
    def __init__(
        self,
        store: FileStore,
        llm: LlmClient,
        judge: LlmClient | None,
        rate_per_min: int,
        access_log: bool,
        promote_dir: Path | None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.judge = judge
        self.calls = Calls(store, llm, seed=FIXTURES / "seeds" / "base.json", today=TODAY)
        self.limit = RateLimit(rate_per_min)
        self.access_log = access_log
        self.promote_dir = promote_dir  # None: promoted cases are downloaded, not written
        self.fixtures: dict[str, tuple[str, Fixture]] = {}
        self._index_fixtures()
        self.cards = {c.id: c for c in load_cards()}

    def _index_fixtures(self) -> None:
        folders = [("given", FIXTURES), ("ours", FIXTURES / "extra")]
        if self.promote_dir is not None and self.promote_dir != FIXTURES / "extra":
            folders.append(("ours", self.promote_dir))
        found: dict[str, tuple[str, Fixture]] = {}
        for suite, folder in folders:
            for f in load_fixtures(folder):
                found.setdefault(f.id, (suite, f))
        self.fixtures = found

    # caller simulator

    def personas(self, q: dict[str, str]) -> Any:
        people = [
            {"label": c.name, "phone": c.phone, "kind": "customer"}
            for c in self.calls.seed.customers
        ]
        return {
            "personas": [
                *people,
                {"label": "An unknown number", "phone": UNKNOWN_PHONE, "kind": "unknown"},
            ]
        }

    def start(self, body: StartBody) -> Any:
        return HTTPStatus.CREATED, self.calls.view(self.calls.start(body.phone))

    def session(self, sid: str) -> Any:
        return self.calls.view(self._call(sid))

    def turn(self, body: TurnBody) -> Any:
        self._spend()
        call = self._call(body.session_id)
        self.calls.say(call, body.text, body.anger, body.barge_in)
        return self.calls.view(call)

    def silence(self, body: SilenceBody) -> Any:
        self._spend()
        call = self._call(body.session_id)
        self.calls.silence(call, body.seconds)
        return self.calls.view(call)

    def hang_up(self, body: HangupBody) -> Any:
        call = self._call(body.session_id)
        self.calls.hang_up(call)
        return self.calls.view(call)

    def feedback_post(self, body: FeedbackBody) -> Any:
        call = self._call(body.session_id)
        try:
            self.calls.feedback(call, body.turn_id, body.verdict, body.note)
        except KeyError:
            raise HttpError(404, "no such turn in this call") from None
        return HTTPStatus.CREATED, {"ok": True}

    def promote(self, body: PromoteBody) -> Any:
        """A reviewed turn becomes a test case; only a person, through the form, does this."""
        call = self._call(body.session_id)
        step = next((s for s in call.steps if s["turn_id"] == body.turn_id), None)
        if step is None:
            raise HttpError(404, "no such turn in this call")
        fb = self.store.get(Feedback, step["agent_version"], body.turn_id)
        if fb is None:
            raise HttpError(
                409, "Rate or flag this turn first: a case comes from reviewed feedback."
            )
        want = Expected(
            title=body.title,
            action=body.action,
            bookings=[b.model_dump(mode="json") for b in body.bookings],
            judge=body.judge,
            why=body.why,
        )
        try:
            case = build(call, step, self.calls.seed, want)
            path = save(case, self.promote_dir) if self.promote_dir is not None else None
        except PromoteError as e:
            raise HttpError(400, str(e)) from None
        name = path.name if path else f"{case.id}.yaml"
        self.store.put(fb.model_copy(update={"promoted_fixture": name}))
        if path is not None:
            self._index_fixtures()
        return HTTPStatus.CREATED, {
            "id": case.id,
            "file": name,
            "written": path is not None,
            "yaml": case.text,
            "run": f"uv run harness --fixtures {_shown(path.parent) if path else '<folder>'} "
            f"--id {case.id} --runs 3 --need 3 -v",
        }

    # human evaluation

    def scenarios(self, q: dict[str, str]) -> Any:
        phones = {c.name: c.phone for c in self.calls.seed.customers}
        return {
            "cards": [
                {**c.model_dump(), "phone": phones.get(c.persona, UNKNOWN_PHONE)}
                for c in self.cards.values()
            ]
        }

    def rating(self, body: RatingBody) -> Any:
        call = self._call(body.session_id)
        if body.scenario_id not in self.cards:
            raise HttpError(404, "no such scenario card")
        self.store.put(
            HumanRating(
                scenario_id=body.scenario_id,
                session_id=body.session_id,
                rater=body.rater.upper(),  # one rater, one key, on a case-blind share too
                agent_version=call.steps[-1]["agent_version"] if call.steps else AGENT_VERSION,
                goal_achieved=body.goal_achieved,
                natural=body.natural,
                numbers_clear=body.numbers_clear,
                one_question=body.one_question,
                turn_taking=body.turn_taking,
                note=body.note,
                created=datetime.now(UTC),
            )
        )
        return HTTPStatus.CREATED, {"ok": True}

    def human_eval(self, q: dict[str, str]) -> Any:
        ratings = self.store.query(HumanRating)
        outcomes: dict[str, str | None] = {}
        for sid in {r.session_id for r in ratings}:
            call = self.calls.get(sid)  # gone after 30 days: no outcome to compare
            outcomes[sid] = (
                outcome_of(call.status, self.calls.card(call)["status"]) if call else None
            )
        calibrations = self.store.query(Calibration)
        return analytics.human_eval(
            ratings, list(self.cards.values()), outcomes, calibrations[-1] if calibrations else None
        )

    def fixture_list(self, q: dict[str, str]) -> Any:
        return {
            "fixtures": [
                {"id": fid, "suite": suite, "judged": len(f.judge)}
                for fid, (suite, f) in sorted(
                    self.fixtures.items(), key=lambda kv: (kv[1][0] != "given", kv[0])
                )
            ]
        }

    def replay(self, body: ReplayBody) -> Any:
        if body.fixture not in self.fixtures:
            raise HttpError(404, f"no fixture {body.fixture}")
        self._spend()
        _, f = self.fixtures[body.fixture]
        runner = Runner(llm=self.llm, judge=self.judge, decide=decide, handlers=handlers)
        r = runner.run(f).runs[0]
        return {
            "fixture": f.id,
            "passed": r.passed(),
            "error": r.error,
            "failures": r.failures,
            "verdicts": plain(r.verdicts),
            "actions": plain(r.actions),
            "effects": plain(r.effects),
            "api_calls": [
                {"method": c.method, "status": c.status, "code": c.code} for c in r.calls
            ],
            "llm": plain(r.stats),
            "wall_ms": r.wall_ms,
            "transcript": _messages(f.transcript.items),
        }

    # admin console: content-free reads

    def turns(self, q: dict[str, str]) -> Any:
        return {"turns": analytics.turns_view(self._turns(q))}

    def conversations(self, q: dict[str, str]) -> Any:
        days = _int(q, "days", 7, 1, 90)
        now = datetime.now(UTC)
        since = now - timedelta(days=2 * days)
        turns = [t for t in self.store.query(Turn, since=since) if _source_ok(t, q)]
        return analytics.conversations(turns, now=now, days=days)

    def gate_runs(self, q: dict[str, str]) -> Any:
        return {"runs": analytics.gate_runs_view(self.store.query(GateRun))}

    def thermal(self, q: dict[str, str]) -> Any:
        runs = self.store.query(GateRun)[-_int(q, "limit", 6, 1, 30) :]
        return analytics.thermal(runs)

    def fleet(self, q: dict[str, str]) -> Any:
        try:
            start = date.fromisoformat(q.get("from") or TODAY)
        except ValueError:
            raise HttpError(400, "from: a date as YYYY-MM-DD") from None
        return analytics.fleet(self.calls.seed, start, _int(q, "days", 42, 1, 90))

    def what_if(self, body: WhatIfBody) -> Any:
        return analytics.what_if(
            self.calls.seed,
            people=body.people,
            tow_kg=body.tow_kg,
            power=body.power,
            start=body.start.isoformat(),
            end=body.end.isoformat(),
        )

    # admin console: reads of stored calls (personal, 30 days)

    def trace(self, sid: str, turn_id: str) -> Any:
        call = self._call(sid)
        step = next((s for s in call.steps if s["turn_id"] == turn_id), None)
        if step is None:
            raise HttpError(404, "no such turn in this call")
        rec = next(
            (
                t
                for t in self.store.query(Turn, since=call.started)
                if t.turn_id == turn_id and t.session_id == sid
            ),
            None,
        )
        seen = seen_at(call.t.items, step["at"])
        return {
            "session_id": sid,
            "turn": rec.model_dump(mode="json", exclude={"phone_hash", "call_id"}) if rec else None,
            "step": step,
            "transcript": _messages(seen),
        }

    def feedback_list(self, q: dict[str, str]) -> Any:
        items = sorted(self.store.query(Feedback), key=lambda f: f.created, reverse=True)
        if q.get("verdict") in ("up", "down"):
            items = [f for f in items if f.verdict == q["verdict"]]
        out = []
        for f in items:
            call = self.calls.get(f.session_id)  # gone after 30 days, like the feedback
            step = (
                next((s for s in call.steps if s["turn_id"] == f.turn_id), None) if call else None
            )
            said = [e["text"] for e in step["effects"] if e.get("type") == "speech"] if step else []
            out.append(
                {
                    **f.model_dump(mode="json", exclude={"transcript"}),
                    "reply": " ".join(said),
                    "decision": step["decision"] if step else "",
                    "transcript": _messages([Item.from_dict(i) for i in f.transcript["items"]]),
                }
            )
        return {"feedback": out}

    def health(self, q: dict[str, str]) -> Any:
        return {"ok": True, "agent_version": AGENT_VERSION, "today": TODAY}

    # helpers

    def _call(self, sid: str) -> LiveCall:
        call = self.calls.get(sid)
        if call is None:
            raise HttpError(404, "no such call")
        return call

    def _spend(self) -> None:
        if not self.limit.take():
            raise HttpError(429, "Too many requests this minute. Wait a few seconds and try again.")

    def _turns(self, q: dict[str, str]) -> list[Turn]:
        since = datetime.now(UTC) - timedelta(days=_int(q, "days", 7, 1, 90))
        return [t for t in self.store.query(Turn, since=since) if _source_ok(t, q)]


def _shown(p: Path) -> str:
    """A path as the reviewer would type it from python/."""
    try:
        return "../" + str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _source_ok(t: Turn, q: dict[str, str]) -> bool:
    return q.get("source") in (None, "", t.source)


def _int(q: dict[str, str], key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(q.get(key, default))))
    except ValueError:
        raise HttpError(400, f"{key}: a whole number") from None


def _messages(items: list[Item]) -> list[dict[str, Any]]:
    """A transcript for display: what was said, and API calls by name."""
    out: list[dict[str, Any]] = []
    for i in items:
        if i.type == "message":
            out.append(
                {
                    "role": i.role,
                    "content": i.content,
                    "start": i.start,
                    "interrupted": i.interrupted,
                }
            )
        elif i.type == "function_call":
            out.append(
                {"role": "system", "content": i.name, "start": i.start, "interrupted": False}
            )
    return out


GET_ROUTES = {
    "personas": App.personas,
    "fixtures": App.fixture_list,
    "turns": App.turns,
    "conversations": App.conversations,
    "gate-runs": App.gate_runs,
    "thermal": App.thermal,
    "fleet": App.fleet,
    "feedback": App.feedback_list,
    "health": App.health,
    "scenarios": App.scenarios,
    "human-eval": App.human_eval,
}
POST_ROUTES: dict[str, tuple[type[_Body], Any]] = {
    "session": (StartBody, App.start),
    "turn": (TurnBody, App.turn),
    "silence": (SilenceBody, App.silence),
    "hangup": (HangupBody, App.hang_up),
    "feedback": (FeedbackBody, App.feedback_post),
    "replay": (ReplayBody, App.replay),
    "promote": (PromoteBody, App.promote),
    "rating": (RatingBody, App.rating),
    "what-if": (WhatIfBody, App.what_if),
}


def _handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pirtis-playground"
        sys_version = ""

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def _serve(self, method: str) -> None:
            url = urlsplit(self.path)
            try:
                if method == "GET" and not url.path.startswith("/api/"):
                    return self._static(url.path)
                status, body = self._route(
                    method, url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
                )
            except HttpError as e:
                status, body = e.status, {"error": str(e)}
            except ValidationError as e:
                status, body = 400, {"error": _explain(e)}
            except CallClosed as e:
                status, body = 409, {"error": str(e)}
            except Exception:
                log.exception("request failed: %s %s", method, url.path)
                status, body = 500, {"error": "Something went wrong on our side."}
            self._send(
                status,
                json.dumps(body, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def _route(self, method: str, path: str, q: dict[str, str]) -> tuple[int, Any]:
            parts = [unquote(p) for p in path.split("/")[2:]]
            if method == "GET":
                if parts[0] == "session" and len(parts) == 2:
                    return 200, app.session(parts[1])
                if parts[0] == "trace" and len(parts) == 3:
                    return 200, app.trace(parts[1], parts[2])
                if len(parts) == 1 and parts[0] in GET_ROUTES:
                    return 200, GET_ROUTES[parts[0]](app, q)
            elif len(parts) == 1 and parts[0] in POST_ROUTES:
                model, fn = POST_ROUTES[parts[0]]
                out = fn(app, model.model_validate(self._json()))
                return out if isinstance(out, tuple) else (200, out)
            raise HttpError(404, "no such endpoint")

        def _json(self) -> Any:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise HttpError(413, "the request body is too large")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise HttpError(400, "the body must be JSON") from None

        def _static(self, path: str) -> None:
            rel = {"/": "caller.html", "/admin": "admin.html"}.get(path)
            if rel is None and path.startswith("/static/"):
                rel = unquote(path[len("/static/") :])
            target = (STATIC / rel).resolve() if rel else None
            if (
                target is None
                or not target.is_relative_to(STATIC.resolve())
                or not target.is_file()
            ):
                return self._send(404, b'{"error": "not found"}', "application/json; charset=utf-8")
            self._send(
                200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream")
            )

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if "json" in ctype else "no-cache")
            for k, v in SECURITY.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            if app.access_log:  # one JSON line; the path without its query string
                line = {"event": "http", "method": self.command, "path": urlsplit(self.path).path}
                print(
                    json.dumps({**line, "status": str(int(code)) if code != "-" else code}),
                    file=sys.stderr,
                )

        def log_message(self, format: str, *args: Any) -> None:
            if app.access_log:
                print(json.dumps({"event": "http_error", "detail": format % args}), file=sys.stderr)

    return Handler


def _explain(e: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}" for err in e.errors()
    )


def make_server(
    store: FileStore,
    llm: LlmClient,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    rate_per_min: int = 30,
    judge: LlmClient | None = None,
    access_log: bool = False,
    promote_dir: Path | None = None,
) -> ThreadingHTTPServer:
    app = App(store, llm, judge, rate_per_min, access_log, promote_dir)
    srv = ThreadingHTTPServer((host, port), _handler(app))
    srv.daemon_threads = True
    return srv


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="playground", description="caller simulator and admin console")
    p.add_argument("--host", default=os.environ.get("PIRTIS_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = p.parse_args(argv)

    load_dotenv(".env")
    try:
        llm = open_ai_from_env()
    except RuntimeError as e:
        print(f"{e}\nset LLM_API_KEY, and optionally LLM_BASE_URL", file=sys.stderr)
        sys.exit(2)
    # one JSON line per turn on stdout, no content: what the cloud's log queries read
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("pirtis.turn").addHandler(out)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    rate = int(os.environ.get("PIRTIS_RATE_PER_MIN", "30"))
    # promoted cases go into the repo's own suite; in the container, set it empty: download
    promote = os.environ.get("PIRTIS_PROMOTE_DIR", str(FIXTURES / "extra"))
    srv = make_server(
        store_from_env(),
        llm,
        host=args.host,
        port=args.port,
        rate_per_min=rate,
        access_log=True,
        promote_dir=Path(promote) if promote else None,
    )
    print(f"playground on http://{args.host}:{args.port}  ·  admin at /admin", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
