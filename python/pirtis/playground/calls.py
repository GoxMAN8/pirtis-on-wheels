"""Live calls: the caller types or speaks, decide answers, and the call is saved after
every turn.

The harness runs one turn of a fixture; a call is many. Each turn appends the caller's
line with timings (so silence and barge-ins mean what they mean in the fixtures), runs
decide and the handlers exactly as the harness does, then appends what reached the API
and what we said. The transcript is the call's whole record: after a restart a fresh
API replays the call's own writes from it, so booking ids and versions come out as they
were, without touching the API's internals.

Nothing here changes what the agent does. What the card shows is what the agent itself
accepted: the model's answer is captured on its way through and validated again by the
agent's own extract, with a scripted model, so it costs nothing.
"""

import json
import logging
import re
import secrets
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pirtis.agent.actions import HandlerContext, handlers
from pirtis.agent.agent import decide
from pirtis.agent.policy import identified_customer, last_offer
from pirtis.agent.telemetry import phone_hash
from pirtis.agent.understand import UnderstandError, extract
from pirtis.api.client import Call
from pirtis.api.memory import Memory, load_seed
from pirtis.api.types import Booking, BookingPatch, BookingRequest, Site
from pirtis.jsonutil import json_default
from pirtis.llm.client import Client as LlmClient
from pirtis.llm.client import Request, Response
from pirtis.llm.fake import Fake
from pirtis.ops.model import Feedback, Session, Turn
from pirtis.ops.store import FileStore
from pirtis.transcript.types import Caller, Emotion, Item, Prosody, Transcript, action_type

GREETING = "Pirtis on Wheels, hello?"  # how every fixture call opens
UNKNOWN_PHONE = "+37060000000"
WORDS_PER_S = 2.5  # speaking pace, for the timings silence and barge-ins are read from
BARGE_AT = 0.4  # a barge-in comes this far into our line
# what a call's record keeps of the API: who the caller is and their bookings. The
# catalogue and availability lookups repeat every turn and stay in the step (D-61).
KEPT = {"customers.identify", "bookings.list", "bookings.create", "bookings.update"}
SESSION_ID = re.compile(r"^\d{8}-[0-9a-f]{8}$")

type Status = Literal["open", "ended", "transferred"]
type Verdict = Literal["up", "down"]

_local = threading.local()


class _Collect(logging.Handler):
    """Keeps the agent's telemetry line for the turn running on this thread."""

    def emit(self, record: logging.LogRecord) -> None:
        lines = getattr(_local, "lines", None)
        if lines is not None:
            lines.append(record.getMessage())


logging.getLogger("pirtis.turn").addHandler(_Collect())


class _Heard:
    """Passes model calls through and keeps what the model answered."""

    def __init__(self, inner: LlmClient) -> None:
        self.inner = inner
        self.contents: list[str] = []

    def chat(self, req: Request) -> Response:
        resp = self.inner.chat(req)
        self.contents.append(resp.content)
        return resp


class CallClosed(Exception):
    """The call has ended or been passed on; it takes no more turns."""


