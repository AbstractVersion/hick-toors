import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

HOUR = 3600
DAY = 86400
MAX_AGE_S = 366 * DAY  # safety net: the longest TTL (hike routes) is 365 days, so anything older is left over
TMP_GRACE_S = HOUR


class DiskCache:
    """Tiny JSON-file cache with per-entry TTL, shared by the web app and the MCP server."""

    def __init__(self, directory: Path, clock: Callable[[], float] = time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._clock = clock

    def _path(self, key: str) -> Path:
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("expires", 0) < self._clock():
            path.unlink(missing_ok=True)
            return None
        return entry.get("value")

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        path = self._path(key)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"expires": self._clock() + ttl_seconds, "value": value}))
        tmp.replace(path)

    def sweep(self) -> int:
        """Delete expired, unreadable and over-age entries plus stray temp files; returns how many went.

        Entries are only removed on read otherwise, so tiles nobody looks at again would pile up forever.
        A temp file younger than TMP_GRACE_S may be another process's write in progress and is kept.
        """
        now, removed = self._clock(), 0
        for path in self.directory.iterdir():
            try:
                if path.suffix == ".tmp":
                    stale = now - path.stat().st_mtime > TMP_GRACE_S
                elif path.suffix == ".json":
                    stale = now - path.stat().st_mtime > MAX_AGE_S
                    if not stale:
                        try:
                            entry = json.loads(path.read_text())
                        except ValueError:
                            entry = None
                        stale = not isinstance(entry, dict) or entry.get("expires", 0) < now
                else:
                    continue
                if stale:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:  # vanished or unreadable meanwhile: the next sweep tries again
                continue
        return removed
