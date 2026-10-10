# hick-toors — Saved Trips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save trips (named buckets of hikes + stays + dates/guests/dog/notes) from Trip picks or any search result into a backend SQLite store, edit them in a "My trips" dialog, and export them as Markdown.

**Architecture:** Pydantic models in `app/models.py`; `app/trips_store.py` persists each trip as one JSON document row in SQLite (stdlib `sqlite3`, connection per call); `app/trip_markdown.py` renders a trip to Markdown (pure); `app/main.py` exposes `/api/saved-trips…` (sync handlers); `app/mcp_tools.py` gets two read-only tools; `static/trips.js` adds the ＋ Trip menu, 💾 Save on picks and the 🧳 My trips dialog.

**Tech Stack:** Python 3.10+, FastAPI, pydantic v2, stdlib sqlite3, FastMCP, pytest; vanilla JS + Leaflet; Playwright (Chrome) UI check.

**Spec:** `docs/superpowers/specs/2026-10-10-saved-trips-design.md`

## Global Constraints

- Project folder: `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors` (git worktree, branch `feat/hick-toors`). Paths are relative to it; run every command from it. Never `git add` files outside it. Never read, print or commit `.env`.
- Tests: `env -u VIRTUAL_ENV uv run pytest -q`. All 298 existing tests keep passing. Tests never touch the network and never write to the real `data/trips.db` (always a `tmp_path` DB).
- Python 3.10-compatible (no `typing.Self`, no `type X =`, no `ExceptionGroup`).
- No new dependencies.
- Limits verbatim: name 1..120 chars (stripped), notes ≤ 5000, guests 1..16, ≤ 50 hikes and ≤ 50 stays, score 0..100.
- Error details verbatim: `"Trip not found"` (404), `"A trip holds at most 50 hikes and 50 stays"` (409), `"end must be on or after start"`, `"Give exactly one of hike or stay"`.
- Setting `trips_db` (env `TRIPS_DB`), default `BASE_DIR / "data" / "trips.db"`; `data/` is in `.gitignore` and `.dockerignore`.
- UI security: scraped/user strings only via `textContent` or `esc()`; links only for `^https?://` URLs; no `window.confirm/alert/prompt`.

## Review Focus

1. Greek / non-ASCII trip names → export filename falls back to ASCII slug (`trip.md` when nothing ASCII is left) and `filename*` carries the UTF-8 name; Markdown body keeps the Greek text. (Task 2 + Task 3 tests.)
2. Adding the same hike twice (＋ Trip twice, or a pick saved then the hike added) → no duplicate, UI says `Already in “X”`. (Task 1 store test, Task 3 API test, Task 4 UI check.)
3. Trip saved without dates (dates cleared in the editor) → saves, Markdown says `📅 Dates not set`, no per-night price. (Task 1 model test, Task 2 test.)
4. Item URL that is not http(s) (e.g. `javascript:`) → Markdown renders plain name, UI renders a `<span>`, never a link. (Task 2 test; Task 4 code.)
5. Deleting while the editor holds unsaved edits / server restarted → DB persists across `TripStore` instances; delete needs a second click. (Task 1 persistence test, Task 4 UI check.)

---

### Task 1: Models, SQLite store, setting, deployment wiring

**Files:**
- Modify: `app/models.py` (append new models; `datetime` import)
- Create: `app/trips_store.py`
- Modify: `app/config.py` (add `trips_db`)
- Modify: `.gitignore`, `.dockerignore`, `Dockerfile`, `docker-compose.yml`
- Test: `tests/test_trips_store.py`, `tests/test_models.py` (append)

**Interfaces:**
- Produces: `SavedTripIn`, `SavedTrip`, `SavedTripSummary`, `TripItemAdd` (in `app.models`); `TripStore(path)` with `list_trips() -> list[SavedTripSummary]`, `get(trip_id) -> SavedTrip | None`, `create(SavedTripIn) -> SavedTrip`, `replace(trip_id, SavedTripIn) -> SavedTrip | None`, `delete(trip_id) -> bool`, `add_item(trip_id, TripItemAdd) -> SavedTrip | None`; `TripFull` exception; `MAX_ITEMS = 50`; `Settings.trips_db: Path`.

- [ ] **Step 1: Write the failing model tests** (append to `tests/test_models.py`; add imports at top as needed)

```python
import pytest
from pydantic import ValidationError

from app.models import Hike, SavedTripIn, Stay, TripItemAdd


def _hike(i="wikiloc:1"):
    return Hike(id=i, name="h", url="https://es.wikiloc.com/x", lat=39.0, lon=21.0)


def _stay(i="airbnb:1"):
    return Stay(id=i, source="airbnb", name="s", url="https://www.airbnb.com/rooms/1", lat=39.0, lon=21.0)


def test_saved_trip_in_defaults_and_strip():
    t = SavedTripIn(name="  Zagori  ")
    assert (t.name, t.start, t.end, t.guests, t.dog, t.notes, t.source, t.score) == (
        "Zagori", None, None, 2, True, "", "manual", None)
    assert t.hikes == [] and t.stays == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_saved_trip_in_rejects_bad_names(name):
    with pytest.raises(ValidationError):
        SavedTripIn(name=name)


def test_saved_trip_in_rejects_end_before_start():
    with pytest.raises(ValidationError, match="end must be on or after start"):
        SavedTripIn(name="t", start="2026-10-25", end="2026-10-23")


def test_saved_trip_in_limits():
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", notes="x" * 5001)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", guests=0)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", score=101)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", hikes=[_hike(f"wikiloc:{i}") for i in range(51)])


def test_saved_trip_in_dedupes_items_keeping_first():
    first = _hike("wikiloc:1")
    dup = first.model_copy(update={"name": "dup"})
    t = SavedTripIn(name="t", hikes=[first, _hike("wikiloc:2"), dup], stays=[_stay(), _stay()])
    assert [h.id for h in t.hikes] == ["wikiloc:1", "wikiloc:2"]
    assert t.hikes[0].name == "h"
    assert len(t.stays) == 1


def test_trip_item_add_needs_exactly_one():
    assert TripItemAdd(hike=_hike()).stay is None
    with pytest.raises(ValidationError, match="Give exactly one of hike or stay"):
        TripItemAdd()
    with pytest.raises(ValidationError, match="Give exactly one of hike or stay"):
        TripItemAdd(hike=_hike(), stay=_stay())
```

- [ ] **Step 2: Write the failing store tests** (`tests/test_trips_store.py`)

