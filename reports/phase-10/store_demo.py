"""Phase 10 demo: real records through a FileStore in a scratch folder. No model calls.

    cd python && uv run python ../reports/phase-10/store_demo.py <scratch folder>

Needs python/runs/gate from the Phase 9 full gate (traces are gitignored, so local only).
"""

import json
import logging
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pirtis.agent.agent import decide
from pirtis.agent.version import AGENT_VERSION
from pirtis.api.memory import Memory, load_seed
from pirtis.harness.fixture import load_fixtures
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake
from pirtis.ops.gate_report import load
from pirtis.ops.model import ENTITIES, GateRun, Turn
from pirtis.ops.store import FileStore

root = Path(sys.argv[1])
shutil.rmtree(root, ignore_errors=True)
store = FileStore(root)

# 1. the Phase 9 gate traces as one GateRun record
g = GateRun.from_rows(
    load(Path("runs/gate")),
    agent_version=AGENT_VERSION,
    run_ts=datetime(2026, 10, 31, 3, 0, tzinfo=UTC),
)
gp = store.put(g)
assert store.get(GateRun, g.partition(), g.key()) == g
print("1. The Phase 9 gate traces (runs/gate) as one GateRun record")
print(
    f"   passed={g.passed}  cases={g.cases}  runs={g.runs}  k={g.k}  "
    f"deterministic={g.deterministic_passed_cases}/{g.cases}  "
    f"judge={g.judge_passed_cases}/{g.cases}"
)
print(
    f"   model_calls={g.model_calls}  zero_call_runs={g.zero_call_runs}  "
    f"p50={g.p50_ms}ms  p95={g.p95_ms}ms  est_cost=${g.est_cost_usd}"
)
print(f"   per-run rows kept: {len(g.rows)}  file: {gp.relative_to(root)}")
print(f"   ({gp.stat().st_size} bytes)  read back: identical")

# 2. a fresh telemetry line from decide(), the gold state scripted into Fake
lines: list[str] = []


class Keep(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        lines.append(record.getMessage())


logging.getLogger("pirtis.turn").addHandler(Keep())
gold = json.loads(Path("pirtis/agent/gold/happy-path.json").read_text())["state"]
f = next(x for x in load_fixtures(Path("../fixtures")) if x.id == "happy-path")
llm = Fake(script=[Response(content=json.dumps(gold))])
decide(f.transcript, Memory(load_seed(f.seed_path())), llm)
t = Turn.from_record(
    json.loads(lines[0]), source="gate", session_id="gate-20261031T030000Z-happy-path-1"
)
tp = store.put(t)
print("\n2. A fresh telemetry line from decide() on happy-path (gold state, zero tokens)")
print(
    f"   gate={t.gate!r} intent={t.intent!r} decision={t.decision!r} "
    f"llm_calls={t.llm_calls} latency_ms={t.latency_ms}"
)
print(f"   file: {tp.relative_to(root)}")

# 3. retention: an over-age copy of each, then prune
store.put(t.model_copy(update={"ts": t.ts - timedelta(days=91)}))
store.put(g.model_copy(update={"run_ts": g.run_ts - timedelta(days=366)}))
before = {c.KIND: len(store.query(c)) for c in ENTITIES}
removed = store.prune(now=max(t.when(), g.when()))
after = {c.KIND: len(store.query(c)) for c in ENTITIES}
print("\n3. Retention: an over-age copy of the turn (91 d) and the gate run (366 d), then prune")
print(f"   before  {before}\n   removed {removed}\n   after   {after}")

# 4. what is on disk
print("\n4. The folder")
for p in sorted(root.rglob("*")):
    rel = p.relative_to(root)
    print(f"   {'  ' * (len(rel.parts) - 1)}{rel.name}{'/' if p.is_dir() else ''}")
print("\nRetention by kind: " + ", ".join(f"{c.KIND} {c.RETENTION_DAYS} d" for c in ENTITIES))
