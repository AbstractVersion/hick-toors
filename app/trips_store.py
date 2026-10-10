"""Saved trips in SQLite: one row per trip holding the SavedTrip JSON document."""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.models import MAX_TRIP_ITEMS, SavedTrip, SavedTripIn, SavedTripSummary, TripItemAdd

MAX_ITEMS = MAX_TRIP_ITEMS


class TripFull(Exception):
    """The trip already holds MAX_ITEMS hikes (or stays)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    # Fixed-width UTC text so ORDER BY updated_at sorts chronologically.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TripStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS trips ("
                       "id TEXT PRIMARY KEY, doc TEXT NOT NULL, updated_at TEXT NOT NULL)")
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
    def _load(db, trip_id: str) -> SavedTrip | None:
        row = db.execute("SELECT doc FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return SavedTrip.model_validate_json(row[0]) if row else None

    @staticmethod
    def _save(db, trip: SavedTrip) -> None:
        db.execute("INSERT OR REPLACE INTO trips (id, doc, updated_at) VALUES (?, ?, ?)",
                   (trip.id, trip.model_dump_json(), _stamp(trip.updated_at)))

    def list_trips(self) -> list[SavedTripSummary]:
        with self._db() as db:
            rows = db.execute("SELECT doc FROM trips ORDER BY updated_at DESC, rowid DESC").fetchall()
        trips = [SavedTrip.model_validate_json(row[0]) for row in rows]
        return [SavedTripSummary(id=t.id, name=t.name, start=t.start, end=t.end, source=t.source,
                                 hike_count=len(t.hikes), stay_count=len(t.stays), updated_at=t.updated_at)
                for t in trips]

    def get(self, trip_id: str) -> SavedTrip | None:
        with self._db() as db:
            return self._load(db, trip_id)

    def create(self, data: SavedTripIn) -> SavedTrip:
        now = _now()
        trip = SavedTrip.model_validate({**data.model_dump(), "id": uuid.uuid4().hex,
                                         "created_at": now, "updated_at": now})
        with self._db() as db:
            self._save(db, trip)
        return trip

    def replace(self, trip_id: str, data: SavedTripIn) -> SavedTrip | None:
        with self._db() as db:
            old = self._load(db, trip_id)
            if old is None:
                return None
            trip = SavedTrip.model_validate({**data.model_dump(), "id": old.id,
                                             "created_at": old.created_at, "updated_at": _now()})
            self._save(db, trip)
        return trip

    def delete(self, trip_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM trips WHERE id = ?", (trip_id,)).rowcount > 0

    def add_item(self, trip_id: str, item: TripItemAdd) -> SavedTrip | None:
        kind, value = ("hikes", item.hike) if item.hike is not None else ("stays", item.stay)
        with self._db() as db:
            trip = self._load(db, trip_id)
            if trip is None:
                return None
            items = getattr(trip, kind)
            if any(existing.id == value.id for existing in items):
                return trip
            if len(items) >= MAX_ITEMS:
                raise TripFull()
            trip = trip.model_copy(update={kind: [*items, value], "updated_at": _now()})
            self._save(db, trip)
        return trip