@dataclass
class LiveCall:
    session_id: str
    started: datetime
    t: Transcript
    api: Memory
    status: Status = "open"
    steps: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class Calls:
    """The playground's calls: kept in memory while live, on disk after every turn."""

    def __init__(self, store: FileStore, llm: LlmClient, *, seed: Path, today: str) -> None:
        self.store = store
        self.llm = llm
        self.seed_path = seed
        self.today = today
        self.seed = load_seed(seed)
        self._live: dict[str, LiveCall] = {}
        self._lock = threading.Lock()

    def start(self, phone: str) -> LiveCall:
        started = datetime.now(UTC)
        sid = f"{started:%Y%m%d}-{secrets.token_hex(4)}"
        hello = Item(type="message", role="assistant", start=0.5, end=2.4, content=GREETING)
        t = Transcript(call_id=sid, today=self.today, now=2.4, caller=Caller(phone), items=[hello])
        call = LiveCall(sid, started, t, Memory(load_seed(self.seed_path)))
        with self._lock:
            self._live[sid] = call
        self._save(call)
        return call

    def get(self, session_id: str) -> LiveCall | None:
        """A live call, or one rebuilt from disk; None for an id that isn't a call."""
        if not SESSION_ID.match(session_id):
            return None
        with self._lock:
            if session_id in self._live:
                return self._live[session_id]
        day = f"{session_id[:4]}-{session_id[4:6]}-{session_id[6:8]}"
        saved = self.store.get(Session, day, session_id)
        if saved is None:
            return None
        call = self._rebuild(saved)
        with self._lock:
            return self._live.setdefault(session_id, call)

    def say(
        self, call: LiveCall, text: str, anger: float = 0.0, barge_in: bool = False
    ) -> dict[str, Any]:
        """The caller says text, then the receptionist answers."""
        with call.lock:
            _must_be_open(call)
            t = call.t
            start = t.now + 0.4
            ours = _last_line(t)
            if barge_in and ours is not None and not ours.interrupted:
                words = ours.content.split()
                if len(words) > 1:
                    keep = min(len(words) - 1, max(1, round(len(words) * BARGE_AT)))
                    ours.content = " ".join(words[:keep])
                ours.interrupted = True
                start = ours.start + (ours.end - ours.start) * BARGE_AT
            end = start + max(0.8, len(text.split()) / WORDS_PER_S)
            emotions = [Emotion("Anger", round(anger, 2))] if anger > 0 else []
            t.items.append(
                Item(
                    type="message",
                    role="user",
                    start=round(start, 2),
                    end=round(end, 2),
                    content=text,
                    is_final=True,
                    speech_final=True,
                    confidence=0.95,
                    prosody=Prosody(emotions),
                )
            )
            t.now = round(max(end, t.now) + 0.5, 2)
            return self._respond(call)

    def silence(self, call: LiveCall, seconds: float = 8.0) -> dict[str, Any]:
        """The caller says nothing for a while, then the receptionist decides what to do."""
        with call.lock:
            _must_be_open(call)
            call.t.now = round(call.t.now + seconds, 2)
            return self._respond(call)

    def hang_up(self, call: LiveCall) -> None:
        """The caller puts the phone down; the receptionist isn't asked anything."""
        with call.lock:
            _must_be_open(call)
            call.status = "ended"
            self._save(call)

    def feedback(self, call: LiveCall, turn_id: str, verdict: Verdict, note: str) -> Feedback:
        step = next((s for s in call.steps if s["turn_id"] == turn_id), None)
        if step is None:
            raise KeyError(turn_id)
        fb = Feedback(
            turn_id=turn_id,
            session_id=call.session_id,
            agent_version=step["agent_version"],
            verdict=verdict,
            note=note,
            transcript=plain(call.t),
            created=datetime.now(UTC),
        )
        self.store.put(fb)
        return fb

    def view(self, call: LiveCall) -> dict[str, Any]:
        """The call as the caller simulator shows it."""
        return {
            "session_id": call.session_id,
            "status": call.status,
            "phone": call.t.caller.phone,
            "items": [
                {
                    "role": i.role,
                    "content": i.content,
                    "start": i.start,
                    "end": i.end,
                    "interrupted": i.interrupted,
                }
                for i in call.t.items
                if i.type == "message"
            ],
            "card": self.card(call),
            "steps": [
                {"turn_id": s["turn_id"], "decision": s["decision"], "at": s["at"]}
                for s in call.steps
            ],
            "last": call.steps[-1] if call.steps else None,
        }

    def card(self, call: LiveCall) -> dict[str, Any]:
        """The booking card: what the agent has accepted so far, and what it asks next."""
        st: dict[str, Any] = next((s["state"] for s in reversed(call.steps) if s["state"]), {})
        units = {u.id: u for u in self.seed.units}
        identified = bool(identified_customer(call.t))
        decisions = [s["decision"] for s in call.steps if s["decision"]]
        wrote = [d for d in decisions if d in ("Booked", "Moved", "Cancelled")]

        offer = None
        heard = last_offer(call.t, list(units.values()))
        if heard is not None and call.status == "open":
            u = units[heard.unit_id]
            n = (date.fromisoformat(heard.end) - date.fromisoformat(heard.start)).days + 1
            offer = {
                "unit": u.name,
                "start": heard.start,
                "end": heard.end,
                "people": heard.people,
                "total": u.price_per_day * n,
            }
        changed = self._changed(call)
        booking = None
        if changed:
            b = changed[-1]
            booking = {
                "id": b.id,
                "unit": units[b.unit_id].name,
                "start": b.start,
                "end": b.end,
                "people": b.people,
                "total": b.total,
                "status": b.status,
            }
        sms = [e["text"] for s in call.steps for e in s["effects"] if e.get("type") == "sms"]

        if wrote:
            status = wrote[-1].lower()
        elif call.status != "open":
            status = call.status
        elif decisions and decisions[-1] in ("Offer", "ConfirmChange", "ConfirmCancel"):
            status = "offered"
        else:
            status = "gathering"
        nxt = ""
        if call.status == "open" and not wrote:
            for slot, known in (
                ("name", identified),
                ("dates", st.get("start")),
                ("people", st.get("people")),
                ("tow", st.get("tow_kg")),
            ):
                if not known:
                    nxt = slot
                    break
            else:  # all four known and it still asked: power, which only an electric unit needs
                if (
                    st.get("intent") == "book"
                    and st.get("power") is None
                    and decisions[-1:] == ["Ask"]
                ):
                    nxt = "power"
        unit = st.get("unit")
        return {
            "status": status,
            "identified": identified,
            "name": st.get("full_name"),
            "start": st.get("start"),
            "end": st.get("end"),
            "people": st.get("people"),
            "tow_kg": st.get("tow_kg"),
            "power": st.get("power"),
            "unit": units[unit].name if unit in units else None,
            "offer": offer,
            "booking": booking,
            "sms": sms[-1] if sms else "",
            "next": nxt,
            # a quote is evidence only when the agent kept the fact it backs: its check drops
            # the fact, not the quote, when the words weren't the caller's (D-54)
            "evidence": {
                "tow": st.get("tow_quote") if st.get("tow_kg") is not None else None,
                "unit": st.get("unit_quote") if unit in units else None,
                "consent": st.get("consent_quote")
                if st.get("accepted_offer") or st.get("go_ahead")
                else None,
            },
        }

    def _respond(self, call: LiveCall) -> dict[str, Any]:
        t = call.t
        seen = replace(t, items=list(t.items))  # the call as decide sees it
        before = len(call.api.log())
        heard = _Heard(self.llm)
        _local.lines = []
        try:
            actions = decide(seen, call.api, heard)
        finally:
            lines, _local.lines = _local.lines, None
        ctx = HandlerContext(api=call.api, transcript=seen)
        effects = []
        for a in actions:
            h = handlers.get(action_type(a))
            effects.append(h(a, ctx) if h else {"type": "unhandled", "action": action_type(a)})
        made = call.api.log()[before:]
        record = Turn.from_record(
            json.loads(lines[0]), source="playground", session_id=call.session_id
        )

        at = t.now
        for c in made:
            if c.method not in KEPT:
                continue
            cid = f"c{sum(i.type == 'function_call' for i in t.items) + 1}"
            args = json.dumps(_args(c), ensure_ascii=False)
            out = json.dumps(_output(c), ensure_ascii=False)
            t.items.append(
                Item(type="function_call", call_id=cid, name=c.method, arguments=args, start=at)
            )
            t.items.append(
                Item(type="function_call_output", call_id=cid, output=out, end=round(at + 0.2, 2))
            )
            at = round(at + 0.3, 2)
        for e in effects:
            match e.get("type"):
                case "speech":
                    start = at + 0.3
                    end = start + max(0.8, len(str(e["text"]).split()) / WORDS_PER_S)
                    t.items.append(
                        Item(
                            type="message",
                            role="assistant",
                            start=round(start, 2),
                            end=round(end, 2),
                            content=str(e["text"]),
                        )
                    )
                    at = round(end, 2)
                case "transfer":
                    call.status = "transferred"
                case "hangup":
                    call.status = "ended"
        t.now = at

        state = None
        if heard.contents:  # the agent's own validation, replayed on what the model said
            try:
                script = Fake(script=[Response(content=heard.contents[0])])
                state = extract(seen, script, self.seed.units).model_dump()
            except UnderstandError:
                state = None
        note = next(a for a in actions if action_type(a) == "note")
        step = {
            "turn_id": note["turn_id"],
            "agent_version": note["agent_version"],
            "at": seen.now,
            "decision": note["decision"],
            "intent": note["intent"],
            "gate": note["gate"],
            "fallback": note["fallback"],
            "state": state,
            "actions": plain(actions),
            "effects": plain(effects),
            "api_calls": [
                {
                    "method": c.method,
                    "status": c.status,
                    "code": c.code,
                    "args": _args(c),
                    "result": _output(c),
                }
                for c in made
            ],
            "llm": {
                "calls": record.llm_calls,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
            },
            "latency_ms": record.latency_ms,
        }
        call.steps.append(step)
        self._save(call, record)
        return step

    def _changed(self, call: LiveCall) -> list[Booking]:
        """The bookings this call made or changed."""
        seed = {b.id: b for b in self.seed.bookings}
        return [b for b in call.api.bookings() if seed.get(b.id) != b]

    def _save(self, call: LiveCall, record: Turn | None = None) -> None:
        self.store.put(
            Session(
                session_id=call.session_id,
                started=call.started,
                agent_version=call.steps[-1]["agent_version"] if call.steps else "",
                caller_phone_hash=phone_hash(call.t.caller.phone),
                turns=len(call.steps),
                status=call.status,
                transcript=plain(call.t),
                bookings=[plain(b) for b in self._changed(call)],
                steps=call.steps,
            )
        )
        if record is not None:
            self.store.put(record)

    def _rebuild(self, saved: Session) -> LiveCall:
        """A call from disk: the transcript as saved, and a fresh API brought to the same
        state by replaying the call's own writes in order."""
        t = Transcript.from_dict(saved.transcript)
        api = Memory(load_seed(self.seed_path))
        replay_writes(api, t.items, saved.session_id)
        return LiveCall(saved.session_id, saved.started, t, api, saved.status, list(saved.steps))


