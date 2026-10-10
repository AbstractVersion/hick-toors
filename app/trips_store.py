"""Saved trips in SQLite: one row per trip holding the SavedTrip JSON document."""

import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.models import (MAX_TRIP_BYTES, MAX_TRIP_ITEMS, MAX_TRIP_SIGHTS, SavedTrip, SavedTripIn, SavedTripSummary, TripItemAdd,
                        trip_size)

MAX_ITEMS = MAX_TRIP_ITEMS
MAX_SIGHTS = MAX_TRIP_SIGHTS
MAX_TRIPS = 500
_TRIP_FIELDS = set(SavedTripIn.model_fields)


class TripFull(Exception):
    """The trip already holds the maximum of hikes, stays or sights (`kind` says which)."""

    def __init__(self, kind: str = "hikes"):
        super().__init__(kind)
        self.kind = kind


class TripTooLarge(Exception):
    """Adding the item would grow the trip document past MAX_TRIP_BYTES."""


class TooManyTrips(Exception):
    """The store already holds max_trips trips."""


class TripConflict(Exception):
    """The trip was saved elsewhere since the editor loaded it (updated_at differs)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    # Fixed-width UTC text so ORDER BY updated_at sorts chronologically.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TripStore:
    def __init__(self, path: Path, max_trips: int = MAX_TRIPS):
        self.path = Path(path)
        self.max_trips = max_trips
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS trips ("
                       "id TEXT PRIMARY KEY, doc TEXT NOT NULL, updated_at TEXT NOT NULL)")
            if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _parse(trip_id: str, doc: str) -> SavedTrip | None:
        # A row that no longer validates (corrupt, or from an incompatible version) is skipped, not fatal:
        # one bad trip must not break the list for everyone. delete() still removes it.
        try:
            return SavedTrip.model_validate_json(doc)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            print(f"trips: skipping unreadable trip {trip_id} ({type(exc).__name__})", file=sys.stderr)
            return None

    @classmethod
    def _load(cls, db, trip_id: str) -> SavedTrip | None:
        row = db.execute("SELECT doc FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return cls._parse(trip_id, row[0]) if row else None

    @staticmethod
    def _save(db, trip: SavedTrip) -> None:
        db.execute("INSERT OR REPLACE INTO trips (id, doc, updated_at) VALUES (?, ?, ?)",
                   (trip.id, trip.model_dump_json(), _stamp(trip.updated_at)))

    def list_trips(self) -> list[SavedTripSummary]:
        with self._db() as db:
            rows = db.execute("SELECT id, doc FROM trips ORDER BY updated_at DESC, rowid DESC").fetchall()
        trips = [t for t in (self._parse(trip_id, doc) for trip_id, doc in rows) if t is not None]
        return [SavedTripSummary(id=t.id, name=t.name, start=t.start, end=t.end, source=t.source,
                                 hike_count=len(t.hikes), stay_count=len(t.stays), updated_at=t.updated_at)
                for t in trips]

    def get(self, trip_id: str) -> SavedTrip | None:
        with self._db() as db:
            return self._load(db, trip_id)

    def create(self, data: SavedTripIn) -> SavedTrip:
        now = _now()
        trip = SavedTrip.model_validate({**data.model_dump(include=_TRIP_FIELDS), "id": uuid.uuid4().hex,
                                         "created_at": now, "updated_at": now})
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # lock before counting: the cap holds under concurrency
            if db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] >= self.max_trips:
                raise TooManyTrips()
            self._save(db, trip)
        return trip

    def replace(self, trip_id: str, data: SavedTripIn,
                expected_updated_at: datetime | None = None) -> SavedTrip | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # lock before reading: no lost updates
            old = self._load(db, trip_id)
            if old is None:
                return None
            if expected_updated_at is not None and expected_updated_at != old.updated_at:
                raise TripConflict()  # aware datetimes compare by instant, whatever the offset
            trip = SavedTrip.model_validate({**data.model_dump(include=_TRIP_FIELDS), "id": old.id,
                                             "created_at": old.created_at, "updated_at": _now()})
            self._save(db, trip)
        return trip

    def delete(self, trip_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM trips WHERE id = ?", (trip_id,)).rowcount > 0

    def add_item(self, trip_id: str, item: TripItemAdd) -> SavedTrip | None:
        if item.hike is not None:
            kind, value = "hikes", item.hike
        elif item.stay is not None:
            kind, value = "stays", item.stay
        else:
            kind, value = "sights", item.sight.model_copy(update={"lines": []})
        limit = MAX_SIGHTS if kind == "sights" else MAX_ITEMS
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # lock before reading: no lost updates, limit holds
            trip = self._load(db, trip_id)
            if trip is None:
                return None
            items = getattr(trip, kind)
            if any(existing.id == value.id for existing in items):
                return trip
            if len(items) >= limit:
                raise TripFull(kind)
            trip = trip.model_copy(update={kind: [*items, value], "updated_at": _now()})
            if trip_size(trip) > MAX_TRIP_BYTES:
                raise TripTooLarge()
            self._save(db, trip)
        return trip