```python
import pytest

from app.models import Hike, SavedTripIn, Stay, TripItemAdd
from app.trips_store import MAX_ITEMS, TripFull, TripStore


def hike(i):
    return Hike(id=f"wikiloc:{i}", name=f"Hike {i}", url="https://es.wikiloc.com/x", lat=39.0, lon=21.0)


def stay(i):
    return Stay(id=f"airbnb:{i}", source="airbnb", name=f"Stay {i}", url="https://www.airbnb.com/rooms/1",
                lat=39.0, lon=21.0)


@pytest.fixture
def store(tmp_path):
    return TripStore(tmp_path / "sub" / "trips.db")


def test_creates_parent_dir_and_starts_empty(tmp_path, store):
    assert (tmp_path / "sub" / "trips.db").exists()
    assert store.list_trips() == []


def test_create_get_roundtrip(store):
    trip = store.create(SavedTripIn(name="Zagori", start="2026-10-23", end="2026-10-25",
                                    hikes=[hike(1)], stays=[stay(1)], source="suggested", score=77.5))
    assert len(trip.id) == 32
    assert trip.created_at == trip.updated_at
    assert trip.created_at.tzinfo is not None
    got = store.get(trip.id)
    assert got == trip
    assert store.get("missing") is None


def test_list_newest_first_with_counts(store):
    a = store.create(SavedTripIn(name="A", hikes=[hike(1), hike(2)]))
    b = store.create(SavedTripIn(name="B", stays=[stay(1)]))
    summaries = store.list_trips()
    assert [s.name for s in summaries] == ["B", "A"]
    assert (summaries[1].hike_count, summaries[1].stay_count) == (2, 0)
    assert (summaries[0].hike_count, summaries[0].stay_count) == (0, 1)
    store.replace(a.id, SavedTripIn(name="A2"))
    assert [s.name for s in store.list_trips()] == ["A2", "B"]
    assert b.id in {s.id for s in store.list_trips()}


def test_replace_keeps_id_and_created_at(store):
    trip = store.create(SavedTripIn(name="Old", hikes=[hike(1), hike(2)]))
    new = store.replace(trip.id, SavedTripIn(name="New", notes="n", hikes=[hike(2), hike(1)]))
    assert (new.id, new.created_at, new.name, new.notes) == (trip.id, trip.created_at, "New", "n")
    assert new.updated_at > trip.updated_at
    assert [h.id for h in store.get(trip.id).hikes] == ["wikiloc:2", "wikiloc:1"]
    assert store.replace("missing", SavedTripIn(name="x")) is None


def test_delete(store):
    trip = store.create(SavedTripIn(name="T"))
    assert store.delete(trip.id) is True
    assert store.get(trip.id) is None
    assert store.delete(trip.id) is False


def test_add_item_appends_and_dedupes(store):
    trip = store.create(SavedTripIn(name="T", hikes=[hike(1)]))
    after = store.add_item(trip.id, TripItemAdd(hike=hike(2)))
    assert [h.id for h in after.hikes] == ["wikiloc:1", "wikiloc:2"]
    same = store.add_item(trip.id, TripItemAdd(hike=hike(1)))
    assert [h.id for h in same.hikes] == ["wikiloc:1", "wikiloc:2"]
    with_stay = store.add_item(trip.id, TripItemAdd(stay=stay(1)))
    assert [s.id for s in with_stay.stays] == ["airbnb:1"]
    assert store.add_item("missing", TripItemAdd(stay=stay(1))) is None


def test_add_item_full_raises_and_keeps_trip(store):
    trip = store.create(SavedTripIn(name="T", stays=[stay(i) for i in range(MAX_ITEMS)]))
    with pytest.raises(TripFull):
        store.add_item(trip.id, TripItemAdd(stay=stay(999)))
    assert len(store.get(trip.id).stays) == MAX_ITEMS
    store.add_item(trip.id, TripItemAdd(hike=hike(1)))  # hikes have their own limit


def test_persists_across_instances(tmp_path):
    path = tmp_path / "trips.db"
    trip = TripStore(path).create(SavedTripIn(name="Kept", hikes=[hike(1)]))
    assert TripStore(path).get(trip.id).name == "Kept"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_trips_store.py tests/test_models.py`
Expected: FAIL / ImportError (`SavedTripIn`, `app.trips_store` missing).

- [ ] **Step 4: Add the models** — in `app/models.py` change the first import line to `from datetime import date, datetime` and append:

```python
MAX_TRIP_ITEMS = 50


def _unique_by_id(items: list) -> list:
    seen: set[str] = set()
    out = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            out.append(item)
    return out


class SavedTripIn(BaseModel):
    """Body of POST/PUT /api/saved-trips: a named bucket of hikes and stays (snapshots as seen)."""

    name: str = Field(min_length=1, max_length=120)
    start: date | None = None
    end: date | None = None
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    notes: str = Field(default="", max_length=5000)
    source: Literal["manual", "suggested"] = "manual"
    score: float | None = Field(default=None, ge=0, le=100)
    hikes: list[Hike] = Field(default_factory=list, max_length=MAX_TRIP_ITEMS)
    stays: list[Stay] = Field(default_factory=list, max_length=MAX_TRIP_ITEMS)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    _dedupe_items = field_validator("hikes", "stays")(_unique_by_id)

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start and self.end and self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


class SavedTrip(SavedTripIn):
    id: str
    created_at: datetime
    updated_at: datetime


class SavedTripSummary(BaseModel):
    id: str
    name: str
    start: date | None = None
    end: date | None = None
    source: Literal["manual", "suggested"] = "manual"
    hike_count: int
    stay_count: int
    updated_at: datetime


class TripItemAdd(BaseModel):
    """Body of POST /api/saved-trips/{id}/items: exactly one hike or one stay."""

    hike: Hike | None = None
    stay: Stay | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.hike is None) == (self.stay is None):
            raise ValueError("Give exactly one of hike or stay")
        return self
```

(If `field_validator(...)(func)` with a plain module function errors for these fields, wrap it as a `@field_validator("hikes", "stays") @classmethod def _dedupe_items(cls, items): return _unique_by_id(items)` — same behaviour. The file already uses the `_dedupe = field_validator(...)(_unique)` idiom.)

- [ ] **Step 5: Add the store** (`app/trips_store.py`)

```python
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
```

- [ ] **Step 6: Setting + ignore files + Docker**
  - `app/config.py`: after `cache_dir`, add `trips_db: Path = BASE_DIR / "data" / "trips.db"  # TRIPS_DB: saved trips (SQLite), kept outside the cache`.
  - `.gitignore` and `.dockerignore`: add a line `data/`.
  - `Dockerfile`: replace the cache line/comment with
    ```dockerfile
    # Search cache (hikes, stays, geocoding) and saved trips (SQLite) live here; compose mounts volumes on them.
    RUN mkdir -p /app/.cache /app/data && chown -R hick /app/.cache /app/data
    ```
  - `docker-compose.yml`: under `volumes:` of the service add `- hick-data:/app/data   # saved trips (SQLite) – survives rebuilds`; under the top-level `volumes:` add `hick-data:`.
  - `.env.example`: add a commented line `# TRIPS_DB=data/trips.db   # where saved trips are stored (SQLite)`.

- [ ] **Step 7: Run tests** — `env -u VIRTUAL_ENV uv run pytest -q` → all pass (298 + new). Also `docker compose config -q` (skip silently if docker is not installed).

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/trips_store.py app/config.py .gitignore .dockerignore Dockerfile docker-compose.yml .env.example tests/test_trips_store.py tests/test_models.py
git commit -m "feat(hick-toors): saved trips models and SQLite store"
```

---

### Task 2: Markdown export

**Files:**
- Create: `app/trip_markdown.py`
- Test: `tests/test_trip_markdown.py`

**Interfaces:**
- Consumes: `SavedTrip`, `Hike`, `Stay` from `app.models`.
- Produces: `trip_markdown(trip: SavedTrip, today: date) -> str`, `export_filename(name: str) -> str`, `content_disposition(name: str) -> str`, `md_escape(text: str) -> str`.

- [ ] **Step 1: Write the failing tests** (`tests/test_trip_markdown.py`)

```python
from datetime import date, datetime, timezone