def replay_writes(api: Memory, items: list[Item], label: str) -> None:
    """Replays the successful bookings.create and bookings.update calls recorded in items,
    in order, and fails loudly if a replayed booking's id or version differs."""
    asked: dict[str, tuple[str, dict[str, Any]]] = {}
    for it in items:
        if it.type == "function_call":
            asked[it.call_id] = (it.name, json.loads(it.arguments))
            continue
        if it.type != "function_call_output" or it.call_id not in asked:
            continue
        name, args = asked[it.call_id]
        out = json.loads(it.output)
        if not isinstance(out, dict) or "error" in out:
            continue
        if name == "bookings.create":
            got = api.create_booking(BookingRequest(**{**args, "site": Site(**args["site"])}))
        elif name == "bookings.update":
            got = api.update_booking(args["id"], args["version"], BookingPatch(**args["patch"]))
        else:
            continue
        if (got.id, got.version) != (out.get("id"), out.get("version")):
            raise ValueError(f"{label}: replay diverged at {it.call_id}")


def _must_be_open(call: LiveCall) -> None:
    if call.status != "open":
        raise CallClosed(f"the call has {call.status}")


def _last_line(t: Transcript) -> Item | None:
    """Our last line, if we spoke last."""
    for it in reversed(t.items):
        if it.type == "message":
            return it if it.role == "assistant" else None
    return None


def plain(x: Any) -> Any:
    """Dataclasses and all, as plain JSON values."""
    if isinstance(x, Transcript):
        x = asdict(x)
    return json.loads(json.dumps(x, default=json_default, ensure_ascii=False))


def _args(c: Call) -> Any:
    """A call's arguments as the fixtures write them: "from" not "from_", no blanks."""
    a = plain(c.args)
    if isinstance(a, dict):
        return {("from" if k == "from_" else k): v for k, v in a.items() if v not in ("", None)}
    return a


def _output(c: Call) -> Any:
    if c.status != 200:
        return {"error": c.code, "detail": c.detail}
    return plain(c.result)
