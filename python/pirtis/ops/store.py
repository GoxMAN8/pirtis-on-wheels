"""One folder of JSON files: every record is its own file.

    <root>/<kind>/<partition>/<key>.json

Locally the root is python/var/; in Azure it is a file share mounted at /data
(PIRTIS_DATA_DIR). The playground and the gate job write to it at the same time, so
there is no shared file to fight over: each write goes to a temp file next to its
target and is renamed into place, which is atomic, so a reader sees the old record or
the new one, never half of one. Queries are directory scans, which is plenty at this
size. Names are percent-encoded, dots included: a key can't climb out of its folder,
and ':' (not allowed on Azure Files) never reaches the share.
"""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from pirtis.ops.model import ENTITIES, Entity

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "var"


def _name(part: str) -> str:
    name = quote(part, safe="").replace(".", "%2E")
    if not name:
        raise ValueError("an empty partition or key")
    return name


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, cls: type[Entity], partition: str, key: str) -> Path:
        return self.root / cls.KIND / _name(partition) / f"{_name(key)}.json"

    def put(self, e: Entity) -> Path:
        path = self._path(type(e), e.partition(), e.key())
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(e.model_dump_json())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def get[E: Entity](self, cls: type[E], partition: str, key: str) -> E | None:
        return self._read(cls, self._path(cls, partition, key))

    def query[E: Entity](
        self, cls: type[E], partition: str | None = None, since: datetime | None = None
    ) -> list[E]:
        """Records of one kind, oldest first; optionally one partition, or from a time."""
        found = [e for e, _ in self._scan(cls, partition)]
        if since is not None:
            found = [e for e in found if e.when() >= since]
        return sorted(found, key=lambda e: e.when())

    def prune(self, now: datetime | None = None) -> dict[str, int]:
        """Deletes every record older than its kind's retention; returns how many, by kind.
        Emptied folders stay: they cost nothing, and removing one could race a writer."""
        now = now or datetime.now(UTC)
        removed = {}
        for cls in ENTITIES:
            cutoff = now - timedelta(days=cls.RETENTION_DAYS)
            old = [p for e, p in self._scan(cls) if e.when() < cutoff]
            for p in old:
                p.unlink(missing_ok=True)
            removed[cls.KIND] = len(old)
        return removed

    def _scan[E: Entity](self, cls: type[E], partition: str | None = None) -> list[tuple[E, Path]]:
        base = self.root / cls.KIND
        dirs = [base / _name(partition)] if partition is not None else sorted(base.glob("*"))
        out = []
        for p in (p for d in dirs for p in sorted(d.glob("*.json"))):
            e = self._read(cls, p)
            if e is not None:
                out.append((e, p))
        return out

    @staticmethod
    def _read[E: Entity](cls: type[E], p: Path) -> E | None:
        try:
            return cls.model_validate_json(p.read_text(encoding="utf-8"))
        except FileNotFoundError:  # pruned between listing and reading
            return None


def store_from_env() -> FileStore:
    return FileStore(Path(os.environ.get("PIRTIS_DATA_DIR") or DEFAULT_ROOT))