from app.models import Hike, SavedTrip, Stay
from app.trip_markdown import content_disposition, export_filename, md_escape, trip_markdown

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 10)
HIKE = Hike(id="wikiloc:1", name="Vikos Gorge", url="https://es.wikiloc.com/vikos", lat=39.9, lon=20.7,
            distance_km=12.0, elevation_gain_m=600, difficulty="Moderate", trailrank=70, near="Monodendri",
            drive_minutes=130)
STAY = Stay(id="airbnb:1", source="airbnb", name="Stone house", url="https://www.airbnb.com/rooms/1",
            lat=39.95, lon=20.75, total_price=190, currency="EUR", rating=4.8, badge="pets")


def trip(**kw):
    base = dict(id="t1", name="Zagori long weekend", created_at=NOW, updated_at=NOW)
    return SavedTrip(**{**base, **kw})


FULL = """# Zagori long weekend

📅 Fri 23 Oct 2026 → Sun 25 Oct 2026 (2 nights) · 👥 2 guests · 🐕 With dog
⭐ Saved from Suggest trips – score 77.5/100

## 🥾 Hikes (1)

1. **[Vikos Gorge](https://es.wikiloc.com/vikos)** – 12 km · +600 m · Moderate · TrailRank 70 · 🚗 2h 10m
   Near Monodendri · [📍 Map](https://www.google.com/maps/search/?api=1&query=39.9,20.7)

## 🏡 Stays (1)

- **[Stone house](https://www.airbnb.com/rooms/1)** – Airbnb · €190 total (€95/night) · ★ 4.8 · 🐕 Pets allowed
  [📍 Map](https://www.google.com/maps/search/?api=1&query=39.95,20.75)

## 📝 Notes

Bring the dog's water bowl.

---
Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved.
"""

EMPTY = """# Empty trip

📅 Dates not set · 👥 1 guest

## 🥾 Hikes (0)

_None yet._

## 🏡 Stays (0)

_None yet._

---
Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved.
"""


def test_full_trip():
    t = trip(start=date(2026, 10, 23), end=date(2026, 10, 25), guests=2, dog=True, source="suggested",
             score=77.5, hikes=[HIKE], stays=[STAY], notes="Bring the dog's water bowl.\n")
    assert trip_markdown(t, TODAY) == FULL


def test_empty_trip_no_dates_no_dog():
    t = trip(name="Empty trip", guests=1, dog=False)
    assert trip_markdown(t, TODAY) == EMPTY


def test_one_night_foreign_currency_and_missing_fields():
    bare = Hike(id="wikiloc:2", name="Bare", url="https://es.wikiloc.com/b", lat=1.5, lon=2.5)
    chf = STAY.model_copy(update={"currency": "CHF", "total_price": 300, "rating": None, "badge": None})
    md = trip_markdown(trip(start=date(2026, 10, 23), end=date(2026, 10, 24), hikes=[bare], stays=[chf]), TODAY)
    assert "(1 night)" in md
    assert "1. **[Bare](https://es.wikiloc.com/b)**\n   [📍 Map](https://www.google.com/maps/search/?api=1&query=1.5,2.5)" in md
    assert "– Airbnb · CHF 300 total (CHF 300/night)\n" in md


def test_same_day_dates_have_no_nights_or_per_night():
    md = trip_markdown(trip(start=date(2026, 10, 23), end=date(2026, 10, 23), stays=[STAY]), TODAY)
    assert "📅 Fri 23 Oct 2026 → Fri 23 Oct 2026 · " in md
    assert "€190 total ·" in md and "/night" not in md


def test_drive_variants_and_booking():
    ferry = HIKE.model_copy(update={"id": "wikiloc:3", "ferry_needed": True})
    est = HIKE.model_copy(update={"id": "wikiloc:4", "drive_minutes": 45, "drive_estimated": True})
    book = STAY.model_copy(update={"id": "booking:1", "source": "booking", "badge": "garden_unconfirmed"})
    md = trip_markdown(trip(hikes=[ferry, est], stays=[book]), TODAY)
    assert "· 🚗 ⛴️ ferry needed\n" in md
    assert "· 🚗 ≈ 45m\n" in md
    assert "2. **[Vikos Gorge]" in md
    assert "– Booking.com · €190 total · ★ 4.8 · ⚠️ Garden – pets not confirmed\n" in md


def test_escaping_and_unsafe_urls():
    odd = HIKE.model_copy(update={"name": "Mt *Olympus* [north]_trail", "url": "javascript:alert(1)"})
    paren = STAY.model_copy(update={"url": "https://x.example/a (b)"})
    md = trip_markdown(trip(name="<Hi> `there`", hikes=[odd], stays=[paren]), TODAY)
    assert md.startswith("# \\<Hi\\> \\`there\\`\n")
    assert "1. **Mt \\*Olympus\\* \\[north\\]\\_trail** – " in md
    assert "javascript" not in md
    assert "(https://x.example/a%20%28b%29)" in md
    assert md_escape("a\\b") == "a\\\\b"


def test_greek_name_kept_in_body():
    assert trip_markdown(trip(name="Ζαγόρι"), TODAY).startswith("# Ζαγόρι\n")


def test_export_filename():
    assert export_filename("Zagori long weekend") == "zagori-long-weekend.md"
    assert export_filename("  Pelion: 3 days!! ") == "pelion-3-days.md"
    assert export_filename("Ζαγόρι") == "trip.md"
    assert export_filename("a" * 100) == "a" * 60 + ".md"


def test_content_disposition():
    assert content_disposition("Ζαγόρι 2") == (
        "attachment; filename=\"2.md\"; filename*=UTF-8''%CE%96%CE%B1%CE%B3%CF%8C%CF%81%CE%B9%202.md")
```

- [ ] **Step 2: Run to verify failure** — `env -u VIRTUAL_ENV uv run pytest -q tests/test_trip_markdown.py` → ImportError.

- [ ] **Step 3: Implement** (`app/trip_markdown.py`)

```python
"""Render a saved trip as a Markdown document (pure, no I/O)."""

import math
import re
from datetime import date
from urllib.parse import quote

from app.models import Hike, SavedTrip, Stay

