"""Abuse limits for 🌿 Find nearby sights (one process; the searches can cost many Overpass calls)."""

import time
from contextlib import contextmanager

NEARBY_COOLDOWN_S = 30.0  # per trip, from the start of its last search
NEARBY_MAX_RUNNING = 2  # searches at the same time, all trips


class NearbyBusy(Exception):
    """A search may not start now; the message is what the user sees (HTTP 429)."""


class NearbyLimits:
    def __init__(self, cooldown_s: float = NEARBY_COOLDOWN_S, max_running: int = NEARBY_MAX_RUNNING,
                 clock=time.monotonic):
        self.cooldown_s, self.max_running, self.clock = cooldown_s, max_running, clock
        self.running: set[str] = set()
        self.started: dict[str, float] = {}

    @contextmanager
    def slot(self, trip_id: str):
        """Hold a search slot for trip_id, or raise NearbyBusy. Checked and taken without awaiting."""
        now = self.clock()
        if trip_id in self.running:
            raise NearbyBusy("A search for this trip is already running")
        last = self.started.get(trip_id)
        if last is not None and now - last < self.cooldown_s:
            raise NearbyBusy("Wait a moment before searching this trip again")
        if len(self.running) >= self.max_running:
            raise NearbyBusy("Too many searches right now – try again in a minute")
        # Forget old start times so the dict can't grow without bound.
        self.started = {k: t for k, t in self.started.items() if now - t < self.cooldown_s}
        self.started[trip_id] = now
        self.running.add(trip_id)
        try:
            yield
        finally:
            self.running.discard(trip_id)
