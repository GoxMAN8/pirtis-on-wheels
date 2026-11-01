"""Case 30 with the model's answer scripted (zero tokens): what the receptionist says when
the model names the sauna of a price question, and when it leaves the sauna out.

    cd python && uv run python ../reports/after-16/replay_30.py
"""

import json
from pathlib import Path

from pirtis.agent.agent import decide
from pirtis.api.memory import Memory, load_seed
from pirtis.harness.fixture import load_fixtures
from pirtis.llm.client import Response
from pirtis.llm.fake import Fake

case = next(
    f for f in load_fixtures(Path("../fixtures/extra")) if f.id == "price-before-identification"
)
gold = json.loads(Path("pirtis/agent/gold/price-before-identification.json").read_text())["state"]
for label, state in [
    ("model names the sauna (old prompt)", gold),
    ("model leaves the sauna and its quote out", {**gold, "unit": None, "unit_quote": None}),
]:
    llm = Fake(script=[Response(content=json.dumps(state))])
    actions = decide(case.transcript, Memory(load_seed(case.seed_path())), llm)
    said = " ".join(str(a.get("text", "")) for a in actions if a.get("type") == "say")
    print(f"{label}:\n   {said}")