BADGES = {
    "pets": "🐕 Pets allowed",
    "pets_garden": "🐕🌳 Pets allowed + garden",
    "garden": "🌳 Garden",
    "garden_unconfirmed": "⚠️ Garden – pets not confirmed",
}
SOURCES = {"airbnb": "Airbnb", "booking": "Booking.com"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
_SAFE_URL = re.compile(r"^https?://", re.IGNORECASE)


def md_escape(text: str) -> str:
    return _SPECIAL.sub(r"\\\1", text)


def _link(name: str, url: str | None) -> str:
    label = md_escape(name)
    if not url or not _SAFE_URL.match(url):
        return label
    href = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"[{label}]({href})"


def _map(lat: float, lon: float) -> str:
    return f"[📍 Map](https://www.google.com/maps/search/?api=1&query={lat},{lon})"


def _day(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def _minutes(m: int, estimated: bool) -> str:
    h, mm = divmod(m, 60)
    return f"{'≈ ' if estimated else ''}{f'{h}h ' if h else ''}{mm}m"


def _money(value: float, currency: str | None) -> str:
    prefix = "€" if not currency or currency == "EUR" else f"{currency} "
    return f"{prefix}{math.floor(value + 0.5)}"  # half-up, like the UI's Math.round


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _nights(trip: SavedTrip) -> int:
    return (trip.end - trip.start).days if trip.start and trip.end else 0


def _summary(trip: SavedTrip, nights: int) -> str:
    if trip.start and trip.end:
        when = f"📅 {_day(trip.start)} → {_day(trip.end)}"
        if nights > 0:
            when += f" ({_plural(nights, 'night')})"
    else:
        when = "📅 Dates not set"
    parts = [when, f"👥 {_plural(trip.guests, 'guest')}"]
    if trip.dog:
        parts.append("🐕 With dog")
    return " · ".join(parts)


def _hike_lines(n: int, h: Hike) -> list[str]:
    facts = []
    if h.distance_km is not None:
        facts.append(f"{h.distance_km:g} km")
    if h.elevation_gain_m is not None:
        facts.append(f"+{h.elevation_gain_m} m")
    if h.difficulty:
        facts.append(md_escape(h.difficulty))
    if h.trailrank is not None:
        facts.append(f"TrailRank {h.trailrank}")
    if h.ferry_needed:
        facts.append("🚗 ⛴️ ferry needed")
    elif h.drive_minutes is not None:
        facts.append(f"🚗 {_minutes(h.drive_minutes, h.drive_estimated)}")
    head = f"{n}. **{_link(h.name, h.url)}**" + (f" – {' · '.join(facts)}" if facts else "")
    second = [f"Near {md_escape(h.near)}"] if h.near else []
    return [head, "   " + " · ".join([*second, _map(h.lat, h.lon)])]


def _stay_lines(s: Stay, nights: int) -> list[str]:
    facts = [SOURCES.get(s.source, s.source)]
    if s.total_price is not None:
        price = f"{_money(s.total_price, s.currency)} total"
        if nights > 0:
            price += f" ({_money(s.total_price / nights, s.currency)}/night)"
        facts.append(price)
    if s.rating is not None:
        facts.append(f"★ {s.rating:g}")
    if s.badge:
        facts.append(BADGES[s.badge])
    return [f"- **{_link(s.name, s.url)}** – {' · '.join(facts)}", "  " + _map(s.lat, s.lon)]


def trip_markdown(trip: SavedTrip, today: date) -> str:
    nights = _nights(trip)
    out = [f"# {md_escape(trip.name)}", "", _summary(trip, nights)]
    if trip.source == "suggested":
        score = f" – score {trip.score:g}/100" if trip.score is not None else ""
        out.append(f"⭐ Saved from Suggest trips{score}")
    out += ["", f"## 🥾 Hikes ({len(trip.hikes)})", ""]
    if trip.hikes:
        for n, h in enumerate(trip.hikes, 1):
            out += _hike_lines(n, h)
    else:
        out.append("_None yet._")
    out += ["", f"## 🏡 Stays ({len(trip.stays)})", ""]
    if trip.stays:
        for s in trip.stays:
            out += _stay_lines(s, nights)
    else:
        out.append("_None yet._")
    if trip.notes.strip():
        out += ["", "## 📝 Notes", "", trip.notes.strip()]
    out += ["", "---", f"Exported from hick-toors on {today.isoformat()}. Prices and ratings as seen when saved."]
    return "\n".join(out) + "\n"


def export_filename(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")
    return f"{slug or 'trip'}.md"


def content_disposition(name: str) -> str:
    return f"attachment; filename=\"{export_filename(name)}\"; filename*=UTF-8''{quote(name + '.md', safe='')}"
```

- [ ] **Step 4: Run** — `env -u VIRTUAL_ENV uv run pytest -q tests/test_trip_markdown.py` → PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/trip_markdown.py tests/test_trip_markdown.py
git commit -m "feat(hick-toors): export saved trips as Markdown"
```

---

### Task 3: API routes + read-only MCP tools

**Files:**
- Modify: `app/main.py`
- Modify: `app/mcp_tools.py`
- Modify: `tests/test_api.py` (helper `client()` must use a tmp DB; new tests), `tests/test_mcp_tools.py` (new tests)
- Create: `tests/test_api_saved_trips.py`

**Interfaces:**
- Consumes: `TripStore`, `TripFull` (Task 1), `trip_markdown`, `content_disposition` (Task 2).
- Produces: routes listed in the spec; `create_app(engine=None, ask_runner=None, settings=None, trip_store=None)`; MCP `list_saved_trips()`, `get_saved_trip(trip_id)`, module global `_trip_store` (monkeypatchable) and `trip_store()` accessor.

- [ ] **Step 1: Make every existing test client use a tmp DB** — in `tests/test_api.py` change the helper to
  `settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")`. Grep `tests/` for other `create_app(` calls and give each the same `trips_db=tmp_path / ...` (or a `trip_store=TripStore(tmp_path / "trips.db")`).

- [ ] **Step 2: Write the failing API tests** (`tests/test_api_saved_trips.py`)

```python
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.trips_store import TripStore
from tests.test_api import FakeAsk, FakeEngine

HIKE = {"id": "wikiloc:1", "name": "Vikos Gorge", "url": "https://es.wikiloc.com/v", "lat": 39.9, "lon": 20.7}
HIKE2 = {**HIKE, "id": "wikiloc:2", "name": "Papingo pools"}
STAY = {"id": "airbnb:1", "source": "airbnb", "name": "Stone house", "url": "https://www.airbnb.com/rooms/1",
        "lat": 39.95, "lon": 20.75, "total_price": 190, "currency": "EUR"}


def make(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "unused.db")
    store = TripStore(tmp_path / "trips.db")
    return TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings,
                                 trip_store=store)), store


def test_crud_flow(tmp_path):
    c, store = make(tmp_path)
    assert c.get("/api/saved-trips").json() == []
    r = c.post("/api/saved-trips", json={"name": " Zagori ", "start": "2026-10-23", "end": "2026-10-25",
                                         "hikes": [HIKE], "stays": [STAY], "source": "suggested", "score": 77.5})
    assert r.status_code == 201
    trip = r.json()
    assert trip["name"] == "Zagori" and trip["hikes"][0]["id"] == "wikiloc:1"
    tid = trip["id"]
    listed = c.get("/api/saved-trips").json()
    assert [(t["id"], t["hike_count"], t["stay_count"], t["source"]) for t in listed] == [(tid, 1, 1, "suggested")]
    assert c.get(f"/api/saved-trips/{tid}").json()["score"] == 77.5
    r = c.put(f"/api/saved-trips/{tid}", json={"name": "Renamed", "notes": "n", "hikes": [HIKE2, HIKE]})
    assert r.status_code == 200
    assert [h["id"] for h in r.json()["hikes"]] == ["wikiloc:2", "wikiloc:1"]
    assert r.json()["created_at"] == trip["created_at"]
    assert r.json()["stays"] == []
    r = c.delete(f"/api/saved-trips/{tid}")
    assert r.status_code == 204 and r.content == b""
    assert c.get("/api/saved-trips").json() == []


def test_not_found(tmp_path):
    c, _ = make(tmp_path)
    for method, path, body in [("get", "/api/saved-trips/nope", None),
                               ("put", "/api/saved-trips/nope", {"name": "x"}),
                               ("delete", "/api/saved-trips/nope", None),
                               ("post", "/api/saved-trips/nope/items", {"hike": HIKE}),
                               ("get", "/api/saved-trips/nope/export.md", None)]:
        r = c.request(method.upper(), path, json=body)
        assert r.status_code == 404, path
        assert r.json() == {"detail": "Trip not found"}


def test_validation(tmp_path):
    c, _ = make(tmp_path)
    assert c.post("/api/saved-trips", json={"name": "  "}).status_code == 422
    r = c.post("/api/saved-trips", json={"name": "t", "start": "2026-10-25", "end": "2026-10-23"})
    assert r.status_code == 422 and "end must be on or after start" in r.text
    tid = c.post("/api/saved-trips", json={"name": "t"}).json()["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={})
    assert r.status_code == 422 and "Give exactly one of hike or stay" in r.text


def test_add_items_dedupe_and_full(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "t", "hikes": [HIKE]}).json()["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200 and len(r.json()["hikes"]) == 1
    r = c.post(f"/api/saved-trips/{tid}/items", json={"stay": STAY})
    assert [s["id"] for s in r.json()["stays"]] == ["airbnb:1"]
    full = c.post("/api/saved-trips", json={"name": "f", "stays": [{**STAY, "id": f"airbnb:{i}"} for i in range(50)]})
    r = c.post(f"/api/saved-trips/{full.json()['id']}/items", json={"stay": {**STAY, "id": "airbnb:x"}})
    assert r.status_code == 409
    assert r.json() == {"detail": "A trip holds at most 50 hikes and 50 stays"}


def test_export_markdown(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "Ζαγόρι weekend", "hikes": [HIKE]}).json()["id"]
    r = c.get(f"/api/saved-trips/{tid}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="weekend.md"; filename*=UTF-8\'\'')
    assert r.text.startswith("# Ζαγόρι weekend\n")
    assert "**[Vikos Gorge](https://es.wikiloc.com/v)**" in r.text


def test_default_store_uses_settings_path(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "d" / "trips.db")
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings))
    c.post("/api/saved-trips", json={"name": "t"})
    assert (tmp_path / "d" / "trips.db").exists()
```

- [ ] **Step 3: Write the failing MCP tests** (append to `tests/test_mcp_tools.py`)

```python
from app.models import SavedTripIn
from app.trips_store import TripStore


@pytest.fixture
def trips(monkeypatch, tmp_path):
    store = TripStore(tmp_path / "trips.db")
    monkeypatch.setattr(tools, "_trip_store", store)
    return store


async def test_list_saved_trips(trips):
    assert json.loads(await tools.list_saved_trips()) == []
    t = trips.create(SavedTripIn(name="Zagori"))
    out = json.loads(await tools.list_saved_trips())
    assert [(s["id"], s["name"], s["hike_count"]) for s in out] == [(t.id, "Zagori", 0)]


async def test_get_saved_trip_markdown_or_error(trips):
    t = trips.create(SavedTripIn(name="Zagori"))
    assert (await tools.get_saved_trip(t.id)).startswith("# Zagori\n")
    assert json.loads(await tools.get_saved_trip("nope")) == {"error": "Trip not found"}
```

- [ ] **Step 4: Run to verify failure** — `env -u VIRTUAL_ENV uv run pytest -q tests/test_api_saved_trips.py tests/test_mcp_tools.py` → FAIL.

- [ ] **Step 5: Implement the routes** in `app/main.py`:
  - imports: `from datetime import date`, `from fastapi import FastAPI, HTTPException, Query, Response`, `from app.models import ... SavedTrip, SavedTripIn, SavedTripSummary, TripItemAdd`, `from app.trip_markdown import content_disposition, trip_markdown`, `from app.trips_store import TripFull, TripStore`.
  - signature `def create_app(engine=None, ask_runner=None, settings: Settings | None = None, trip_store: TripStore | None = None) -> FastAPI:`; after the ask runner: `trip_store = trip_store or TripStore(settings.trips_db)` and `app.state.trips = trip_store`.
  - before `@app.get("/")` add:

```python
    def found(trip):
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        return trip

    # Saved trips: sync handlers, so sqlite runs in FastAPI's threadpool.
    @app.get("/api/saved-trips", response_model=list[SavedTripSummary])
    def list_saved_trips():
        return trip_store.list_trips()

    @app.post("/api/saved-trips", response_model=SavedTrip, status_code=201)
    def create_saved_trip(body: SavedTripIn):
        return trip_store.create(body)

    @app.get("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def get_saved_trip(trip_id: str):
        return found(trip_store.get(trip_id))

    @app.put("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def replace_saved_trip(trip_id: str, body: SavedTripIn):
        return found(trip_store.replace(trip_id, body))

    @app.delete("/api/saved-trips/{trip_id}", status_code=204)
    def delete_saved_trip(trip_id: str):
        if not trip_store.delete(trip_id):
            raise HTTPException(status_code=404, detail="Trip not found")
        return Response(status_code=204)

    @app.post("/api/saved-trips/{trip_id}/items", response_model=SavedTrip)
    def add_saved_trip_item(trip_id: str, body: TripItemAdd):
        try:
            return found(trip_store.add_item(trip_id, body))
        except TripFull:
            raise HTTPException(status_code=409, detail="A trip holds at most 50 hikes and 50 stays")

    @app.get("/api/saved-trips/{trip_id}/export.md")
    def export_saved_trip(trip_id: str):
        trip = found(trip_store.get(trip_id))
        return Response(trip_markdown(trip, date.today()), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": content_disposition(trip.name)})
```

- [ ] **Step 6: Implement the MCP tools** in `app/mcp_tools.py` (after `engine()`; add `from datetime import date` at the top):

```python
_trip_store = None


def trip_store():
    global _trip_store
    if _trip_store is None:
        from app.config import Settings
        from app.trips_store import TripStore

        _trip_store = TripStore(Settings().trips_db)
    return _trip_store
```

and before `if __name__ == "__main__":`

```python
@mcp.tool()
async def list_saved_trips() -> str:
    """List the user's saved trips (read-only), newest first.

    Returns JSON [{id, name, start, end, source ("manual" | "suggested"), hike_count, stay_count,
    updated_at}]. Use get_saved_trip(id) for the full trip.
    """
    return json.dumps([s.model_dump(mode="json") for s in trip_store().list_trips()])


@mcp.tool()
async def get_saved_trip(trip_id: str) -> str:
    """Return one saved trip (id from list_saved_trips) as Markdown: dates, guests, dog, hikes with
    Wikiloc links and stats, stays with links/prices/pet badges, and notes. Read-only.
    Unknown id -> JSON {"error": "Trip not found"}.
    """
    from app.trip_markdown import trip_markdown

    trip = trip_store().get(trip_id)
    if trip is None:
        return json.dumps({"error": "Trip not found"})
    return trip_markdown(trip, date.today())
```

- [ ] **Step 7: Check Ask Claude can use them** — read `app/ask.py`: if its allowed-tools list names tools individually (not the `mcp__hicktoors` prefix), add `mcp__hicktoors__list_saved_trips` and `mcp__hicktoors__get_saved_trip`; if `tests/test_ask.py` pins the argv, update that expectation. If it already allows the whole server, change nothing.

- [ ] **Step 8: Run** — `env -u VIRTUAL_ENV uv run pytest -q` → all green. Confirm no `data/trips.db` was created by the test run inside the project (`git status --porcelain --ignored data` shows nothing new from tests — importing `app.main` builds the module-level `app`, which may create `data/trips.db`; that is acceptable only because `data/` is ignored, but tests themselves must write to tmp).

- [ ] **Step 9: Commit**

```bash
git add app/main.py app/mcp_tools.py app/ask.py tests/
git commit -m "feat(hick-toors): saved trips API, Markdown export route, read-only MCP tools"
```

---

### Task 4: UI — ＋ Trip, 💾 Save, 🧳 My trips dialog; UI check; README

**Files:**
- Create: `static/trips.js`
- Modify: `static/app.js` (`api()` method arg; ＋ Trip on list items + popups; 💾 Save on picks)
- Modify: `static/index.html` (header button, dialog, menu, script tag)
- Modify: `static/style.css`
- Modify: `tests/ui/check_ui.py` (trips scenario)
- Modify: `README.md`

**Interfaces:**
- Consumes (from `app.js`, globals of classic scripts): `$`, `esc`, `state`, `api(path, body, method)`, `notices`, `dates`, `guests`, `clearAll`, `addHike`, `addStay`, `fitAll`, `map`.
- Produces (globals used by `app.js` at runtime): `tripButton(kind, id) -> HTMLButtonElement`, `tripButtonHtml(kind, id) -> string`, `saveComboButton(combo) -> HTMLButtonElement`.

- [ ] **Step 1: `api()` takes a method** — in `static/app.js` change the signature to `async function api(path, body, method)` and the fetch option to `method: method || (body ? "POST" : "GET"),`. (A 204 has no JSON; the existing `.catch(() => ({}))` already returns `{}`.)

- [ ] **Step 2: ＋ Trip and 💾 Save hooks in `static/app.js`**
  - `hikePopup(h)`: before the closing `</div>` add `<br>${tripButtonHtml("hike", h.id)}`; `stayPopup(s)`: same with `"stay", s.id`.
  - `addHike(h)`: `const li = listItem(...)` (keep the arguments) then `li.appendChild(tripButton("hike", h.id));`. `addStay(s)`: same with `"stay"`.
  - `renderCombos`: after `li.title = meta;` add `li.appendChild(saveComboButton(c));`.

- [ ] **Step 3: Markup in `static/index.html`**
  - In the header, right after the brand: `<button type="button" id="trips-btn" class="tool-btn">🧳 My trips <span id="trips-count"></span></button>`
  - Before the first `<script>` tag:

```html
  <div id="trip-menu" class="trip-menu" role="menu" hidden></div>
  <dialog id="trips-dialog" class="trips-dialog" aria-labelledby="trips-title">
    <div class="dlg-head"><h2 id="trips-title">🧳 My trips</h2>
      <button type="button" id="trips-close" class="tool-btn" aria-label="Close">✕</button></div>
    <div id="trips-error" class="notice" hidden></div>
    <div id="trips-list-view">
      <ul id="trips-list" class="trips-list"></ul>
      <p id="trips-empty" class="list-note" hidden>No saved trips yet – use ＋ Trip on a hike or stay, or 💾 Save on a Trip pick.</p>
    </div>
    <form id="trip-editor" hidden>
      <button type="button" id="trip-back" class="link-btn">← All trips</button>
      <label class="wide">Name <input id="trip-name" maxlength="120" required></label>
      <div class="row">
        <label>📅 <input type="date" id="trip-start"></label> → <input type="date" id="trip-end" aria-label="End date">
        <label>👥 <input type="number" id="trip-guests" min="1" max="16" step="1"></label>
        <label><input type="checkbox" id="trip-dog"> 🐕 Dog</label>
      </div>
      <h3>🥾 Hikes</h3><ul id="trip-hikes" class="trip-items"></ul>
      <h3>🏡 Stays</h3><ul id="trip-stays" class="trip-items"></ul>
      <label class="wide">📝 Notes <textarea id="trip-notes" maxlength="5000" rows="4"></textarea></label>
      <div class="dlg-actions">
        <button type="submit" id="trip-save" class="primary">Save changes</button>
        <button type="button" id="trip-show" class="tool-btn">🗺️ Show on map</button>
        <button type="button" id="trip-export" class="tool-btn">⬇ Export .md</button>
        <button type="button" id="trip-delete" class="tool-btn danger">Delete</button>
      </div>
    </form>
  </dialog>
```
  - Add `<script src="/static/trips.js"></script>` **after** `app.js`.

- [ ] **Step 4: `static/trips.js`**

```js
"use strict";
// Saved trips: ＋ Trip menu on hikes/stays, 💾 Save on Trip picks, and the 🧳 My trips dialog.
// Loaded after app.js and uses its globals ($, esc, state, api, notices, dates, guests, clearAll, ...).
const trips = { list: [], current: null, deleteTimer: null };
const LAST_TRIP_KEY = "hick-toors:last-trip";

function lastTripId() { try { return localStorage.getItem(LAST_TRIP_KEY); } catch { return null; } }
function rememberTrip(id) { try { localStorage.setItem(LAST_TRIP_KEY, id); } catch { /* storage blocked */ } }
const tripPath = (id, rest = "") => `/api/saved-trips/${encodeURIComponent(id)}${rest}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

async function refreshTrips() {
  trips.list = await api("/api/saved-trips");
  $("trips-count").textContent = `(${trips.list.length})`;
  return trips.list;
}
function formTripFields() {
  const { start, end } = dates();
  return { start: start || null, end: end || null, guests: guests(), dog: $("dog").checked };
}
function itemOf(kind, id) { return (kind === "hike" ? state.hikes : state.stays).get(id); }

// ---- ＋ Trip (list items and map popups) ----
function tripButton(kind, id) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "add-trip link-btn";
  b.dataset.kind = kind;
  b.dataset.id = id;
  b.title = "Add to a saved trip";
  b.textContent = "＋ Trip";
  return b;
}
function tripButtonHtml(kind, id) {
  return `<button type="button" class="add-trip link-btn" data-kind="${esc(kind)}" data-id="${esc(id)}" title="Add to a saved trip">＋ Trip</button>`;
}
function closeTripMenu() { $("trip-menu").hidden = true; }
async function openTripMenu(button) {
  const { kind, id } = button.dataset;
  const item = itemOf(kind, id);
  if (!item) return;
  await refreshTrips();
  const last = lastTripId();
  const ordered = [...trips.list].sort((a, b) => (b.id === last) - (a.id === last));
  const menu = $("trip-menu");
  menu.replaceChildren();
  const entry = (label, action) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.textContent = label;
    b.addEventListener("click", async () => {
      closeTripMenu();
      try { await action(); } catch (err) { notices([err.message]); }
    });
    menu.appendChild(b);
  };
  entry("➕ New trip", () => newTripWith(kind, item));
  for (const t of ordered) entry(t.name, () => addToTrip(t, kind, item));
  const r = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 260))}px`;
  menu.style.top = `${r.bottom + 4}px`;
  menu.hidden = false;
  menu.querySelector("button")?.focus();
}
async function newTripWith(kind, item) {
  const trip = await api("/api/saved-trips", { name: String(item.name || "New trip").slice(0, 120), ...formTripFields(),
    hikes: kind === "hike" ? [item] : [], stays: kind === "stay" ? [item] : [] });
  rememberTrip(trip.id);
  await refreshTrips();
  notices([`Added to “${trip.name}” – rename it in 🧳 My trips`]);
}
async function addToTrip(summary, kind, item) {
  const before = kind === "hike" ? summary.hike_count : summary.stay_count;
  const trip = await api(tripPath(summary.id, "/items"), { [kind]: item });
  rememberTrip(trip.id);
  await refreshTrips();
  const after = (kind === "hike" ? trip.hikes : trip.stays).length;
  notices([after > before ? `Added to “${trip.name}”` : `Already in “${trip.name}”`]);
}
// Capture phase: a ＋ Trip click must not also select the hike / focus the stay underneath it.
document.addEventListener("click", (event) => {
  const button = event.target.closest?.(".add-trip");
  if (button) {
    event.preventDefault();
    event.stopPropagation();
    openTripMenu(button).catch((err) => notices([err.message]));
    return;
  }
  if (!event.target.closest?.("#trip-menu")) closeTripMenu();
}, true);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeTripMenu(); });

// ---- 💾 Save on a Trip pick ----
function saveComboButton(combo) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "save-combo link-btn";
  b.textContent = "💾 Save";
  b.addEventListener("click", async (event) => {
    event.stopPropagation();
    b.disabled = true;
    try {
      const trip = await api("/api/saved-trips", { name: `${combo.hike.name} + ${combo.stay.name}`.slice(0, 120),
        ...formTripFields(), source: "suggested", score: combo.score ?? null, hikes: [combo.hike], stays: [combo.stay] });
      rememberTrip(trip.id);
      await refreshTrips();
      b.textContent = "✓ Saved";
      notices([`Saved “${trip.name}” – see 🧳 My trips`]);
    } catch (err) {
      b.disabled = false;
      notices([err.message]);
    }
  });
  return b;
}

// ---- 🧳 My trips dialog ----
function showTripsError(message) {
  $("trips-error").textContent = message || "";
  $("trips-error").hidden = !message;
}
function showTripList() {
  trips.current = null;
  resetDelete();
  $("trip-editor").hidden = true;
  $("trips-list-view").hidden = false;
}
async function reloadTripList() {
  try { renderTripList(await refreshTrips()); } catch (err) { showTripsError(err.message); }
}
async function openTripsDialog() {
  showTripsError("");
  showTripList();
  if (!$("trips-dialog").open) $("trips-dialog").showModal();
  await reloadTripList();
}
function tripDates(t) { return t.start && t.end ? `${t.start} → ${t.end}` : "Dates not set"; }
function renderTripList(list) {
  const ul = $("trips-list");
  ul.replaceChildren();
  $("trips-empty").hidden = list.length > 0;
  for (const t of list) {
    const li = document.createElement("li");
    li.tabIndex = 0;
    const strong = document.createElement("strong");
    strong.textContent = t.name;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${tripDates(t)} · ${plural(t.hike_count, "hike")} · ${plural(t.stay_count, "stay")}${t.source === "suggested" ? " · ⭐" : ""}`;
    li.append(strong, meta);
    li.addEventListener("click", () => openTrip(t.id));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter") openTrip(t.id); });
    ul.appendChild(li);
  }
}
async function openTrip(id) {
  showTripsError("");
  try { trips.current = await api(tripPath(id)); } catch (err) { showTripsError(err.message); return; }
  const t = trips.current;
  $("trip-name").value = t.name;
  $("trip-start").value = t.start || "";
  $("trip-end").value = t.end || "";
  $("trip-guests").value = t.guests;
  $("trip-dog").checked = t.dog;
  $("trip-notes").value = t.notes || "";
  resetDelete();
  renderTripItems();
  $("trips-list-view").hidden = true;
  $("trip-editor").hidden = false;
}
function renderTripItems() {
  for (const kind of ["hikes", "stays"]) {
    const ul = $(`trip-${kind}`);
    ul.replaceChildren();
    const items = trips.current[kind];
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "list-note";
      li.textContent = "None yet.";
      ul.appendChild(li);
      continue;
    }
    items.forEach((item, i) => {
      const li = document.createElement("li");
      const safe = /^https?:\/\//i.test(String(item.url ?? ""));
      const label = document.createElement(safe ? "a" : "span");
      label.textContent = item.name;
      if (safe) { label.href = item.url; label.target = "_blank"; label.rel = "noopener"; }
      const tools = document.createElement("span");
      tools.className = "item-tools";
      const tool = (text, title, disabled, action) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "link-btn";
        b.textContent = text;
        b.title = title;
        b.setAttribute("aria-label", `${title}: ${item.name}`);
        b.disabled = disabled;
        b.addEventListener("click", action);
        tools.appendChild(b);
      };
      tool("↑", "Move up", i === 0, () => moveItem(kind, i, -1));
      tool("↓", "Move down", i === items.length - 1, () => moveItem(kind, i, 1));
      tool("✕", "Remove", false, () => { items.splice(i, 1); renderTripItems(); });
      li.append(label, tools);
      ul.appendChild(li);
    });
  }
}
function moveItem(kind, i, delta) {
  const items = trips.current[kind];
  [items[i], items[i + delta]] = [items[i + delta], items[i]];
  renderTripItems();
}
function editorBody() {
  const t = trips.current;
  return { name: $("trip-name").value, start: $("trip-start").value || null, end: $("trip-end").value || null,
    guests: Math.min(16, Math.max(1, Math.round(Number($("trip-guests").value)) || 1)), dog: $("trip-dog").checked,
    notes: $("trip-notes").value, source: t.source, score: t.score, hikes: t.hikes, stays: t.stays };
}
async function persistTrip() {
  showTripsError("");
  try {
    trips.current = await api(tripPath(trips.current.id), editorBody(), "PUT");
    await refreshTrips();
    return true;
  } catch (err) {
    showTripsError(err.message);
    return false;
  }
}
async function saveTrip(event) {
  event.preventDefault();
  if (!trips.current || !(await persistTrip())) return;
  $("trip-save").textContent = "✓ Saved";
  setTimeout(() => { $("trip-save").textContent = "Save changes"; }, 1500);
}
async function exportTrip() {
  // Export what is on screen: save first, then download the server's Markdown.
  if (!trips.current || !(await persistTrip())) return;
  window.location.href = tripPath(trips.current.id, "/export.md");
}
function showTripOnMap() {
  const t = trips.current;
  if (!t) return;
  const name = $("trip-name").value.trim() || t.name;
  clearAll();
  t.hikes.forEach(addHike);
  t.stays.forEach(addStay);
  state.comboView = true;  // clicking a trip hike must not replace its stays with a new search
  $("hike-count").textContent = `(${state.hikes.size})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("stay-note").textContent = `in trip “${name}”`;
  notices([`Showing trip “${name}”`]);
  $("trips-dialog").close();
  fitAll();
}
function resetDelete() {
  clearTimeout(trips.deleteTimer);
  trips.deleteTimer = null;
  $("trip-delete").textContent = "Delete";
}
async function deleteTrip() {
  if (!trips.current) return;
  if (!trips.deleteTimer) {  // first click arms, second click (within 4 s) deletes
    $("trip-delete").textContent = "Confirm delete";
    trips.deleteTimer = setTimeout(resetDelete, 4000);
    return;
  }
  resetDelete();
  try {
    await api(tripPath(trips.current.id), undefined, "DELETE");
    showTripList();
    await reloadTripList();
  } catch (err) {
    showTripsError(err.message);
  }
}

$("trips-btn").addEventListener("click", () => openTripsDialog());
$("trips-close").addEventListener("click", () => $("trips-dialog").close());
$("trip-back").addEventListener("click", () => { showTripsError(""); showTripList(); reloadTripList(); });
$("trip-editor").addEventListener("submit", saveTrip);
$("trip-show").addEventListener("click", showTripOnMap);
$("trip-export").addEventListener("click", exportTrip);
$("trip-delete").addEventListener("click", deleteTrip);
refreshTrips().catch(() => { $("trips-count").textContent = ""; });
```

- [ ] **Step 5: Styles** — append to `static/style.css` (match existing palette `#2f6f4e`, `#dde3dc`, `#5d6b63`; reuse `.tool-btn` / `.link-btn` / `.filters-dialog` looks already defined there — read the file first and reuse its button rules):

```css
.add-trip, .save-combo { margin-left: 6px; font-size: 12px; }
.trip-menu { position: fixed; z-index: 2000; min-width: 200px; max-width: 260px; max-height: 50vh; overflow-y: auto; padding: 4px; background: #fff; border: 1px solid #dde3dc; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.18); }
.trip-menu button { display: block; width: 100%; text-align: left; padding: 6px 10px; border: 0; border-radius: 6px; background: none; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trip-menu button:hover, .trip-menu button:focus { background: #e7f1ea; }
.trips-dialog { width: min(640px, calc(100vw - 32px)); max-height: calc(100vh - 32px); border: 1px solid #dde3dc; border-radius: 10px; padding: 12px 16px; }
.trips-dialog::backdrop { background: rgba(0,0,0,.35); }
.trips-dialog .dlg-head { display: flex; justify-content: space-between; align-items: center; }
.trips-dialog h2 { margin: 0; font-size: 16px; }
.trips-dialog h3 { margin: 12px 0 4px; font-size: 14px; }
.trips-list, .trip-items { list-style: none; margin: 8px 0; padding: 0; }
.trips-list li { padding: 8px; border-radius: 6px; cursor: pointer; }
.trips-list li:hover, .trips-list li:focus { background: #e7f1ea; outline: none; }
.trips-list .meta { color: #5d6b63; font-size: 12px; }
.trip-items li { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 4px 0; border-bottom: 1px solid #eef1ed; }
.trip-items .item-tools { white-space: nowrap; }
#trip-editor label.wide { display: block; margin: 8px 0; }
#trip-editor label.wide input, #trip-editor textarea { display: block; width: 100%; margin-top: 2px; padding: 5px 7px; border: 1px solid #c8d0c6; border-radius: 6px; font: inherit; }
#trip-editor .row { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
#trip-guests { width: 4em; }
.dlg-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.dlg-actions .primary { padding: 6px 14px; border: 0; border-radius: 6px; background: #2f6f4e; color: #fff; cursor: pointer; }
.tool-btn.danger { color: #a8322d; border-color: #e3b5b2; }
```

- [ ] **Step 6: Syntax check** — `node --check static/trips.js && node --check static/app.js && node tests/ui/filters_logic.test.js`.

- [ ] **Step 7: Playwright trips scenario** in `tests/ui/check_ui.py` (read the file first; follow its structure, helpers and canned-route style). Update the docstring: the server must be started with a throwaway DB, e.g.
  `TRIPS_DB=$(mktemp -d)/trips.db env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765`.
  At the start of the trips scenario, `GET /api/saved-trips` through the page; if it is not empty, exit with `"start the server with a throwaway TRIPS_DB"` (never delete trips you did not create). `/api/saved-trips…` routes are **not** mocked. Assert:
  1. After a Suggest-trips render (existing canned COMBO), click the pick's **💾 Save** → it shows `✓ Saved`; `#trips-count` reads `(1)`.
  2. Click **＋ Trip** in the hike list for `HIKE` → menu shows `➕ New trip` and the saved trip; clicking the saved trip shows notice `Already in “Test hike + Area house”`; the hike was **not** selected (no stay request fired by that click).
  3. Search hikes with paging so `HIKE2` is listed, ＋ Trip on it → saved trip → notice `Added to “…”`.
  4. Open **🧳 My trips** → one row with `2 hikes · 1 stay · ⭐`; open it; move `Second hike` up (↑); rename to `Renamed trip`; **Save changes** → `✓ Saved`; reopen and check order + name persisted (via `GET /api/saved-trips/{id}`).
  5. **⬇ Export .md** → `page.expect_download()` gives `renamed-trip.md` whose text starts with `# Renamed trip` and contains `## 🥾 Hikes (2)`.
  6. **🗺️ Show on map** → dialog closed, `#hike-list` has 2 items, notice `Showing trip “Renamed trip”`.
  7. **Delete** → text `Confirm delete`; click again → list empty and `#trips-empty` visible; `#trips-count` `(0)`.

- [ ] **Step 8: Run the UI check** — start the server on 8765 with a throwaway `TRIPS_DB` (background), then `env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765` → all checks pass (existing + new). Stop the server.

- [ ] **Step 9: README** — under "How it works" add:

```markdown
- **🧳 Saved trips:** **＋ Trip** on any hike or stay (list or map popup) adds it to a new or existing trip; **💾 Save** on a Trip pick saves that hike + stay as a trip. **🧳 My trips** lists them: rename, set dates/guests/dog, write notes, reorder or remove items, **Show on map**, **⬇ Export .md** (saves first, then downloads a Markdown file) and **Delete** (click twice). Trips live in a SQLite file (`data/trips.db`, change with `TRIPS_DB`; in Docker the `hick-data` volume) and keep a copy of each hike/stay as seen when added. There is one shared list and no login – anyone who can open the site can change it. Ask Claude can read them (`list_saved_trips`, `get_saved_trip`).
```
  and in the Docker section mention `docker compose down` keeps both volumes (`hick-cache`, `hick-data`). Update the UI-check command in "Tests" to the `TRIPS_DB=$(mktemp -d)/trips.db …` form.

- [ ] **Step 10: Full suite + commit**

```bash
env -u VIRTUAL_ENV uv run pytest -q
git add static/ tests/ui/check_ui.py README.md
git commit -m "feat(hick-toors): save trips from picks and results, My trips dialog, Markdown export"
```
