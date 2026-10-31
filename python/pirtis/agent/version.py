"""The agent's version: a short hash of its own source files, the prompt included.

Any change to how the agent behaves changes it, so a turn record or a gate run always
says exactly which code produced it, and a changed version means the gate must run
again before it ships.
"""

import hashlib
from pathlib import Path


def _hash_sources() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        if p.name.startswith("test_"):
            continue
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


AGENT_VERSION = _hash_sources()
