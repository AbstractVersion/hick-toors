import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

HOUR = 3600
DAY = 86400


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
