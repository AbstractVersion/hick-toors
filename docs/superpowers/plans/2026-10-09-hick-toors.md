# hick-toors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local website that finds hikes in Greece within reach of the days you have, shows them and nearby dog-friendly stays (Airbnb, Booking.com) on a map, ranks hike+stay combos, and offers an "Ask Claude" box powered by the local Claude Code CLI.

**Architecture:** FastAPI backend with one `Engine` that orchestrates independent sources (Wikiloc, Airbnb, Booking.com), routing (OpenRouteService) and pure logic (trip window, dog rule, ranking). The static Leaflet page and a FastMCP server both call that engine; the Ask box runs `claude -p` with the MCP server attached and resolves returned ids from a shared disk cache.

**Tech Stack:** Python 3.10+, uv, FastAPI, uvicorn, httpx, curl_cffi, pydantic v2, pydantic-settings, mcp (FastMCP), pytest + pytest-asyncio; Leaflet 1.9.4 + Leaflet.markercluster 1.5.3 (CDN).

**Spec:** `hick-toors/docs/superpowers/specs/2026-10-09-hick-toors-design.md`

## Global Constraints

- All new code lives in `/Users/abstractversion/Documents/fun-staff/hiking-mcp/hick-toors/`; never modify the repo-root `mcp-server.py`, `pyproject.toml` or `.venv`.
- Python must stay 3.10-compatible (repo `.python-version` is `3.10`): no `type X = ...`, no `ExceptionGroup`, no `asyncio.timeout()`.
- Run every command from `hick-toors/` (`cd /Users/abstractversion/Documents/fun-staff/hiking-mcp/hick-toors`); tests: `uv run pytest`.
- Tests never touch the network; sources take injectable fetch callables.
- Home default: `HOME_NAME=Athens`, `HOME_LAT=37.9838`, `HOME_LON=23.7275`.
- Drive bands: 1 day → 90 min, 2–3 days → 240 min, 4+ days → unlimited; slider override.
- Dog ON: show stay only if pets allowed or (garden and pets not explicitly refused); garden-only → badge `garden_unconfirmed` ranked after pet-friendly.
- Ranking weights /100: hike 40, stay 25, proximity 20, price 15.
- Default stay radius 15 km; default guests 2; Airbnb detail top N = 10.
- Cache TTLs: hikes 7 d, drive times 7 d, geocoding 30 d, stays 1 h, registered objects 1 d.
- Every API response carries `warnings: list[str]`; source failures never produce HTTP 500.
- Ask box: `claude -p … --mcp-config <cache>/mcp.json --strict-mcp-config --allowedTools mcp__hicktoors --tools "" --append-system-prompt … --output-format json`, 120 s timeout, one run at a time.
- No Anthropic API keys anywhere.
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; never `git add` files outside `hick-toors/`.

## Review Focus

1. End date before start date → HTTP 422 with a readable message, not a crash (tests: Task 1 models, Task 8 API).
2. Wikiloc returning non-hiking activities (diving, cycling) or routes on the Turkish coast inside the bounding box → excluded (test: Task 2).
3. Island/Crete trailhead with no road connection (ORS `null` duration) → dropped when a drive limit applies, kept with `ferry_needed=True` when unlimited (test: Task 7).
4. Localised numbers — Wikiloc `"11,81"` km, `"1.136"` m, Airbnb `"€1,202 total"` → parsed as 11.81, 1136, 1202.0 (tests: Tasks 2, 3).
5. Claude's answer without a JSON block, or with ids not in the cache → summary still shown, unknown ids skipped with a warning (test: Task 10).

---

### Task 1: Project scaffold, config, models, geo, trip window, cache

**Files:**
- Create: `hick-toors/pyproject.toml`, `hick-toors/.gitignore`, `hick-toors/.env.example`
- Create: `hick-toors/app/__init__.py`, `app/config.py`, `app/models.py`, `app/geo.py`, `app/trip_window.py`, `app/cache.py`
- Create: `hick-toors/app/sources/__init__.py`, `app/sources/stays/__init__.py`
- Test: `hick-toors/tests/__init__.py`, `tests/test_models.py`, `tests/test_geo.py`, `tests/test_trip_window.py`, `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `app.config.BASE_DIR: Path`, `app.config.Settings` (fields listed in Step 3; property `weights -> dict[str, float]` with keys `hike, stay, proximity, price`).
  - `app.models`: `LatLon`, `BBox`, `Hike`, `Stay`, `Badge`, `ScoreBreakdown`, `Combo`, `DriveLimit`, `TripDates`, `HikeQuery`, `StaySearchQuery`, `TripQuery`, `StayQuery`, `HikeSearch`, `StaySearch`, `TripSearch`.
  - `app.geo`: `haversine_km(a: LatLon, b: LatLon) -> float`, `bbox_around(center: LatLon, radius_km: float) -> BBox`, `clamp_to_greece(box: BBox) -> BBox`, `ensure_min_size(box: BBox, radius_km: float) -> BBox`, `center_of(box: BBox) -> LatLon`, `GREECE_BBOX: BBox`.
  - `app.trip_window`: `trip_days(start: date, end: date) -> int`, `max_drive_minutes(start, end, limit: DriveLimit, day_trip_max: int, weekend_max: int) -> int | None`, `stay_window(start, end) -> tuple[date, date]`.
  - `app.cache`: `DiskCache(directory: Path, clock=time.time)` with `get(key) -> Any | None`, `set(key, value, ttl_seconds: float) -> None`; constants `HOUR`, `DAY`.

- [ ] **Step 1: Create the project files**

`hick-toors/pyproject.toml`:
```toml
[project]
name = "hick-toors"
version = "0.1.0"
description = "Local hike + dog-friendly stay finder for Greece"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.28",
    "curl_cffi>=0.7",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "mcp[cli]>=1.6.0",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

`hick-toors/.gitignore`:
```
.env
.cache/
.venv/
__pycache__/
```

`hick-toors/.env.example`:
```
# Free key from https://openrouteservice.org/dev/#/signup (optional; without it drive times are estimated)
ORS_API_KEY=
HOME_NAME=Athens
HOME_LAT=37.9838
HOME_LON=23.7275
DAY_TRIP_MAX_MINUTES=90
WEEKEND_MAX_MINUTES=240
STAY_RADIUS_KM=15
DEFAULT_GUESTS=2
AIRBNB_DETAIL_TOP_N=10
```

Create empty files: `app/__init__.py`, `app/sources/__init__.py`, `app/sources/stays/__init__.py`, `tests/__init__.py`.

Run: `cd /Users/abstractversion/Documents/fun-staff/hiking-mcp/hick-toors && uv sync`
Expected: creates `hick-toors/.venv` and `uv.lock`, no errors.

- [ ] **Step 2: Write the failing tests**

`tests/test_models.py`:
```python
from datetime import date

import pytest
from pydantic import ValidationError

from app.models import HikeQuery, StaySearchQuery, TripQuery


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="end date must be on or after start date"):
        TripQuery(start=date(2026, 10, 25), end=date(2026, 10, 23))


def test_same_day_is_allowed():
    q = TripQuery(start=date(2026, 10, 23), end=date(2026, 10, 23))
    assert q.drive_limit == "auto"


def test_place_mode_requires_place():
    with pytest.raises(ValidationError, match="place is required"):
        HikeQuery(start=date(2026, 10, 23), end=date(2026, 10, 25), mode="place")


def test_drive_limit_accepts_minutes_and_keywords():
    base = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))
    assert HikeQuery(**base, drive_limit=120).drive_limit == 120
    assert HikeQuery(**base, drive_limit="unlimited").drive_limit == "unlimited"
    with pytest.raises(ValidationError):
        HikeQuery(**base, drive_limit=0)


def test_stay_search_needs_hike_or_coordinates():
    base = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))
    with pytest.raises(ValidationError, match="hike_id or lat/lon"):
        StaySearchQuery(**base)
    assert StaySearchQuery(**base, lat=39.4, lon=23.0).dog is True
    assert StaySearchQuery(**base, hike_id="wikiloc:1").hike_id == "wikiloc:1"
```

`tests/test_geo.py`:
```python
import pytest

from app.geo import GREECE_BBOX, bbox_around, center_of, clamp_to_greece, ensure_min_size, haversine_km
from app.models import BBox, LatLon

ATHENS = LatLon(lat=37.9838, lon=23.7275)
VOLOS = LatLon(lat=39.3622, lon=22.9420)


def test_haversine_athens_volos():
    assert haversine_km(ATHENS, VOLOS) == pytest.approx(170, abs=10)


def test_bbox_around_contains_radius():
    box = bbox_around(ATHENS, 50)
    assert box.south < ATHENS.lat < box.north
    assert box.west < ATHENS.lon < box.east
    assert haversine_km(ATHENS, LatLon(lat=box.north, lon=ATHENS.lon)) == pytest.approx(50, abs=1)
    assert haversine_km(ATHENS, LatLon(lat=ATHENS.lat, lon=box.east)) == pytest.approx(50, abs=1)


def test_clamp_to_greece():
    huge = bbox_around(ATHENS, 2000)
    assert clamp_to_greece(huge) == GREECE_BBOX


def test_ensure_min_size_expands_tiny_box():
    tiny = BBox(south=39.39, west=22.99, north=39.40, east=23.00)
    out = ensure_min_size(tiny, 15)
    c = center_of(tiny)
    assert haversine_km(c, LatLon(lat=out.north, lon=c.lon)) >= 14.5


def test_ensure_min_size_keeps_big_box():
    big = bbox_around(ATHENS, 100)
    assert ensure_min_size(big, 15) == big
```

`tests/test_trip_window.py`:
```python
from datetime import date

import pytest

from app.trip_window import max_drive_minutes, stay_window, trip_days

FRI = date(2026, 10, 23)


def d(day):
    return date(2026, 10, day)


def test_trip_days_inclusive():
    assert trip_days(FRI, FRI) == 1
    assert trip_days(FRI, d(25)) == 3


@pytest.mark.parametrize(
    "end,expected",
    [(23, 90), (24, 240), (25, 240), (26, None), (30, None)],
)
def test_auto_bands(end, expected):
    assert max_drive_minutes(FRI, d(end), "auto", 90, 240) == expected


def test_override_and_unlimited():
    assert max_drive_minutes(FRI, d(25), 60, 90, 240) == 60
    assert max_drive_minutes(FRI, FRI, "unlimited", 90, 240) is None


def test_stay_window_gives_at_least_one_night():
    assert stay_window(FRI, FRI) == (FRI, d(24))
    assert stay_window(FRI, d(25)) == (FRI, d(25))
```

`tests/test_cache.py`:
```python
from app.cache import DiskCache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_set_get_roundtrip(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("k", {"a": [1, 2]}, ttl_seconds=60)
    assert cache.get("k") == {"a": [1, 2]}


def test_missing_key_returns_none(tmp_path):
    assert DiskCache(tmp_path).get("nope") is None


def test_expired_entry_returns_none(tmp_path):
    clock = Clock()
    cache = DiskCache(tmp_path, clock=clock)
    cache.set("k", 1, ttl_seconds=10)
    clock.now += 11
    assert cache.get("k") is None


def test_corrupt_file_returns_none(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("k", 1, ttl_seconds=10)
    for f in tmp_path.iterdir():
        f.write_text("{not json")
    assert cache.get("k") is None


def test_creates_directory(tmp_path):
    DiskCache(tmp_path / "nested" / "dir").set("k", 1, 10)
    assert (tmp_path / "nested" / "dir").is_dir()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'app.models'` (and similar).

- [ ] **Step 4: Implement**

`app/config.py`:
```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    ors_api_key: str | None = None
    home_name: str = "Athens"
    home_lat: float = 37.9838
    home_lon: float = 23.7275
    day_trip_max_minutes: int = 90
    weekend_max_minutes: int = 240
    stay_radius_km: float = 15.0
    default_guests: int = 2
    airbnb_detail_top_n: int = 10
    hike_pages: int = 2
    max_hikes: int = 40
    trip_top_hikes: int = 5
    trip_stays_per_hike: int = 3
    trip_max_combos: int = 10
    weight_hike: float = 40
    weight_stay: float = 25
    weight_proximity: float = 20
    weight_price: float = 15
    cache_dir: Path = BASE_DIR / ".cache"
    claude_bin: str = "claude"
    ask_timeout_seconds: int = 120

    @property
    def weights(self) -> dict[str, float]:
        return {
            "hike": self.weight_hike,
            "stay": self.weight_stay,
            "proximity": self.weight_proximity,
            "price": self.weight_price,
        }
```

`app/models.py`:
```python
from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

Badge = Literal["pets", "pets_garden", "garden", "garden_unconfirmed"]
DriveLimit = Union[Annotated[int, Field(gt=0)], Literal["auto", "unlimited"]]


class LatLon(BaseModel):
    lat: float
    lon: float


class BBox(BaseModel):
    south: float
    west: float
    north: float
    east: float


class Hike(BaseModel):
    id: str
    name: str
    url: str
    lat: float
    lon: float
    distance_km: float | None = None
    elevation_gain_m: int | None = None
    difficulty: str | None = None
    trailrank: int | None = None
    near: str | None = None
    thumbnail: str | None = None
    drive_minutes: int | None = None
    drive_estimated: bool = False
    ferry_needed: bool = False


class Stay(BaseModel):
    id: str
    source: Literal["airbnb", "booking"]
    name: str
    url: str
    lat: float
    lon: float
    total_price: float | None = None
    currency: str | None = None
    rating: float | None = None  # 0-5 scale
    photo: str | None = None
    pets_allowed: bool | None = None
    has_garden: bool | None = None
    badge: Badge | None = None
    distance_km: float | None = None


class ScoreBreakdown(BaseModel):
    hike: float
    stay: float
    proximity: float
    price: float


class Combo(BaseModel):
    hike: Hike
    stay: Stay
    score: float
    breakdown: ScoreBreakdown


class TripDates(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end < self.start:
            raise ValueError("end date must be on or after start date")
        return self


class HikeQuery(TripDates):
    mode: Literal["place", "near_home"] = "near_home"
    place: str | None = None
    drive_limit: DriveLimit = "auto"

    @model_validator(mode="after")
    def _place_required(self):
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        return self


class StaySearchQuery(TripDates):
    hike_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = Field(default=None, gt=0, le=100)
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True

    @model_validator(mode="after")
    def _needs_center(self):
        if not self.hike_id and (self.lat is None or self.lon is None):
            raise ValueError("hike_id or lat/lon is required")
        return self


class TripQuery(TripDates):
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True


class StayQuery(BaseModel):
    """What a single stay provider is asked for."""

    lat: float
    lon: float
    radius_km: float
    check_in: date
    check_out: date
    guests: int
    dog: bool


class HikeSearch(BaseModel):
    hikes: list[Hike] = []
    warnings: list[str] = []
    drive_limit_minutes: int | None = None


class StaySearch(BaseModel):
    stays: list[Stay] = []
    warnings: list[str] = []
    center: LatLon | None = None


class TripSearch(BaseModel):
    combos: list[Combo] = []
    warnings: list[str] = []
```

`app/geo.py`:
```python
import math

from app.models import BBox, LatLon

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT = 111.32
GREECE_BBOX = BBox(south=34.7, west=19.3, north=41.8, east=28.3)


def haversine_km(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bbox_around(center: LatLon, radius_km: float) -> BBox:
    dlat = radius_km / KM_PER_DEG_LAT
    dlon = radius_km / (KM_PER_DEG_LAT * math.cos(math.radians(center.lat)))
    return BBox(
        south=center.lat - dlat,
        west=center.lon - dlon,
        north=center.lat + dlat,
        east=center.lon + dlon,
    )


def clamp_to_greece(box: BBox) -> BBox:
    g = GREECE_BBOX
    return BBox(
        south=max(box.south, g.south),
        west=max(box.west, g.west),
        north=min(box.north, g.north),
        east=min(box.east, g.east),
    )


def center_of(box: BBox) -> LatLon:
    return LatLon(lat=(box.south + box.north) / 2, lon=(box.west + box.east) / 2)


def ensure_min_size(box: BBox, radius_km: float) -> BBox:
    m = bbox_around(center_of(box), radius_km)
    return BBox(
        south=min(box.south, m.south),
        west=min(box.west, m.west),
        north=max(box.north, m.north),
        east=max(box.east, m.east),
    )
```

`app/trip_window.py`:
```python
from datetime import date, timedelta

from app.models import DriveLimit


def trip_days(start: date, end: date) -> int:
    return (end - start).days + 1


def max_drive_minutes(
    start: date, end: date, limit: DriveLimit, day_trip_max: int, weekend_max: int
) -> int | None:
    """Max driving minutes from home, or None for unlimited."""
    if limit == "unlimited":
        return None
    if isinstance(limit, int):
        return limit
    days = trip_days(start, end)
    if days <= 1:
        return day_trip_max
    if days <= 3:
        return weekend_max
    return None


def stay_window(start: date, end: date) -> tuple[date, date]:
    """Check-in/check-out; a same-day trip still books one night."""
    return start, max(end, start + timedelta(days=1))
```

`app/cache.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all tests in the 4 files PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example app tests
git commit -m "feat(hick-toors): scaffold project with config, models, geo, trip window, cache

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Wikiloc source

**Files:**
- Create: `hick-toors/app/sources/wikiloc.py`, `hick-toors/tests/builders.py`
- Test: `hick-toors/tests/test_wikiloc.py`

**Interfaces:**
- Consumes: `app.models.BBox`, `app.models.Hike`.
- Produces:
  - `app.sources.wikiloc.parse_search(data: dict) -> list[Hike]`
  - `app.sources.wikiloc.make_fetch(transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep, retries: int = 4) -> Fetch` where `Fetch = Callable[[str, dict], Awaitable[dict | None]]`
  - `app.sources.wikiloc.WikilocSource(fetch: Fetch | None = None, pages: int = 2)` with `async search(bbox: BBox, query: str = "") -> list[Hike]`
  - `app.sources.wikiloc.WikilocUnavailable(Exception)`
  - `tests.builders.wikiloc_spa(...)` (fixture builder; Tasks 3/4 add more builders to the same file).

Real response shape (probed 2026-10-09, `GET https://es.wikiloc.com/wikiloc/find.do?event=map&to=25&sw=lat,lon&ne=lat,lon&q=&page=1`): top-level `spas` list; each item has `id`, `prettyURL`, `name`, `picto` (1 = hiking), `near` (e.g. `"Miléai, Θεσσαλία (Ελλάδα)"` or `"Portariá, Thessaly (Greece)"`), `trailrank` (int), `distance` (`"11,81"`), `slope` (`"425"`), `skill` (1–5), `lat`, `lon`, `thumbs[].url`. Plain curl gets a Cloudflare page; httpx with `User-Agent: wikiloc-app/1.0` works but is intermittently answered 403.

- [ ] **Step 1: Write the fixture builder**

`tests/builders.py`:
```python
"""Builders that mimic the real payload shapes of the scraped sites (probed 2026-10-09)."""


def wikiloc_spa(
    id,
    name,
    lat,
    lon,
    near="Portariá, Thessaly (Greece)",
    picto=1,
    distance="9,59",
    slope="563",
    skill=2,
    trailrank=80,
):
    return {
        "id": id,
        "prettyURL": f"/rutas-senderismo/route-{id}",
        "name": name,
        "picto": picto,
        "pictoText": "Senderismo" if picto == 1 else "Otro",
        "near": near,
        "trailrank": trailrank,
        "distance": distance,
        "slope": slope,
        "skill": skill,
        "lat": lat,
        "lon": lon,
        "author": "someone",
        "thumbs": [{"url": f"https://s0.wklcdn.com/{id}_tn.jpg", "id": 1}],
    }
```

- [ ] **Step 2: Write the failing tests**

`tests/test_wikiloc.py`:
```python
import httpx
import pytest

from app.models import BBox
from app.sources.wikiloc import WikilocSource, WikilocUnavailable, make_fetch, parse_search
from tests.builders import wikiloc_spa

BOX = BBox(south=39.3, west=22.9, north=39.5, east=23.2)


def test_parse_search_maps_fields():
    data = {"spas": [wikiloc_spa(133095679, "Portaria - Makrinitsa", 39.39, 22.99,
                                 distance="11,81", slope="1.136", skill=3, trailrank=89)]}
    [hike] = parse_search(data)
    assert hike.id == "wikiloc:133095679"
    assert hike.url == "https://es.wikiloc.com/rutas-senderismo/route-133095679"
    assert hike.distance_km == 11.81
    assert hike.elevation_gain_m == 1136
    assert hike.difficulty == "Hard"
    assert hike.trailrank == 89
    assert (hike.lat, hike.lon) == (39.39, 22.99)
    assert hike.thumbnail == "https://s0.wklcdn.com/133095679_tn.jpg"


def test_parse_search_keeps_only_greek_hiking_routes():
    data = {"spas": [
        wikiloc_spa(1, "Greek script", 39.3, 22.9, near="Miléai, Θεσσαλία (Ελλάδα)"),
        wikiloc_spa(2, "English", 39.3, 22.9, near="Portariá, Thessaly (Greece)"),
        wikiloc_spa(3, "Spanish", 39.3, 22.9, near="Volos, Tesalia (Grecia)"),
        wikiloc_spa(4, "Diving", 39.3, 22.9, picto=41),
        wikiloc_spa(5, "Turkish coast", 39.3, 26.7, near="Ayvalık, Balıkesir (Türkiye)"),
    ]}
    assert [h.id for h in parse_search(data)] == ["wikiloc:1", "wikiloc:2", "wikiloc:3"]


def test_parse_search_skips_routes_without_coordinates():
    spa = wikiloc_spa(1, "No coords", None, None)
    assert parse_search({"spas": [spa]}) == []


def test_parse_search_handles_missing_spas():
    assert parse_search({}) == []


async def test_search_pages_and_dedupes():
    pages = {
        1: {"spas": [wikiloc_spa(i, f"r{i}", 39.3, 22.9) for i in range(25)]},
        2: {"spas": [wikiloc_spa(24, "dup", 39.3, 22.9), wikiloc_spa(99, "r99", 39.3, 22.9)]},
    }
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return pages[params["page"]]

    hikes = await WikilocSource(fetch=fetch, pages=3).search(BOX)
    assert len(hikes) == 26
    assert calls[0]["sw"] == "39.3,22.9" and calls[0]["ne"] == "39.5,23.2"
    assert len(calls) == 2  # page 2 had < 25 results, so no page 3


async def test_search_raises_when_first_page_fails():
    async def fetch(url, params):
        return None

    with pytest.raises(WikilocUnavailable, match="Hikes temporarily unavailable"):
        await WikilocSource(fetch=fetch).search(BOX)


async def test_search_returns_partial_when_later_page_fails():
    async def fetch(url, params):
        if params["page"] == 1:
            return {"spas": [wikiloc_spa(i, f"r{i}", 39.3, 22.9) for i in range(25)]}
        return None

    assert len(await WikilocSource(fetch=fetch, pages=2).search(BOX)) == 25


async def test_make_fetch_retries_403_then_succeeds():
    responses = iter([httpx.Response(403), httpx.Response(200, json={"spas": []})])
    transport = httpx.MockTransport(lambda request: next(responses))
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    fetch = make_fetch(transport=transport, sleep=sleep)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}) == {"spas": []}
    assert len(sleeps) == 1


async def test_make_fetch_gives_up_after_retries():
    transport = httpx.MockTransport(lambda request: httpx.Response(403))

    async def sleep(seconds):
        pass

    fetch = make_fetch(transport=transport, sleep=sleep, retries=2)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_wikiloc.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.sources.wikiloc'`.

- [ ] **Step 4: Implement**

`app/sources/wikiloc.py`:
```python
import asyncio
import sys
from typing import Any, Awaitable, Callable

import httpx

from app.models import BBox, Hike

SEARCH_URL = "https://es.wikiloc.com/wikiloc/find.do"
BASE_URL = "https://es.wikiloc.com"
USER_AGENT = "wikiloc-app/1.0"
PAGE_SIZE = 25
HIKING_PICTO = 1
GREECE_MARKERS = ("Greece", "Ελλάδα", "Grecia")
SKILL = {1: "Easy", 2: "Moderate", 3: "Hard", 4: "Very Hard", 5: "Experts Only"}

Fetch = Callable[[str, dict], Awaitable[Any]]


class WikilocUnavailable(Exception):
    pass


def _num(value) -> float | None:
    """Parse Wikiloc's Spanish-locale numbers: '11,81' -> 11.81, '1.136' -> 1136."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _in_greece(near: str | None) -> bool:
    return not near or any(marker in near for marker in GREECE_MARKERS)


def parse_search(data: dict) -> list[Hike]:
    hikes = []
    for spa in data.get("spas") or []:
        if spa.get("picto") != HIKING_PICTO or not _in_greece(spa.get("near")):
            continue
        if spa.get("lat") is None or spa.get("lon") is None:
            continue
        slope = _num(spa.get("slope"))
        thumbs = spa.get("thumbs") or []
        hikes.append(
            Hike(
                id=f"wikiloc:{spa['id']}",
                name=spa.get("name") or "Unnamed route",
                url=f"{BASE_URL}{spa.get('prettyURL', '')}",
                lat=spa["lat"],
                lon=spa["lon"],
                distance_km=_num(spa.get("distance")),
                elevation_gain_m=int(slope) if slope is not None else None,
                difficulty=SKILL.get(spa.get("skill")),
                trailrank=spa.get("trailrank"),
                near=spa.get("near"),
                thumbnail=thumbs[0].get("url") if thumbs else None,
            )
        )
    return hikes


def make_fetch(
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retries: int = 4,
) -> Fetch:
    """Build a JSON fetcher that retries Wikiloc's intermittent 403s with backoff."""

    async def fetch(url: str, params: dict) -> Any:
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, params=params, timeout=30.0)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    print(f"wikiloc request failed: {exc}", file=sys.stderr)
                    if attempt < retries:
                        await sleep(1.5 * (attempt + 1))
        return None

    return fetch


class WikilocSource:
    def __init__(self, fetch: Fetch | None = None, pages: int = 2):
        self._fetch = fetch or make_fetch()
        self._pages = pages

    async def search(self, bbox: BBox, query: str = "") -> list[Hike]:
        hikes: list[Hike] = []
        seen: set[str] = set()
        for page in range(1, self._pages + 1):
            params = {
                "event": "map",
                "to": PAGE_SIZE,
                "sw": f"{bbox.south},{bbox.west}",
                "ne": f"{bbox.north},{bbox.east}",
                "q": query,
                "page": page,
            }
            data = await self._fetch(SEARCH_URL, params)
            if not isinstance(data, dict):
                if page == 1:
                    raise WikilocUnavailable(
                        "Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute"
                    )
                break
            for hike in parse_search(data):
                if hike.id not in seen:
                    seen.add(hike.id)
                    hikes.append(hike)
            if len(data.get("spas") or []) < PAGE_SIZE:
                break
        return hikes
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_wikiloc.py -v`
Expected: 9 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/sources/wikiloc.py tests/builders.py tests/test_wikiloc.py
git commit -m "feat(hick-toors): add Wikiloc hike source with Greece/hiking filter and retries

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Stay provider interface + Airbnb provider

**Files:**
- Create: `hick-toors/app/sources/stays/base.py`, `hick-toors/app/sources/stays/airbnb.py`
- Modify: `hick-toors/tests/builders.py` (append Airbnb builders)
- Test: `hick-toors/tests/test_airbnb.py`

**Interfaces:**
- Consumes: `app.models.Stay`, `app.models.StayQuery`, `app.geo.bbox_around`, `app.models.LatLon`.
- Produces:
  - `app.sources.stays.base.StayProvider` (Protocol: attribute `name: str`, `async search(q: StayQuery) -> list[Stay]`)
  - `app.sources.stays.base.ProviderUnavailable(Exception)` — message is shown to the user.
  - `app.sources.stays.airbnb.parse_search(html: str) -> list[Stay]`, `parse_details(html: str) -> tuple[bool | None, bool]` (pets_allowed, has_garden), `AirbnbParseError`, `AirbnbProvider(fetch: FetchText | None = None, detail_top_n: int = 10, concurrency: int = 3)` with `name = "Airbnb"`.
  - `FetchText = Callable[[str, dict], Awaitable[str]]`.

Real shape (probed 2026-10-09): `GET https://www.airbnb.com/s/homes?ne_lat&ne_lng&sw_lat&sw_lng&search_by_map=true&search_type=user_map_move&checkin&checkout&adults[&pets=1]` with a browser User-Agent returns HTML containing `<script id="data-deferred-state-0" type="application/json">`; results at `niobeClientData[0][1].data.presentation.staysSearch.results.searchResults[]`, each with `demandStayListing.id` (base64 of `"DemandStayListing:<numeric id>"`), `demandStayListing.location.coordinate.{latitude,longitude}`, `nameLocalized.localizedStringWithTranslationPreference`, `title`, `subtitle`, `avgRatingA11yLabel` (`"4.85 out of 5 average rating,  26 reviews"`), `structuredDisplayPrice.primaryLine.accessibilityLabel` (`"€202 total"`), `contextualPictures[].picture`. Listing page `https://www.airbnb.com/rooms/<id>` contains JSON strings like `"title":"Private backyard – Fully fenced"`, `"title":"Backyard"`, `"title":"Pets allowed"`.

- [ ] **Step 1: Append Airbnb builders to `tests/builders.py`**

Add at the top of the file: `import base64` and `import json`. Append:
```python
def airbnb_result(listing_id, title, lat, lon, label="€202 total",
                  rating="4.85 out of 5 average rating,  26 reviews"):
    encoded = base64.b64encode(f"DemandStayListing:{listing_id}".encode()).decode()
    return {
        "__typename": "StaySearchResult",
        "avgRatingA11yLabel": rating,
        "title": "Cottage in Makrinitsa",
        "subtitle": title,
        "nameLocalized": {"localizedStringWithTranslationPreference": title},
        "structuredDisplayPrice": {"primaryLine": {"accessibilityLabel": label, "price": label.split()[0]}},
        "contextualPictures": [{"picture": f"https://a0.muscache.com/{listing_id}.jpg"}],
        "demandStayListing": {
            "id": encoded,
            "location": {"coordinate": {"latitude": lat, "longitude": lon}},
        },
    }


def airbnb_search_html(results):
    state = {"niobeClientData": [["StaysSearch:{}", {"data": {"presentation": {
        "staysSearch": {"results": {"searchResults": results}}}}}]]}
    return ('<html><body><script id="data-deferred-state-0" type="application/json">'
            f"{json.dumps(state)}</script></body></html>")


def airbnb_listing_html(titles):
    blob = json.dumps({"sections": [{"title": t} for t in titles]},
                      ensure_ascii=False, separators=(",", ":"))
    return f"<html><script>{blob}</script></html>"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_airbnb.py`:
```python
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import StayQuery
from app.sources.stays.airbnb import AirbnbParseError, AirbnbProvider, parse_details, parse_search
from app.sources.stays.base import ProviderUnavailable
from tests.builders import airbnb_listing_html, airbnb_result, airbnb_search_html

Q = StayQuery(lat=39.40, lon=23.03, radius_km=15, check_in=date(2026, 10, 23),
              check_out=date(2026, 10, 25), guests=2, dog=True)


def test_parse_search_maps_fields():
    html = airbnb_search_html([airbnb_result(898234413854573103, "Rustic Stone House", 39.4016, 22.985,
                                             label="€1,202 total")])
    [stay] = parse_search(html)
    assert stay.id == "airbnb:898234413854573103"
    assert stay.source == "airbnb"
    assert stay.name == "Rustic Stone House"
    assert stay.url == "https://www.airbnb.com/rooms/898234413854573103"
    assert (stay.lat, stay.lon) == (39.4016, 22.985)
    assert stay.total_price == 1202.0
    assert stay.currency == "EUR"
    assert stay.rating == 4.85
    assert stay.photo == "https://a0.muscache.com/898234413854573103.jpg"


def test_parse_search_takes_discounted_price_and_handles_new_listing():
    html = airbnb_search_html([airbnb_result(1, "A", 39.4, 23.0, label="originally €250, now €202 total",
                                             rating="New place to stay")])
    [stay] = parse_search(html)
    assert stay.total_price == 202.0
    assert stay.rating is None


def test_parse_search_raises_without_state_script():
    with pytest.raises(AirbnbParseError):
        parse_search("<html>captcha</html>")


@pytest.mark.parametrize("titles,expected", [
    (["Private backyard – Fully fenced", "Pets allowed"], (True, True)),
    (["Garden view", "Wifi"], (None, True)),
    (["Backyard", "No pets"], (False, True)),
    (["Wifi", "Pet-friendly vacation rentals in Greece"], (None, False)),
])
def test_parse_details(titles, expected):
    assert parse_details(airbnb_listing_html(titles)) == expected


class FakeFetch:
    def __init__(self, search_with_pets, search_without, details):
        self.search_with_pets = search_with_pets
        self.search_without = search_without
        self.details = details
        self.calls = []

    async def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if url.endswith("/s/homes"):
            return self.search_with_pets if params.get("pets") == 1 else self.search_without
        listing_id = int(url.rsplit("/", 1)[1])
        return self.details[listing_id]


async def test_dog_search_marks_pets_and_finds_garden_only_listings():
    fetch = FakeFetch(
        search_with_pets=airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)]),
        search_without=airbnb_search_html([
            airbnb_result(1, "Pet house", 39.4, 23.0),
            airbnb_result(2, "Garden house", 39.41, 23.01),
            airbnb_result(3, "Plain flat", 39.42, 23.02),
        ]),
        details={
            1: airbnb_listing_html(["Backyard", "Pets allowed"]),
            2: airbnb_listing_html(["Garden"]),
            3: airbnb_listing_html(["Wifi"]),
        },
    )
    stays = await AirbnbProvider(fetch=fetch, detail_top_n=10).search(Q)
    by_id = {s.id: s for s in stays}
    assert [s.id for s in stays] == ["airbnb:1", "airbnb:2", "airbnb:3"]
    assert (by_id["airbnb:1"].pets_allowed, by_id["airbnb:1"].has_garden) == (True, True)
    assert (by_id["airbnb:2"].pets_allowed, by_id["airbnb:2"].has_garden) == (None, True)
    assert (by_id["airbnb:3"].pets_allowed, by_id["airbnb:3"].has_garden) == (None, False)
    query = parse_qs(urlparse(by_id["airbnb:1"].url).query)
    assert query["check_in"] == ["2026-10-23"] and query["pets"] == ["1"]
    search_params = fetch.calls[0][1]
    assert search_params["checkin"] == "2026-10-23" and search_params["adults"] == 2
    assert search_params["sw_lat"] < 39.40 < search_params["ne_lat"]


async def test_no_dog_search_is_single_request_without_details():
    fetch = FakeFetch(search_with_pets="", search_without=airbnb_search_html(
        [airbnb_result(5, "Flat", 39.4, 23.0)]), details={})
    stays = await AirbnbProvider(fetch=fetch).search(Q.model_copy(update={"dog": False}))
    assert [s.id for s in stays] == ["airbnb:5"]
    assert len(fetch.calls) == 1 and "pets" not in fetch.calls[0][1]


async def test_detail_failure_is_ignored():
    class Failing(FakeFetch):
        async def __call__(self, url, params):
            if "/rooms/" in url:
                raise RuntimeError("blocked")
            return await super().__call__(url, params)

    fetch = Failing(airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)]),
                    airbnb_search_html([]), {})
    [stay] = await AirbnbProvider(fetch=fetch).search(Q)
    assert stay.pets_allowed is True and stay.has_garden is None


async def test_blocked_search_raises_provider_unavailable():
    async def fetch(url, params):
        return "<html>captcha</html>"

    with pytest.raises(ProviderUnavailable, match="Airbnb unavailable"):
        await AirbnbProvider(fetch=fetch).search(Q)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_airbnb.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.sources.stays.airbnb'`.

- [ ] **Step 4: Implement**

`app/sources/stays/base.py`:
```python
from typing import Protocol

from app.models import Stay, StayQuery


class ProviderUnavailable(Exception):
    """A stay source is blocked or returned something unparsable; the message is shown to the user."""


class StayProvider(Protocol):
    name: str

    async def search(self, q: StayQuery) -> list[Stay]: ...
```

`app/sources/stays/airbnb.py`:
```python
import asyncio
import base64
import binascii
import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from app.geo import bbox_around
from app.models import LatLon, Stay, StayQuery
from app.sources.stays.base import ProviderUnavailable

SEARCH_URL = "https://www.airbnb.com/s/homes"
ROOM_URL = "https://www.airbnb.com/rooms/{id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
STATE_RE = re.compile(r'<script id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S)
TITLE_RE = re.compile(r'"title":"([^"]*)"')
GARDEN_RE = re.compile(r"garden|backyard|\byard\b", re.I)
PRICE_RE = re.compile(r"([€$£])\s?([\d.,]+)")
RATING_RE = re.compile(r"^\s*([\d.]+) out of 5")
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}

FetchText = Callable[[str, dict], Awaitable[str]]


class AirbnbParseError(Exception):
    pass


def _walk_results(node: Any) -> list[dict]:
    """Find every search result dict (has 'demandStayListing') wherever Airbnb nests it."""
    found = []
    if isinstance(node, dict):
        if "demandStayListing" in node:
            return [node]
        for value in node.values():
            found.extend(_walk_results(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_results(value))
    return found


def _listing_id(encoded: str) -> str:
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.b64decode(padded).decode().split(":")[-1]
    except (binascii.Error, UnicodeDecodeError):
        return encoded


def _price(result: dict) -> tuple[float | None, str | None]:
    line = ((result.get("structuredDisplayPrice") or {}).get("primaryLine") or {})
    text = line.get("accessibilityLabel") or line.get("price") or ""
    matches = PRICE_RE.findall(text)
    if not matches:
        return None, None
    symbol, number = matches[-1]
    try:
        return float(number.replace(",", "")), CURRENCIES.get(symbol)
    except ValueError:
        return None, None


def _rating(label: str | None) -> float | None:
    match = RATING_RE.match(label or "")
    return float(match.group(1)) if match else None


def parse_search(html: str) -> list[Stay]:
    match = STATE_RE.search(html)
    if not match:
        raise AirbnbParseError("no data-deferred-state script")
    try:
        state = json.loads(match.group(1))
    except ValueError as exc:
        raise AirbnbParseError("state script is not JSON") from exc
    stays = []
    for result in _walk_results(state):
        listing = result["demandStayListing"]
        coord = ((listing.get("location") or {}).get("coordinate") or {})
        if coord.get("latitude") is None or coord.get("longitude") is None or not listing.get("id"):
            continue
        listing_id = _listing_id(listing["id"])
        name = ((result.get("nameLocalized") or {}).get("localizedStringWithTranslationPreference")
                or result.get("subtitle") or result.get("title") or "Airbnb stay")
        price, currency = _price(result)
        pictures = result.get("contextualPictures") or []
        stays.append(Stay(
            id=f"airbnb:{listing_id}",
            source="airbnb",
            name=name,
            url=ROOM_URL.format(id=listing_id),
            lat=coord["latitude"],
            lon=coord["longitude"],
            total_price=price,
            currency=currency,
            rating=_rating(result.get("avgRatingA11yLabel")),
            photo=pictures[0].get("picture") if pictures else None,
        ))
    return stays


def parse_details(html: str) -> tuple[bool | None, bool]:
    """(pets_allowed, has_garden) from a listing page's amenity/house-rule titles."""
    titles = [t.strip() for t in TITLE_RE.findall(html)]
    has_garden = any(GARDEN_RE.search(t) for t in titles)
    lowered = {t.lower() for t in titles}
    if "pets allowed" in lowered:
        pets: bool | None = True
    elif lowered & {"no pets", "pets not allowed"}:
        pets = False
    else:
        pets = None
    return pets, has_garden


async def fetch_text(url: str, params: dict) -> str:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        return response.text


class AirbnbProvider:
    name = "Airbnb"

    def __init__(self, fetch: FetchText | None = None, detail_top_n: int = 10, concurrency: int = 3):
        self._fetch = fetch or fetch_text
        self._top_n = detail_top_n
        self._concurrency = concurrency

    def _search_params(self, q: StayQuery) -> dict:
        box = bbox_around(LatLon(lat=q.lat, lon=q.lon), q.radius_km)
        return {
            "ne_lat": box.north, "ne_lng": box.east, "sw_lat": box.south, "sw_lng": box.west,
            "search_by_map": "true", "search_type": "user_map_move",
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "adults": q.guests, "currency": "EUR",
        }

    async def _search(self, params: dict) -> list[Stay]:
        try:
            return parse_search(await self._fetch(SEARCH_URL, params))
        except (httpx.HTTPError, AirbnbParseError, ValueError) as exc:
            raise ProviderUnavailable("Airbnb unavailable – showing other sources") from exc

    async def _enrich(self, stays: list[Stay]) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(stay: Stay) -> None:
            async with semaphore:
                try:
                    html = await self._fetch(stay.url, {})
                except Exception:
                    return
            pets, garden = parse_details(html)
            stay.has_garden = garden
            if stay.pets_allowed is None:
                stay.pets_allowed = pets

        await asyncio.gather(*(one(s) for s in stays))

    async def search(self, q: StayQuery) -> list[Stay]:
        base = self._search_params(q)
        if not q.dog:
            stays = await self._search(base)
        else:
            pet_stays = await self._search({**base, "pets": 1})
            for stay in pet_stays:
                stay.pets_allowed = True
            pet_ids = {s.id for s in pet_stays}
            others = [s for s in await self._search(base) if s.id not in pet_ids]
            await self._enrich(pet_stays[: self._top_n] + others[: self._top_n])
            stays = pet_stays + others
        link = {"check_in": q.check_in.isoformat(), "check_out": q.check_out.isoformat(), "adults": q.guests}
        if q.dog:
            link["pets"] = 1
        for stay in stays:
            stay.url = f"{stay.url}?{urlencode(link)}"
        return stays
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_airbnb.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/sources/stays/base.py app/sources/stays/airbnb.py tests/builders.py tests/test_airbnb.py
git commit -m "feat(hick-toors): add stay provider interface and Airbnb provider with pets/garden detection

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Booking.com provider

**Files:**
- Create: `hick-toors/app/sources/stays/booking.py`
- Modify: `hick-toors/tests/builders.py` (append Booking builders)
- Test: `hick-toors/tests/test_booking.py`

**Interfaces:**
- Consumes: `app.models.Stay`, `app.models.StayQuery`, `app.sources.stays.base.ProviderUnavailable`.
- Produces: `app.sources.stays.booking.parse_search(html: str) -> list[Stay]`, `BookingBlocked(Exception)`, `BookingProvider(fetch: FetchStatus | None = None, retries: int = 3, sleep=asyncio.sleep)` with `name = "Booking.com"`; `FetchStatus = Callable[[str, dict], Awaitable[tuple[int, str]]]`.

Real shape (probed 2026-10-09): `GET https://www.booking.com/searchresults.html?latitude&longitude&radius&checkin&checkout&group_adults&no_rooms=1&group_children=0&nflt=hotelfacility%3D4&lang=en-us&selected_currency=EUR` (`hotelfacility=4` = "Pets allowed"). A good page has `<script type="application/json" …>` blobs containing `searchQueries` and result dicts with `displayName.text`, `basicPropertyData.{id, pageName, location.{latitude, longitude, countryCode}, reviews.totalScore (0–10), photos.main.highResUrl.relativeUrl}`, `priceDisplayInfoIrene.displayPrice.amountPerStay.{amountUnformatted, currency}`. Photos live under `https://cf.bstatic.com`. httpx gets HTTP 202 challenge pages; curl_cffi with `impersonate="chrome"` is the best option but is still intermittently blocked (HTTP 202, or 200 without `searchQueries`).

- [ ] **Step 1: Append Booking builders to `tests/builders.py`**

```python
def booking_property(pid, name, lat, lon, score=9.6, amount=354.0, page="anna-villa"):
    return {
        "__typename": "SearchResultProperty",
        "displayName": {"text": name},
        "basicPropertyData": {
            "id": pid,
            "pageName": page,
            "location": {"latitude": lat, "longitude": lon, "countryCode": "gr"},
            "reviews": {"totalScore": score},
            "photos": {"main": {"highResUrl": {"relativeUrl": f"/xdata/images/hotel/square600/{pid}.webp"}}},
        },
        "priceDisplayInfoIrene": {"displayPrice": {"amountPerStay": {"amountUnformatted": amount, "currency": "EUR"}}},
    }


def booking_search_html(props):
    store = {"ROOT_QUERY": {"searchQueries": {"search": {"results": props}}}}
    return ('<html><script type="application/json" data-capla-store-data="apollo">'
            f"{json.dumps(store)}</script></html>")


BOOKING_BLOCKED_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_booking.py`:
```python
from datetime import date

import pytest

from app.models import StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import BookingBlocked, BookingProvider, parse_search
from tests.builders import BOOKING_BLOCKED_HTML, booking_property, booking_search_html

Q = StayQuery(lat=39.40, lon=23.03, radius_km=15, check_in=date(2026, 10, 23),
              check_out=date(2026, 10, 25), guests=2, dog=True)


def test_parse_search_maps_fields():
    html = booking_search_html([booking_property(2228437, "Anna's Villa Portaria", 39.3906, 23.0020,
                                                 page="anna-39-s-villa-portaria-portaria")])
    [stay] = parse_search(html)
    assert stay.id == "booking:2228437"
    assert stay.source == "booking"
    assert stay.name == "Anna's Villa Portaria"
    assert stay.url == "https://www.booking.com/hotel/gr/anna-39-s-villa-portaria-portaria.html"
    assert stay.rating == 4.8
    assert stay.total_price == 354.0 and stay.currency == "EUR"
    assert stay.photo == "https://cf.bstatic.com/xdata/images/hotel/square600/2228437.webp"


def test_parse_search_genuine_empty_result():
    assert parse_search(booking_search_html([])) == []


def test_parse_search_challenge_page_raises():
    with pytest.raises(BookingBlocked):
        parse_search(BOOKING_BLOCKED_HTML)


async def no_sleep(seconds):
    pass


async def test_search_with_dog_uses_pets_filter_and_marks_pets():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return 200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)])

    [stay] = await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q)
    assert calls[0]["nflt"] == "hotelfacility=4"
    assert calls[0]["checkin"] == "2026-10-23" and calls[0]["group_adults"] == 2
    assert stay.pets_allowed is True and stay.has_garden is None
    assert "checkin=2026-10-23" in stay.url


async def test_search_without_dog_has_no_filter():
    async def fetch(url, params):
        assert "nflt" not in params
        return 200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)])

    [stay] = await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q.model_copy(update={"dog": False}))
    assert stay.pets_allowed is None


async def test_retries_after_challenge_then_succeeds():
    responses = iter([(202, ""), (200, BOOKING_BLOCKED_HTML),
                      (200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)]))])

    async def fetch(url, params):
        return next(responses)

    assert len(await BookingProvider(fetch=fetch, retries=3, sleep=no_sleep).search(Q)) == 1


async def test_gives_up_with_provider_unavailable():
    async def fetch(url, params):
        raise ConnectionError("reset")

    with pytest.raises(ProviderUnavailable, match="Booking.com unavailable"):
        await BookingProvider(fetch=fetch, retries=2, sleep=no_sleep).search(Q)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.sources.stays.booking'`.

- [ ] **Step 4: Implement**

`app/sources/stays/booking.py`:
```python
import asyncio
import json
import re
import sys
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from app.models import Stay, StayQuery
from app.sources.stays.base import ProviderUnavailable

SEARCH_URL = "https://www.booking.com/searchresults.html"
PETS_FILTER = "hotelfacility=4"
PHOTO_BASE = "https://cf.bstatic.com"
JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)

FetchStatus = Callable[[str, dict], Awaitable[tuple[int, str]]]


class BookingBlocked(Exception):
    pass


def _walk_properties(node: Any) -> list[dict]:
    found = []
    if isinstance(node, dict):
        if "basicPropertyData" in node and "displayName" in node:
            return [node]
        for value in node.values():
            found.extend(_walk_properties(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_properties(value))
    return found


def parse_search(html: str) -> list[Stay]:
    props: list[dict] = []
    seen: set = set()
    for blob in JSON_SCRIPT_RE.findall(html):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for prop in _walk_properties(data):
            pid = (prop.get("basicPropertyData") or {}).get("id")
            if pid is not None and pid not in seen:
                seen.add(pid)
                props.append(prop)
    if not props and "searchQueries" not in html:
        raise BookingBlocked("no search results data on page")
    stays = []
    for prop in props:
        basic = prop["basicPropertyData"]
        loc = basic.get("location") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        score = (basic.get("reviews") or {}).get("totalScore")
        price = (((prop.get("priceDisplayInfoIrene") or {}).get("displayPrice") or {})
                 .get("amountPerStay") or {})
        amount = price.get("amountUnformatted")
        page = basic.get("pageName")
        photo = ((((basic.get("photos") or {}).get("main") or {}).get("highResUrl") or {})
                 .get("relativeUrl"))
        stays.append(Stay(
            id=f"booking:{basic['id']}",
            source="booking",
            name=(prop.get("displayName") or {}).get("text") or "Booking.com stay",
            url=(f"https://www.booking.com/hotel/{loc.get('countryCode', 'gr')}/{page}.html"
                 if page else "https://www.booking.com"),
            lat=loc["latitude"],
            lon=loc["longitude"],
            total_price=round(amount, 2) if amount else None,
            currency=price.get("currency") or None,
            rating=round(score / 2, 2) if score else None,
            photo=f"{PHOTO_BASE}{photo}" if photo else None,
        ))
    return stays


async def default_fetch(url: str, params: dict) -> tuple[int, str]:
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(url, params=params, timeout=30)
        return response.status_code, response.text


class BookingProvider:
    name = "Booking.com"

    def __init__(self, fetch: FetchStatus | None = None, retries: int = 3,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._fetch = fetch or default_fetch
        self._retries = retries
        self._sleep = sleep

    async def search(self, q: StayQuery) -> list[Stay]:
        params = {
            "latitude": q.lat, "longitude": q.lon, "radius": round(q.radius_km),
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "group_adults": q.guests, "no_rooms": 1, "group_children": 0,
            "lang": "en-us", "selected_currency": "EUR",
        }
        if q.dog:
            params["nflt"] = PETS_FILTER
        stays = None
        for attempt in range(self._retries):
            try:
                status, html = await self._fetch(SEARCH_URL, params)
                if status == 200:
                    stays = parse_search(html)
                    break
            except BookingBlocked:
                pass
            except Exception as exc:
                print(f"booking request failed: {exc}", file=sys.stderr)
            if attempt < self._retries - 1:
                await self._sleep(2 * (attempt + 1))
        if stays is None:
            raise ProviderUnavailable("Booking.com unavailable (blocked by the site) – showing other sources")
        link = urlencode({"checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
                          "group_adults": q.guests})
        for stay in stays:
            if q.dog:
                stay.pets_allowed = True
            stay.url = f"{stay.url}?{link}"
        return stays
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/sources/stays/booking.py tests/builders.py tests/test_booking.py
git commit -m "feat(hick-toors): add Booking.com provider with pets filter and soft failure

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Geocoding and routing

**Files:**
- Create: `hick-toors/app/geocode.py`, `hick-toors/app/routing.py`
- Test: `hick-toors/tests/test_geocode.py`, `hick-toors/tests/test_routing.py`

**Interfaces:**
- Consumes: `app.cache.DiskCache`, `DAY`; `app.geo.ensure_min_size`, `haversine_km`; `app.models.BBox`, `LatLon`.
- Produces:
  - `app.geocode.Geocoder(cache: DiskCache, fetch: FetchJson | None = None, min_radius_km: float = 15)` with `async geocode(place: str) -> BBox | None` (raises on network failure; the engine turns that into a warning).
  - `app.routing.RouteTime` (pydantic: `minutes: int | None`, `estimated: bool`, `reachable: bool = True`).
  - `app.routing.Router(cache: DiskCache, api_key: str | None, post: PostJson | None = None)` with `async drive_times(origin: LatLon, dests: list[LatLon]) -> tuple[list[RouteTime], str | None]` (second value is a warning or None).
  - `FetchJson = Callable[[str, dict], Awaitable[Any]]`, `PostJson = Callable[[str, dict, dict], Awaitable[Any]]` (url, json body, headers).

- [ ] **Step 1: Write the failing tests**

`tests/test_geocode.py`:
```python
from app.cache import DiskCache
from app.geo import haversine_km
from app.geocode import Geocoder
from app.models import LatLon


async def test_geocode_expands_small_box_and_caches(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [{"boundingbox": ["39.38", "39.40", "22.98", "23.00"]}]

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch, min_radius_km=15)
    box = await geocoder.geocode("Makrinitsa")
    assert calls[0]["countrycodes"] == "gr" and calls[0]["q"] == "Makrinitsa"
    assert haversine_km(LatLon(lat=39.39, lon=22.99), LatLon(lat=box.north, lon=22.99)) >= 14.5
    again = await geocoder.geocode("  makrinitsa ")
    assert again == box and len(calls) == 1


async def test_geocode_unknown_place_returns_none(tmp_path):
    async def fetch(url, params):
        return []

    assert await Geocoder(DiskCache(tmp_path), fetch=fetch).geocode("Atlantis") is None
```

`tests/test_routing.py`:
```python
from app.cache import DiskCache
from app.models import LatLon
from app.routing import Router

ATHENS = LatLon(lat=37.9838, lon=23.7275)
PELION = LatLon(lat=39.39, lon=22.99)
CRETE = LatLon(lat=35.30, lon=23.95)


async def test_ors_matrix_minutes_and_unreachable(tmp_path):
    calls = []

    async def post(url, body, headers):
        calls.append((body, headers))
        return {"durations": [[12600.0, None]]}

    router = Router(DiskCache(tmp_path), api_key="KEY", post=post)
    times, warning = await router.drive_times(ATHENS, [PELION, CRETE])
    assert warning is None
    assert (times[0].minutes, times[0].estimated, times[0].reachable) == (210, False, True)
    assert (times[1].minutes, times[1].reachable) == (None, False)
    body, headers = calls[0]
    assert headers["Authorization"] == "KEY"
    assert body["locations"][0] == [23.7275, 37.9838]
    assert body["sources"] == [0] and body["destinations"] == [1, 2]


async def test_results_are_cached(tmp_path):
    calls = []

    async def post(url, body, headers):
        calls.append(body)
        return {"durations": [[600.0]]}

    router = Router(DiskCache(tmp_path), api_key="KEY", post=post)
    await router.drive_times(ATHENS, [PELION])
    times, _ = await router.drive_times(ATHENS, [PELION])
    assert len(calls) == 1 and times[0].minutes == 10


async def test_no_key_estimates_without_warning(tmp_path):
    times, warning = await Router(DiskCache(tmp_path), api_key=None).drive_times(ATHENS, [PELION])
    assert warning is None
    assert times[0].estimated is True and times[0].reachable is True
    assert 150 <= times[0].minutes <= 190  # ~170 km straight line at 60 km/h


async def test_ors_failure_falls_back_with_warning(tmp_path):
    async def post(url, body, headers):
        raise ConnectionError("down")

    times, warning = await Router(DiskCache(tmp_path), api_key="KEY", post=post).drive_times(ATHENS, [PELION])
    assert "estimated" in warning
    assert times[0].estimated is True


async def test_empty_destinations(tmp_path):
    assert await Router(DiskCache(tmp_path), api_key="KEY").drive_times(ATHENS, []) == ([], None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_geocode.py tests/test_routing.py -v`
Expected: ERROR `ModuleNotFoundError` for `app.geocode` / `app.routing`.

- [ ] **Step 3: Implement**

`app/geocode.py`:
```python
from typing import Any, Awaitable, Callable

import httpx

from app.cache import DAY, DiskCache
from app.geo import ensure_min_size
from app.models import BBox

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "hick-toors/0.1 (personal local app)"

FetchJson = Callable[[str, dict], Awaitable[Any]]


async def fetch_json(url: str, params: dict) -> Any:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()


class Geocoder:
    def __init__(self, cache: DiskCache, fetch: FetchJson | None = None, min_radius_km: float = 15):
        self._cache = cache
        self._fetch = fetch or fetch_json
        self._min_radius_km = min_radius_km

    async def geocode(self, place: str) -> BBox | None:
        key = f"geocode:{place.strip().lower()}"
        cached = self._cache.get(key)
        if cached:
            return BBox(**cached)
        data = await self._fetch(
            NOMINATIM_URL, {"q": place.strip(), "format": "json", "limit": 1, "countrycodes": "gr"}
        )
        if not data:
            return None
        south, north, west, east = (float(v) for v in data[0]["boundingbox"])
        box = ensure_min_size(BBox(south=south, west=west, north=north, east=east), self._min_radius_km)
        self._cache.set(key, box.model_dump(), 30 * DAY)
        return box
```

`app/routing.py`:
```python
import sys
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel

from app.cache import DAY, DiskCache
from app.geo import haversine_km
from app.models import LatLon

ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
FALLBACK_KMH = 60
FALLBACK_WARNING = "Driving times unavailable – showing ≈ estimated times"

PostJson = Callable[[str, dict, dict], Awaitable[Any]]


class RouteTime(BaseModel):
    minutes: int | None
    estimated: bool
    reachable: bool = True


async def post_json(url: str, body: dict, headers: dict) -> Any:
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=body, headers=headers, timeout=30.0)
        response.raise_for_status()
        return response.json()


def _key(origin: LatLon, dest: LatLon) -> str:
    return f"drive:{origin.lat:.4f},{origin.lon:.4f}->{dest.lat:.4f},{dest.lon:.4f}"


class Router:
    def __init__(self, cache: DiskCache, api_key: str | None, post: PostJson | None = None):
        self._cache = cache
        self._api_key = api_key
        self._post = post or post_json

    async def drive_times(self, origin: LatLon, dests: list[LatLon]) -> tuple[list[RouteTime], str | None]:
        results: list[RouteTime | None] = []
        for dest in dests:
            cached = self._cache.get(_key(origin, dest))
            results.append(RouteTime(**cached) if cached else None)
        missing = [i for i, r in enumerate(results) if r is None]
        warning = None
        if missing and self._api_key:
            body = {
                "locations": [[origin.lon, origin.lat]] + [[dests[i].lon, dests[i].lat] for i in missing],
                "sources": [0],
                "destinations": list(range(1, len(missing) + 1)),
                "metrics": ["duration"],
            }
            try:
                data = await self._post(ORS_MATRIX_URL, body, {"Authorization": self._api_key})
                durations = data["durations"][0]
                for i, seconds in zip(missing, durations):
                    if seconds is None:
                        rt = RouteTime(minutes=None, estimated=False, reachable=False)
                    else:
                        rt = RouteTime(minutes=round(seconds / 60), estimated=False)
                    results[i] = rt
                    self._cache.set(_key(origin, dests[i]), rt.model_dump(), 7 * DAY)
            except Exception as exc:
                print(f"routing failed: {exc}", file=sys.stderr)
                warning = FALLBACK_WARNING
        final = []
        for dest, rt in zip(dests, results):
            if rt is None:
                km = haversine_km(origin, dest)
                rt = RouteTime(minutes=round(km / FALLBACK_KMH * 60), estimated=True)
            final.append(rt)
        return final, warning
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_geocode.py tests/test_routing.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/geocode.py app/routing.py tests/test_geocode.py tests/test_routing.py
git commit -m "feat(hick-toors): add Nominatim geocoding and ORS drive-time routing with fallback

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Dog rule and combo ranking

**Files:**
- Create: `hick-toors/app/dog_rule.py`, `hick-toors/app/ranking.py`
- Test: `hick-toors/tests/test_dog_rule.py`, `hick-toors/tests/test_ranking.py`

**Interfaces:**
- Consumes: `app.models.Stay`, `Hike`, `Combo`, `ScoreBreakdown`, `LatLon`, `Badge`; `app.geo.haversine_km`.
- Produces:
  - `app.dog_rule.assign_badge(stay: Stay, dog: bool) -> Badge | None`
  - `app.dog_rule.apply_dog_rule(stays: list[Stay], dog: bool) -> list[Stay]` (returns copies with `badge` set; filtered + pet-friendly first when dog)
  - `app.dog_rule.badge_tier(stay: Stay) -> int` (0 = pet-friendly or no dog concern, 1 = `garden_unconfirmed`)
  - `app.ranking.rank_combos(pairs: list[tuple[Hike, Stay]], weights: dict[str, float], radius_km: float, dog: bool) -> list[Combo]`

- [ ] **Step 1: Write the failing tests**

`tests/test_dog_rule.py`:
```python
import pytest

from app.dog_rule import apply_dog_rule, assign_badge, badge_tier
from app.models import Stay


def stay(id, pets=None, garden=None):
    return Stay(id=id, source="airbnb", name=id, url="u", lat=39.4, lon=23.0,
                pets_allowed=pets, has_garden=garden)


@pytest.mark.parametrize("pets,garden,dog,expected", [
    (True, None, True, "pets"),
    (True, True, True, "pets_garden"),
    (None, True, True, "garden_unconfirmed"),
    (None, True, False, "garden"),
    (False, True, True, None),
    (False, True, False, "garden"),
    (None, None, True, None),
    (None, False, False, None),
])
def test_assign_badge(pets, garden, dog, expected):
    assert assign_badge(stay("s", pets, garden), dog) == expected


def test_dog_on_filters_and_orders_garden_only_last():
    stays = [
        stay("garden-only", None, True),
        stay("plain", None, False),
        stay("pets", True, False),
        stay("no-pets-garden", False, True),
        stay("pets-garden", True, True),
    ]
    out = apply_dog_rule(stays, dog=True)
    assert [s.id for s in out] == ["pets", "pets-garden", "garden-only"]
    assert [badge_tier(s) for s in out] == [0, 0, 1]


def test_dog_off_keeps_everything_with_badges():
    stays = [stay("a", None, True), stay("b", None, None)]
    out = apply_dog_rule(stays, dog=False)
    assert [(s.id, s.badge) for s in out] == [("a", "garden"), ("b", None)]


def test_does_not_mutate_input():
    s = stay("a", True)
    apply_dog_rule([s], dog=True)
    assert s.badge is None
```

`tests/test_ranking.py`:
```python
import pytest

from app.models import Hike, Stay
from app.ranking import rank_combos

W = {"hike": 40, "stay": 25, "proximity": 20, "price": 15}


def hike(id, trailrank):
    return Hike(id=id, name=id, url="u", lat=39.40, lon=23.00, trailrank=trailrank)


def stay(id, rating, price, lat=39.40, lon=23.00, badge="pets"):
    return Stay(id=id, source="airbnb", name=id, url="u", lat=lat, lon=lon,
                rating=rating, total_price=price, badge=badge)


def test_perfect_combo_scores_100():
    [combo] = rank_combos([(hike("h", 100), stay("s", 5.0, 100))], W, radius_km=15, dog=True)
    assert combo.score == 100
    assert combo.breakdown.model_dump() == {"hike": 40, "stay": 25, "proximity": 20, "price": 15}


def test_price_is_normalised_within_set_and_sorted():
    h = hike("h", 80)
    cheap = stay("cheap", 4.5, 100)
    pricey = stay("pricey", 4.5, 300)
    combos = rank_combos([(h, pricey), (h, cheap)], W, radius_km=15, dog=True)
    assert [c.stay.id for c in combos] == ["cheap", "pricey"]
    assert combos[0].breakdown.price == 15 and combos[1].breakdown.price == 0


def test_far_stay_gets_no_proximity_points():
    far = stay("far", 4.0, 100, lat=39.60)  # ~22 km north
    [combo] = rank_combos([(hike("h", 50), far)], W, radius_km=15, dog=True)
    assert combo.breakdown.proximity == 0


def test_missing_values_use_neutral_defaults():
    [combo] = rank_combos([(hike("h", None), stay("s", None, None))], W, radius_km=15, dog=True)
    assert combo.breakdown.hike == 20 and combo.breakdown.stay == 15 and combo.breakdown.price == 7.5


def test_garden_unconfirmed_ranks_after_pet_friendly_when_dog():
    h = hike("h", 80)
    great_garden = stay("garden", 5.0, 50, badge="garden_unconfirmed")
    ok_pets = stay("pets", 3.0, 300, badge="pets")
    combos = rank_combos([(h, great_garden), (h, ok_pets)], W, radius_km=15, dog=True)
    assert [c.stay.id for c in combos] == ["pets", "garden"]
    combos = rank_combos([(h, great_garden), (h, ok_pets)], W, radius_km=15, dog=False)
    assert [c.stay.id for c in combos] == ["garden", "pets"]


def test_empty():
    assert rank_combos([], W, radius_km=15, dog=True) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dog_rule.py tests/test_ranking.py -v`
Expected: ERROR `ModuleNotFoundError` for `app.dog_rule` / `app.ranking`.

- [ ] **Step 3: Implement**

`app/dog_rule.py`:
```python
from app.models import Badge, Stay

DOG_OK = ("pets", "pets_garden", "garden_unconfirmed")


def assign_badge(stay: Stay, dog: bool) -> Badge | None:
    if stay.pets_allowed is True:
        return "pets_garden" if stay.has_garden else "pets"
    if stay.has_garden:
        if not dog:
            return "garden"
        # A garden counts for the dog only if the listing hasn't explicitly refused pets.
        return "garden_unconfirmed" if stay.pets_allowed is None else None
    return None


def badge_tier(stay: Stay) -> int:
    return 1 if stay.badge == "garden_unconfirmed" else 0


def apply_dog_rule(stays: list[Stay], dog: bool) -> list[Stay]:
    out = []
    for stay in stays:
        badged = stay.model_copy(update={"badge": assign_badge(stay, dog)})
        if dog and badged.badge not in DOG_OK:
            continue
        out.append(badged)
    if dog:
        out.sort(key=badge_tier)  # stable: keeps provider order inside each tier
    return out
```

`app/ranking.py`:
```python
from app.dog_rule import badge_tier
from app.geo import haversine_km
from app.models import Combo, Hike, LatLon, ScoreBreakdown, Stay

NEUTRAL_HIKE = 0.5
NEUTRAL_STAY = 0.6
NEUTRAL_PRICE = 0.5


def rank_combos(
    pairs: list[tuple[Hike, Stay]], weights: dict[str, float], radius_km: float, dog: bool
) -> list[Combo]:
    prices = [s.total_price for _, s in pairs if s.total_price is not None]
    lo, hi = (min(prices), max(prices)) if prices else (0.0, 0.0)
    combos = []
    for hike, stay in pairs:
        hike_n = min(max(hike.trailrank / 100, 0.0), 1.0) if hike.trailrank is not None else NEUTRAL_HIKE
        stay_n = min(max(stay.rating / 5, 0.0), 1.0) if stay.rating is not None else NEUTRAL_STAY
        km = haversine_km(LatLon(lat=hike.lat, lon=hike.lon), LatLon(lat=stay.lat, lon=stay.lon))
        prox_n = max(0.0, 1 - km / radius_km) if radius_km > 0 else 0.0
        if stay.total_price is None:
            price_n = NEUTRAL_PRICE
        elif hi == lo:
            price_n = 1.0
        else:
            price_n = (hi - stay.total_price) / (hi - lo)
        breakdown = ScoreBreakdown(
            hike=round(weights["hike"] * hike_n, 1),
            stay=round(weights["stay"] * stay_n, 1),
            proximity=round(weights["proximity"] * prox_n, 1),
            price=round(weights["price"] * price_n, 1),
        )
        score = round(breakdown.hike + breakdown.stay + breakdown.proximity + breakdown.price, 1)
        combos.append(Combo(hike=hike, stay=stay, score=score, breakdown=breakdown))
    combos.sort(key=lambda c: (badge_tier(c.stay) if dog else 0, -c.score))
    return combos
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dog_rule.py tests/test_ranking.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/dog_rule.py app/ranking.py tests/test_dog_rule.py tests/test_ranking.py
git commit -m "feat(hick-toors): add dog rule badges and hike+stay combo ranking

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Engine and wiring

**Files:**
- Create: `hick-toors/app/engine.py`, `hick-toors/app/wiring.py`
- Test: `hick-toors/tests/test_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6: `Settings`, `DiskCache`/`DAY`/`HOUR`, `WikilocSource`/`WikilocUnavailable`, `StayProvider`/`ProviderUnavailable`, `AirbnbProvider`, `BookingProvider`, `Router`/`RouteTime`, `Geocoder`, `apply_dog_rule`/`badge_tier`, `rank_combos`, `max_drive_minutes`/`stay_window`, geo helpers, models.
- Produces:
  - `app.engine.Engine(settings, wikiloc, providers, router, geocoder, cache)` with
    `async search_hikes(q: HikeQuery) -> HikeSearch`,
    `async search_stays(q: StaySearchQuery) -> StaySearch`,
    `async plan_trip(q: TripQuery) -> TripSearch`,
    `lookup(obj_id: str) -> Hike | Stay | None`,
    `drive_limit_minutes(start, end, limit) -> int | None`.
  - `app.engine.STRAIGHT_LINE_KM_PER_MIN = 1.5`
  - `app.wiring.build_engine(settings: Settings | None = None) -> Engine`

- [ ] **Step 1: Write the failing tests**

`tests/test_engine.py`:
```python
from datetime import date

import pytest

from app.cache import DiskCache
from app.config import Settings
from app.engine import Engine
from app.models import Hike, HikeQuery, LatLon, Stay, StaySearchQuery, TripQuery
from app.routing import RouteTime
from app.sources.stays.base import ProviderUnavailable
from app.sources.wikiloc import WikilocUnavailable

FRI, SUN = date(2026, 10, 23), date(2026, 10, 25)


def hike(id, trailrank, lat=39.4, lon=23.0):
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=lat, lon=lon, trailrank=trailrank)


def stay(id, source="airbnb", pets=True, garden=None, lat=39.41, lon=23.01, rating=4.5, price=200.0):
    return Stay(id=f"{source}:{id}", source=source, name=str(id), url="u", lat=lat, lon=lon,
                pets_allowed=pets, has_garden=garden, rating=rating, total_price=price)


class FakeWikiloc:
    def __init__(self, hikes=None, fail=False):
        self.hikes, self.fail, self.boxes = hikes or [], fail, []

    async def search(self, bbox, query=""):
        self.boxes.append(bbox)
        if self.fail:
            raise WikilocUnavailable("Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute")
        return list(self.hikes)


class FakeRouter:
    def __init__(self, minutes_by_lat):
        self.minutes_by_lat = minutes_by_lat

    async def drive_times(self, origin, dests):
        out = []
        for d in dests:
            m = self.minutes_by_lat[d.lat]
            out.append(RouteTime(minutes=None, estimated=False, reachable=False) if m is None
                       else RouteTime(minutes=m, estimated=False))
        return out, None


class FakeGeocoder:
    def __init__(self, box=None, fail=False):
        self.box, self.fail = box, fail

    async def geocode(self, place):
        if self.fail:
            raise ConnectionError("down")
        return self.box


class FakeProvider:
    def __init__(self, name, stays=None, fail=False):
        self.name, self.stays, self.fail, self.queries = name, stays or [], fail, []

    async def search(self, q):
        self.queries.append(q)
        if self.fail:
            raise ProviderUnavailable(f"{self.name} unavailable – showing other sources")
        return [s.model_copy() for s in self.stays]


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, cache_dir=tmp_path)


def make_engine(settings, wikiloc=None, providers=None, router=None, geocoder=None):
    return Engine(settings, wikiloc or FakeWikiloc(), providers or [], router or FakeRouter({}),
                  geocoder or FakeGeocoder(), DiskCache(settings.cache_dir))


async def test_near_home_weekend_filters_by_drive_limit_and_sorts(settings):
    hikes = [hike(1, 50, lat=38.1), hike(2, 90, lat=39.4), hike(3, 99, lat=40.1), hike(4, 70, lat=35.3)]
    router = FakeRouter({38.1: 60, 39.4: 200, 40.1: 300, 35.3: None})
    engine = make_engine(settings, wikiloc=FakeWikiloc(hikes), router=router)
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.drive_limit_minutes == 240
    assert [h.id for h in result.hikes] == ["wikiloc:2", "wikiloc:1"]
    assert result.hikes[0].drive_minutes == 200


async def test_unlimited_keeps_island_hikes_flagged_ferry(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(4, 70, lat=35.3)]),
                         router=FakeRouter({35.3: None}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=date(2026, 10, 28)))
    assert result.drive_limit_minutes is None
    assert result.hikes[0].ferry_needed is True and result.hikes[0].drive_minutes is None


async def test_drive_limit_override(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, drive_limit=45))
    assert result.hikes == [] and result.drive_limit_minutes == 45


async def test_place_mode_uses_geocoded_box_and_does_not_filter_by_drive(settings):
    from app.models import BBox
    box = BBox(south=39.3, west=22.9, north=39.5, east=23.2)
    wikiloc = FakeWikiloc([hike(2, 90, lat=39.4)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({39.4: 999}), geocoder=FakeGeocoder(box))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=FRI, mode="place", place="Pelion"))
    assert wikiloc.boxes == [box]
    assert [h.id for h in result.hikes] == ["wikiloc:2"]


async def test_place_not_found_and_geocoder_down(settings):
    q = HikeQuery(start=FRI, end=SUN, mode="place", place="Atlantis")
    result = await make_engine(settings, geocoder=FakeGeocoder(None)).search_hikes(q)
    assert result.warnings == ["Couldn't find 'Atlantis' in Greece"]
    result = await make_engine(settings, geocoder=FakeGeocoder(fail=True)).search_hikes(q)
    assert result.warnings == ["Place search unavailable – try again later"]


async def test_wikiloc_unavailable_becomes_warning(settings):
    result = await make_engine(settings, wikiloc=FakeWikiloc(fail=True)).search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.hikes == [] and "Hikes temporarily unavailable" in result.warnings[0]


async def test_search_stays_by_hike_merges_providers_and_reports_failures(settings):
    wikiloc = FakeWikiloc([hike(2, 90, lat=39.4)])
    airbnb = FakeProvider("Airbnb", [stay(1), stay(2, pets=None, garden=True), stay(3, pets=None)])
    booking = FakeProvider("Booking.com", fail=True)
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb, booking], router=FakeRouter({39.4: 200}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, hike_id="wikiloc:2"))
    assert [s.id for s in result.stays] == ["airbnb:1", "airbnb:2"]
    assert [s.badge for s in result.stays] == ["pets", "garden_unconfirmed"]
    assert result.stays[0].distance_km is not None
    assert result.warnings == ["Booking.com unavailable – showing other sources"]
    assert result.center == LatLon(lat=39.4, lon=23.0)
    q = airbnb.queries[0]
    assert (q.check_in, q.check_out, q.radius_km, q.dog) == (FRI, SUN, 15.0, True)


async def test_same_day_trip_books_one_night(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    await engine.search_stays(StaySearchQuery(start=FRI, end=FRI, lat=39.4, lon=23.0))
    assert airbnb.queries[0].check_out == date(2026, 10, 24)


async def test_unknown_hike_id(settings):
    result = await make_engine(settings).search_stays(StaySearchQuery(start=FRI, end=SUN, hike_id="wikiloc:404"))
    assert result.warnings == ["Unknown hike – please search hikes again"]


async def test_stays_far_outside_radius_are_dropped(settings):
    airbnb = FakeProvider("Airbnb", [stay(1), stay(9, lat=39.8)])  # ~44 km away
    result = await make_engine(settings, providers=[airbnb]).search_stays(
        StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0))
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_lookup_finds_registered_objects(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0))
    found = engine.lookup("airbnb:1")
    assert isinstance(found, Stay) and found.badge == "pets"
    assert engine.lookup("airbnb:999") is None


async def test_plan_trip_ranks_combos(settings):
    wikiloc = FakeWikiloc([hike(1, 95, lat=39.4), hike(2, 60, lat=39.45)])
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.41, rating=5.0, price=100)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb],
                         router=FakeRouter({39.4: 200, 39.45: 100}))
    result = await engine.plan_trip(TripQuery(start=FRI, end=SUN))
    assert len(result.combos) == 2
    assert result.combos[0].hike.id == "wikiloc:1"
    assert result.combos[0].score >= result.combos[1].score
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.engine'`.

- [ ] **Step 3: Implement**

`app/engine.py`:
```python
import asyncio
import sys
from datetime import date

from app.cache import DAY, HOUR, DiskCache
from app.config import Settings
from app.dog_rule import apply_dog_rule, badge_tier
from app.geo import GREECE_BBOX, bbox_around, clamp_to_greece, haversine_km
from app.models import (
    DriveLimit, Hike, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
    StaySearchQuery, TripQuery, TripSearch,
)
from app.ranking import rank_combos
from app.sources.stays.base import ProviderUnavailable, StayProvider
from app.sources.wikiloc import WikilocUnavailable
from app.trip_window import max_drive_minutes, stay_window

STRAIGHT_LINE_KM_PER_MIN = 1.5  # generous: nothing within the drive limit falls outside the box
STAY_RADIUS_SLACK = 1.5  # providers search a square; drop results far outside the circle


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class Engine:
    def __init__(self, settings: Settings, wikiloc, providers: list[StayProvider], router, geocoder,
                 cache: DiskCache):
        self.settings = settings
        self.wikiloc = wikiloc
        self.providers = providers
        self.router = router
        self.geocoder = geocoder
        self.cache = cache

    @property
    def home(self) -> LatLon:
        return LatLon(lat=self.settings.home_lat, lon=self.settings.home_lon)

    def drive_limit_minutes(self, start: date, end: date, limit: DriveLimit) -> int | None:
        return max_drive_minutes(start, end, limit, self.settings.day_trip_max_minutes,
                                 self.settings.weekend_max_minutes)

    def _register(self, objs: list) -> None:
        for obj in objs:
            kind = "hike" if isinstance(obj, Hike) else "stay"
            self.cache.set(f"obj:{obj.id}", {"kind": kind, "data": obj.model_dump(mode="json")}, DAY)

    def lookup(self, obj_id: str) -> Hike | Stay | None:
        entry = self.cache.get(f"obj:{obj_id}")
        if not entry:
            return None
        return (Hike if entry["kind"] == "hike" else Stay)(**entry["data"])

    async def _wikiloc_cached(self, box) -> list[Hike]:
        key = f"hikes:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes

    async def search_hikes(self, q: HikeQuery) -> HikeSearch:
        warnings: list[str] = []
        if q.mode == "place":
            try:
                box = await self.geocoder.geocode(q.place)
            except Exception as exc:
                print(f"geocoding failed: {exc}", file=sys.stderr)
                return HikeSearch(warnings=["Place search unavailable – try again later"])
            if box is None:
                return HikeSearch(warnings=[f"Couldn't find '{q.place}' in Greece"])
            limit = None
        else:
            limit = self.drive_limit_minutes(q.start, q.end, q.drive_limit)
            box = GREECE_BBOX if limit is None else clamp_to_greece(
                bbox_around(self.home, limit * STRAIGHT_LINE_KM_PER_MIN))
        try:
            hikes = await self._wikiloc_cached(box)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
        times, warning = await self.router.drive_times(self.home, [LatLon(lat=h.lat, lon=h.lon) for h in hikes])
        if warning:
            warnings.append(warning)
        kept = []
        for h, t in zip(hikes, times):
            h = h.model_copy(update={"drive_minutes": t.minutes, "drive_estimated": t.estimated,
                                     "ferry_needed": not t.reachable})
            if q.mode == "near_home" and limit is not None:
                if not t.reachable or t.minutes is None or t.minutes > limit:
                    continue
            kept.append(h)
        kept.sort(key=lambda h: h.trailrank or 0, reverse=True)
        kept = kept[: self.settings.max_hikes]
        self._register(kept)
        return HikeSearch(hikes=kept, warnings=warnings, drive_limit_minutes=limit)

    async def _provider_search(self, provider: StayProvider, sq: StayQuery) -> tuple[list[Stay], str | None]:
        key = f"stays:{provider.name}:{sq.model_dump_json()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Stay(**s) for s in cached], None
        try:
            stays = await provider.search(sq)
        except ProviderUnavailable as exc:
            return [], str(exc)
        except Exception as exc:
            print(f"{provider.name} failed: {exc}", file=sys.stderr)
            return [], f"{provider.name} unavailable – showing other sources"
        self.cache.set(key, [s.model_dump(mode="json") for s in stays], HOUR)
        return stays, None

    async def search_stays(self, q: StaySearchQuery) -> StaySearch:
        if q.hike_id:
            found = self.lookup(q.hike_id)
            if not isinstance(found, Hike):
                return StaySearch(warnings=["Unknown hike – please search hikes again"])
            center = LatLon(lat=found.lat, lon=found.lon)
        else:
            center = LatLon(lat=q.lat, lon=q.lon)
        radius = q.radius_km or self.settings.stay_radius_km
        check_in, check_out = stay_window(q.start, q.end)
        sq = StayQuery(lat=center.lat, lon=center.lon, radius_km=radius, check_in=check_in,
                       check_out=check_out, guests=q.guests, dog=q.dog)
        results = await asyncio.gather(*(self._provider_search(p, sq) for p in self.providers))
        warnings = [w for _, w in results if w]
        stays = []
        for provider_stays, _ in results:
            for s in provider_stays:
                km = haversine_km(center, LatLon(lat=s.lat, lon=s.lon))
                if km <= radius * STAY_RADIUS_SLACK:
                    stays.append(s.model_copy(update={"distance_km": round(km, 1)}))
        stays = apply_dog_rule(stays, q.dog)
        stays.sort(key=lambda s: (badge_tier(s) if q.dog else 0, s.distance_km))
        self._register(stays)
        return StaySearch(stays=stays, warnings=_dedupe(warnings), center=center)

    async def plan_trip(self, q: TripQuery) -> TripSearch:
        hikes = await self.search_hikes(HikeQuery(start=q.start, end=q.end, drive_limit=q.drive_limit))
        warnings = list(hikes.warnings)
        semaphore = asyncio.Semaphore(2)

        async def stays_for(h: Hike):
            async with semaphore:
                return h, await self.search_stays(StaySearchQuery(
                    start=q.start, end=q.end, hike_id=h.id, guests=q.guests, dog=q.dog))

        results = await asyncio.gather(*(stays_for(h) for h in hikes.hikes[: self.settings.trip_top_hikes]))
        pairs = []
        for h, found in results:
            warnings.extend(found.warnings)
            pairs.extend((h, s) for s in found.stays[: self.settings.trip_stays_per_hike])
        combos = rank_combos(pairs, self.settings.weights, self.settings.stay_radius_km, q.dog)
        return TripSearch(combos=combos[: self.settings.trip_max_combos], warnings=_dedupe(warnings))
```

`app/wiring.py`:
```python
from app.cache import DiskCache
from app.config import Settings
from app.engine import Engine
from app.geocode import Geocoder
from app.routing import Router
from app.sources.stays.airbnb import AirbnbProvider
from app.sources.stays.booking import BookingProvider
from app.sources.wikiloc import WikilocSource


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or Settings()
    cache = DiskCache(settings.cache_dir)
    return Engine(
        settings=settings,
        wikiloc=WikilocSource(pages=settings.hike_pages),
        providers=[AirbnbProvider(detail_top_n=settings.airbnb_detail_top_n), BookingProvider()],
        router=Router(cache, settings.ors_api_key),
        geocoder=Geocoder(cache, min_radius_km=settings.stay_radius_km),
        cache=cache,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all tests (including earlier tasks) PASS.

- [ ] **Step 5: Commit**

```bash
git add app/engine.py app/wiring.py tests/test_engine.py
git commit -m "feat(hick-toors): add search engine orchestrating sources, routing, dog rule, ranking

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: FastAPI API

**Files:**
- Create: `hick-toors/app/main.py`, `hick-toors/static/index.html` (minimal shell; Task 9 replaces it)
- Test: `hick-toors/tests/test_api.py`

**Interfaces:**
- Consumes: `Engine` (methods from Task 7), `build_engine`, `Settings`, `BASE_DIR`, query/result models.
- Produces: `app.main.create_app(engine=None, ask_runner=None, settings=None) -> FastAPI`; module-level `app = create_app()`. Endpoints: `GET /`, `GET /api/config`, `POST /api/hikes`, `POST /api/stays`, `POST /api/trips`. `ask_runner` is any object with `available() -> bool` (Task 10 adds `/api/ask` and the default runner).

`/api/config` response shape:
```json
{"home": {"name": "Athens", "lat": 37.9838, "lon": 23.7275},
 "drive_bands": {"day_trip": 90, "weekend": 240},
 "stay_radius_km": 15.0, "default_guests": 2, "ask_available": false}
```

- [ ] **Step 1: Create the minimal page shell**

`static/index.html`:
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>hick-toors</title></head>
<body><div id="map"></div></body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_api.py`:
```python
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Hike, HikeSearch, Stay, StaySearch, TripSearch


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def search_hikes(self, q):
        self.calls.append(("hikes", q))
        return HikeSearch(hikes=[Hike(id="wikiloc:1", name="h", url="u", lat=39.4, lon=23.0)],
                          warnings=["w"], drive_limit_minutes=240)

    async def search_stays(self, q):
        self.calls.append(("stays", q))
        return StaySearch(stays=[Stay(id="airbnb:1", source="airbnb", name="s", url="u", lat=39.4, lon=23.0)])

    async def plan_trip(self, q):
        self.calls.append(("trips", q))
        return TripSearch()


class FakeAsk:
    def available(self):
        return True


def client(tmp_path, engine=None):
    settings = Settings(_env_file=None, cache_dir=tmp_path)
    return TestClient(create_app(engine=engine or FakeEngine(), ask_runner=FakeAsk(), settings=settings))


def test_index_served(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200 and "<html" in r.text


def test_config(tmp_path):
    body = client(tmp_path).get("/api/config").json()
    assert body["home"] == {"name": "Athens", "lat": 37.9838, "lon": 23.7275}
    assert body["drive_bands"] == {"day_trip": 90, "weekend": 240}
    assert body["ask_available"] is True and body["default_guests"] == 2


def test_hikes_endpoint(tmp_path):
    engine = FakeEngine()
    r = client(tmp_path, engine).post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25",
                                                          "drive_limit": "unlimited"})
    assert r.status_code == 200
    body = r.json()
    assert body["hikes"][0]["id"] == "wikiloc:1" and body["warnings"] == ["w"]
    assert engine.calls[0][1].drive_limit == "unlimited"


def test_stays_and_trips_endpoints(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1"})
    assert r.status_code == 200 and r.json()["stays"][0]["id"] == "airbnb:1"
    r = c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25", "dog": False})
    assert r.status_code == 200 and r.json() == {"combos": [], "warnings": []}


def test_end_before_start_is_422_with_message(tmp_path):
    r = client(tmp_path).post("/api/hikes", json={"start": "2026-10-25", "end": "2026-10-23"})
    assert r.status_code == 422
    assert "end date must be on or after start date" in r.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 4: Implement**

`app/main.py`:
```python
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, Settings
from app.models import HikeQuery, HikeSearch, StaySearch, StaySearchQuery, TripQuery, TripSearch
from app.wiring import build_engine

STATIC_DIR = BASE_DIR / "static"


def create_app(engine=None, ask_runner=None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    app = FastAPI(title="hick-toors")
    app.state.engine = engine
    app.state.ask = ask_runner

    @app.get("/api/config")
    async def config():
        return {
            "home": {"name": settings.home_name, "lat": settings.home_lat, "lon": settings.home_lon},
            "drive_bands": {"day_trip": settings.day_trip_max_minutes, "weekend": settings.weekend_max_minutes},
            "stay_radius_km": settings.stay_radius_km,
            "default_guests": settings.default_guests,
            "ask_available": bool(ask_runner and ask_runner.available()),
        }

    @app.post("/api/hikes", response_model=HikeSearch)
    async def hikes(q: HikeQuery):
        return await engine.search_hikes(q)

    @app.post("/api/stays", response_model=StaySearch)
    async def stays(q: StaySearchQuery):
        return await engine.search_stays(q)

    @app.post("/api/trips", response_model=TripSearch)
    async def trips(q: TripQuery):
        return await engine.plan_trip(q)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py static/index.html tests/test_api.py
git commit -m "feat(hick-toors): add FastAPI JSON API and static page serving

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Map-first UI

**Files:**
- Modify (replace): `hick-toors/static/index.html`
- Create: `hick-toors/static/app.js`, `hick-toors/static/style.css`
- Test: `hick-toors/tests/test_api.py` (add one assertion test), manual browser check

**Interfaces:**
- Consumes: the HTTP API from Task 8 (`/api/config`, `/api/hikes`, `/api/stays`, `/api/trips`) and, once Task 10 lands, `POST /api/ask` → `{summary, hikes, stays, combos: [{hike, stay, note}], warnings}`. Until Task 10, the Ask box shows "Ask Claude unavailable" because `ask_available` is false.
- Produces: the page. Element ids used by JS: `start`, `end`, `drive`, `drive-label`, `dog`, `place`, `search`, `notices`, `hike-list`, `stay-list`, `combo-list`, `hike-count`, `stay-count`, `combo-count`, `map`, `ask-form`, `ask-input`, `ask-button`, `ask-status`, `ask-answer`, `home-label`.

Security requirement: every scraped string (names, places, Claude's summary) is inserted with `textContent` or through `esc()`; never interpolate raw API strings into `innerHTML`.

- [ ] **Step 1: Add the failing page test to `tests/test_api.py`**

```python
def test_index_loads_leaflet_and_app(tmp_path):
    html = client(tmp_path).get("/").text
    assert "leaflet@1.9.4" in html and "leaflet.markercluster@1.5.3" in html
    assert "/static/app.js" in html and 'id="ask-form"' in html


def test_static_assets_served(tmp_path):
    c = client(tmp_path)
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/style.css").status_code == 200
```

Run: `uv run pytest tests/test_api.py -v`
Expected: the 2 new tests FAIL (assets missing / old shell).

- [ ] **Step 2: Write `static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>hick-toors</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header id="controls">
    <strong class="brand">🥾 hick-toors</strong>
    <label>📅 <input type="date" id="start"> → <input type="date" id="end"></label>
    <span id="home-label">🏠 Athens</span>
    <label>🚗 <input type="range" id="drive" min="0" max="9" step="1" value="0">
      <span id="drive-label">Auto</span></label>
    <label class="dog"><input type="checkbox" id="dog" checked> 🐕 Dog</label>
    <fieldset id="modes">
      <label><input type="radio" name="mode" value="near_home" checked> Near home</label>
      <label><input type="radio" name="mode" value="place"> Place</label>
      <label><input type="radio" name="mode" value="trip"> Trip plan</label>
    </fieldset>
    <input type="text" id="place" placeholder="e.g. Pelion, Zagori, Olympus" hidden>
    <button id="search">Search</button>
  </header>
  <div id="notices"></div>
  <main>
    <aside id="sidebar">
      <section><h2>🥾 Hikes <span id="hike-count"></span></h2><ul id="hike-list"></ul></section>
      <section><h2>🏡 Stays <span id="stay-count"></span></h2><ul id="stay-list"></ul></section>
      <section><h2>⭐ Combos <span id="combo-count"></span></h2><ul id="combo-list"></ul></section>
    </aside>
    <div id="map"></div>
  </main>
  <footer id="ask">
    <form id="ask-form">
      <input id="ask-input" maxlength="2000"
             placeholder="💬 Ask Claude: quiet weekend, river hike, cabin with a garden under €120/night">
      <button id="ask-button">Ask</button>
    </form>
    <div id="ask-status"></div>
    <div id="ask-answer"></div>
  </footer>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `static/style.css`**

```css
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; font: 14px/1.4 system-ui, -apple-system, sans-serif; color: #1d2b24; background: #f4f6f3; }
body { display: flex; flex-direction: column; }
#controls { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; padding: 10px 16px; background: #fff; border-bottom: 1px solid #dde3dc; }
#controls .brand { font-size: 16px; }
#controls input[type=date], #place { padding: 4px 6px; border: 1px solid #c8d0c6; border-radius: 6px; }
#modes { border: 0; margin: 0; padding: 0; display: flex; gap: 10px; }
#search, #ask-button { padding: 6px 14px; border: 0; border-radius: 6px; background: #2f6f4e; color: #fff; cursor: pointer; }
#search:disabled, #ask-button:disabled { opacity: .5; cursor: wait; }
#notices { padding: 0 16px; }
.notice { margin: 6px 0; padding: 6px 10px; border-radius: 6px; background: #fff4d6; border: 1px solid #f0d58a; }
main { flex: 1; display: flex; min-height: 0; }
#sidebar { width: 360px; overflow-y: auto; background: #fff; border-right: 1px solid #dde3dc; }
#sidebar section { padding: 8px 12px; border-bottom: 1px solid #eef1ed; }
#sidebar h2 { margin: 4px 0 8px; font-size: 15px; }
#sidebar ul { list-style: none; margin: 0; padding: 0; }
#sidebar li { padding: 6px 8px; border-radius: 6px; cursor: pointer; }
#sidebar li:hover, #sidebar li.active { background: #e7f1ea; }
#sidebar li.selected { outline: 2px solid #2f6f4e; }
#sidebar li .meta { color: #5d6b63; font-size: 12px; }
.badge { display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 10px; background: #e3efe7; margin-top: 2px; }
.badge.garden_unconfirmed { background: #fff0cf; }
#map { flex: 1; }
.pin { background: none; border: 0; }
.pin span { font-size: 22px; filter: drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
.pin b { display: block; margin-top: -4px; padding: 0 4px; border-radius: 4px; font-size: 11px; color: #fff; white-space: nowrap; }
.pin.airbnb b { background: #e0565b; }
.pin.booking b { background: #1f4fa3; }
.popup img { width: 100%; max-height: 120px; object-fit: cover; border-radius: 4px; }
#ask { padding: 8px 16px; background: #fff; border-top: 1px solid #dde3dc; }
#ask-form { display: flex; gap: 8px; }
#ask-input { flex: 1; padding: 6px 8px; border: 1px solid #c8d0c6; border-radius: 6px; }
#ask-status { color: #5d6b63; font-size: 12px; min-height: 16px; }
#ask-answer { white-space: pre-wrap; max-height: 120px; overflow-y: auto; }
@media (max-width: 800px) {
  main { flex-direction: column; }
  #sidebar { width: 100%; max-height: 40vh; border-right: 0; }
  #map { min-height: 50vh; }
}
```

- [ ] **Step 4: Write `static/app.js`**

```js
"use strict";

const DRIVE_STEPS = ["auto", 60, 90, 120, 180, 240, 300, 360, 480, "unlimited"];
const BADGES = {
  pets: "🐕 Pets allowed",
  pets_garden: "🐕🌳 Pets allowed + garden",
  garden: "🌳 Garden",
  garden_unconfirmed: "⚠️ Garden – pets not confirmed",
};
const $ = (id) => document.getElementById(id);
const state = { config: null, hikes: new Map(), stays: new Map(), markers: new Map(), items: new Map() };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtMinutes(m, estimated) {
  if (m == null) return "–";
  const h = Math.floor(m / 60), mm = m % 60;
  return `${estimated ? "≈ " : ""}${h ? h + "h " : ""}${mm}m`;
}
function money(price, currency) {
  if (price == null) return "";
  return `${!currency || currency === "EUR" ? "€" : currency + " "}${Math.round(price)}`;
}
function fmtDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function driveText(step) {
  if (step === "auto") return "Auto (from days)";
  if (step === "unlimited") return "Unlimited";
  return `≤ ${fmtMinutes(step)}`;
}

// ---- map ----
const map = L.map("map").setView([38.5, 23.5], 7);
const tiles = {
  Topo: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)" }),
  Streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
  Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 18, attribution: "Tiles © Esri" }),
};
tiles.Topo.addTo(map);
L.control.layers(tiles).addTo(map);
const hikeLayer = L.markerClusterGroup();
const stayLayer = L.markerClusterGroup();
const overlay = L.layerGroup();
map.addLayer(hikeLayer);
map.addLayer(stayLayer);
overlay.addTo(map);
let reachCircle = null;

function pin(emoji, cls, label) {
  return L.divIcon({ className: `pin ${cls}`, iconSize: null,
    html: `<span>${emoji}</span>${label ? `<b>${esc(label)}</b>` : ""}` });
}
function hikePopup(h) {
  return `<div class="popup">${h.thumbnail ? `<img src="${esc(h.thumbnail)}" alt="">` : ""}
    <strong>${esc(h.name)}</strong><br>
    ${esc(h.near || "")}<br>
    ${h.distance_km ?? "?"} km · +${h.elevation_gain_m ?? "?"} m · ${esc(h.difficulty || "?")} · TrailRank ${h.trailrank ?? "?"}<br>
    🚗 ${h.ferry_needed ? "⛴️ ferry needed" : fmtMinutes(h.drive_minutes, h.drive_estimated)}<br>
    <a href="${esc(h.url)}" target="_blank" rel="noopener">Open on Wikiloc ↗</a></div>`;
}
function stayPopup(s) {
  return `<div class="popup">${s.photo ? `<img src="${esc(s.photo)}" alt="">` : ""}
    <strong>${esc(s.name)}</strong><br>
    ${s.source === "airbnb" ? "Airbnb" : "Booking.com"} · ${money(s.total_price, s.currency)} total
    ${s.rating != null ? ` · ★ ${s.rating}` : ""}<br>
    ${s.badge ? `<span class="badge ${esc(s.badge)}">${esc(BADGES[s.badge])}</span><br>` : ""}
    ${s.distance_km != null ? `${s.distance_km} km from trailhead<br>` : ""}
    <a href="${esc(s.url)}" target="_blank" rel="noopener">Open listing ↗</a></div>`;
}

// ---- notices / api ----
function notices(list) {
  const box = $("notices");
  box.replaceChildren();
  for (const w of list || []) {
    const div = document.createElement("div");
    div.className = "notice";
    div.textContent = w;
    box.appendChild(div);
  }
}
async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = Array.isArray(data.detail)
      ? data.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join("; ")
      : data.detail || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}
const mode = () => document.querySelector("input[name=mode]:checked").value;
const driveLimit = () => DRIVE_STEPS[Number($("drive").value)];
const dates = () => ({ start: $("start").value, end: $("end").value });

// ---- list + markers ----
function listItem(ul, id, title, meta, badge, onClick) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const div = document.createElement("div");
  div.className = "meta";
  div.textContent = meta;
  li.append(strong, div);
  if (badge) {
    const b = document.createElement("span");
    b.className = `badge ${badge}`;
    b.textContent = BADGES[badge];
    li.appendChild(b);
  }
  li.addEventListener("click", onClick);
  li.addEventListener("mouseenter", () => li.classList.add("active"));
  li.addEventListener("mouseleave", () => li.classList.remove("active"));
  ul.appendChild(li);
  state.items.set(id, li);
  return li;
}
function linkMarker(id, marker, layer) {
  marker.on("mouseover", () => state.items.get(id)?.classList.add("active"));
  marker.on("mouseout", () => state.items.get(id)?.classList.remove("active"));
  layer.addLayer(marker);
  state.markers.set(id, marker);
}
function focusMarker(id) {
  const marker = state.markers.get(id);
  if (!marker) return;
  const layer = state.hikes.has(id) ? hikeLayer : stayLayer;
  layer.zoomToShowLayer(marker, () => marker.openPopup());
}
function clearStays() {
  stayLayer.clearLayers();
  for (const id of state.stays.keys()) { state.markers.delete(id); state.items.delete(id); }
  state.stays.clear();
  $("stay-list").replaceChildren();
  $("stay-count").textContent = "";
}
function clearAll() {
  hikeLayer.clearLayers();
  overlay.clearLayers();
  clearStays();
  state.hikes.clear();
  state.markers.clear();
  state.items.clear();
  for (const id of ["hike-list", "combo-list"]) $(id).replaceChildren();
  for (const id of ["hike-count", "combo-count"]) $(id).textContent = "";
}
function addHike(h) {
  state.hikes.set(h.id, h);
  const marker = L.marker([h.lat, h.lon], { icon: pin("🥾", "hike") }).bindPopup(hikePopup(h));
  marker.on("click", () => selectHike(h.id));
  linkMarker(h.id, marker, hikeLayer);
  const drive = h.ferry_needed ? "⛴️ ferry" : `🚗 ${fmtMinutes(h.drive_minutes, h.drive_estimated)}`;
  listItem($("hike-list"), h.id, h.name,
    `${h.distance_km ?? "?"} km · +${h.elevation_gain_m ?? "?"} m · ${h.difficulty || "?"} · ${drive}`,
    null, () => { selectHike(h.id); focusMarker(h.id); });
}
function addStay(s) {
  state.stays.set(s.id, s);
  const marker = L.marker([s.lat, s.lon], { icon: pin("🏡", s.source, money(s.total_price, s.currency)) })
    .bindPopup(stayPopup(s));
  linkMarker(s.id, marker, stayLayer);
  const rating = s.rating != null ? ` · ★ ${s.rating}` : "";
  const km = s.distance_km != null ? ` · ${s.distance_km} km` : "";
  listItem($("stay-list"), s.id, s.name,
    `${s.source === "airbnb" ? "Airbnb" : "Booking.com"} · ${money(s.total_price, s.currency)}${rating}${km}`,
    s.badge, () => focusMarker(s.id));
}
function fitAll() {
  const layers = [...hikeLayer.getLayers(), ...stayLayer.getLayers()];
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.15));
}
function drawReach(minutes) {
  if (reachCircle) { map.removeLayer(reachCircle); reachCircle = null; }
  if (minutes == null || !state.config) return;
  const home = state.config.home;
  reachCircle = L.circle([home.lat, home.lon], { radius: minutes * 1000, color: "#2f6f4e",
    weight: 1, fillOpacity: 0.06, interactive: false }).addTo(map);
}

// ---- actions ----
async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (err) { notices([err.message]); } finally { button.disabled = false; }
}
async function searchHikes() {
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit() };
  if (body.mode === "place") body.place = $("place").value;
  const data = await api("/api/hikes", body);
  clearAll();
  notices(data.warnings);
  data.hikes.forEach(addHike);
  $("hike-count").textContent = `(${data.hikes.length})`;
  drawReach(body.mode === "near_home" ? data.drive_limit_minutes : null);
  if (!data.hikes.length && !data.warnings.length) notices(["No hikes found – try more days or a bigger drive limit."]);
  fitAll();
}
async function selectHike(id) {
  for (const li of document.querySelectorAll("#hike-list li.selected")) li.classList.remove("selected");
  state.items.get(id)?.classList.add("selected");
  const data = await api("/api/stays", { ...dates(), hike_id: id, dog: $("dog").checked,
    guests: state.config?.default_guests ?? 2 });
  clearStays();
  notices(data.warnings);
  data.stays.forEach(addStay);
  $("stay-count").textContent = `(${data.stays.length})`;
  const hike = state.hikes.get(id);
  const layers = [state.markers.get(id), ...stayLayer.getLayers()].filter(Boolean);
  if (layers.length > 1) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.2));
  else if (hike) map.setView([hike.lat, hike.lon], 12);
}
function renderCombos(combos) {
  combos.forEach((c, i) => {
    if (!state.hikes.has(c.hike.id)) addHike(c.hike);
    if (!state.stays.has(c.stay.id)) addStay(c.stay);
    const label = c.score != null ? `${c.score}/100` : c.note || "";
    L.polyline([[c.hike.lat, c.hike.lon], [c.stay.lat, c.stay.lon]],
      { dashArray: "6 6", color: "#2f6f4e", weight: 2 }).bindTooltip(label).addTo(overlay);
    const b = c.breakdown;
    const meta = b ? `hike ${b.hike} · stay ${b.stay} · near ${b.proximity} · price ${b.price}` : (c.note || "");
    const li = listItem($("combo-list"), `combo:${i}`, `${i + 1}. ${c.hike.name} + ${c.stay.name}`,
      `${c.score != null ? c.score + "/100 · " : ""}${meta}`, c.stay.badge,
      () => map.fitBounds(L.latLngBounds([[c.hike.lat, c.hike.lon], [c.stay.lat, c.stay.lon]]).pad(0.3)));
    li.title = meta;
  });
  $("combo-count").textContent = `(${combos.length})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("hike-count").textContent = `(${state.hikes.size})`;
}
async function planTrip() {
  const data = await api("/api/trips", { ...dates(), drive_limit: driveLimit(), dog: $("dog").checked,
    guests: state.config?.default_guests ?? 2 });
  clearAll();
  notices(data.warnings);
  renderCombos(data.combos);
  if (!data.combos.length && !data.warnings.length) notices(["No combos found – try more days or turn off the dog filter."]);
  fitAll();
}
async function ask(event) {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (question.length < 3) return;
  const started = Date.now();
  const timer = setInterval(() => {
    $("ask-status").textContent = `Claude is planning… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  await withBusy($("ask-button"), async () => {
    try {
      const data = await api("/api/ask", { question, context: { ...dates(), dog: $("dog").checked,
        drive_limit: driveLimit(), home: state.config?.home?.name } });
      $("ask-answer").textContent = data.summary || "";
      notices(data.warnings);
      if (data.hikes.length || data.stays.length || data.combos.length) {
        clearAll();
        data.hikes.forEach(addHike);
        data.stays.forEach(addStay);
        renderCombos(data.combos);
        fitAll();
      }
    } finally {
      clearInterval(timer);
      $("ask-status").textContent = "";
    }
  });
}

// ---- init ----
async function init() {
  const today = new Date();
  const friday = new Date(today);
  friday.setDate(today.getDate() + ((5 - today.getDay() + 7) % 7));
  const sunday = new Date(friday);
  sunday.setDate(friday.getDate() + 2);
  $("start").value = fmtDate(friday);
  $("end").value = fmtDate(sunday);
  $("drive-label").textContent = driveText(driveLimit());
  $("drive").addEventListener("input", () => { $("drive-label").textContent = driveText(driveLimit()); });
  for (const radio of document.querySelectorAll("input[name=mode]")) {
    radio.addEventListener("change", () => { $("place").hidden = mode() !== "place"; });
  }
  $("search").addEventListener("click", () =>
    withBusy($("search"), () => (mode() === "trip" ? planTrip() : searchHikes())));
  $("ask-form").addEventListener("submit", ask);
  try {
    state.config = await api("/api/config");
    const home = state.config.home;
    $("home-label").textContent = `🏠 ${home.name}`;
    L.marker([home.lat, home.lon], { icon: pin("🏠", "home") }).bindPopup(esc(home.name)).addTo(map);
    if (!state.config.ask_available) {
      $("ask-input").disabled = true;
      $("ask-button").disabled = true;
      $("ask-input").placeholder = "Ask Claude unavailable – run `claude` once to log in";
    }
  } catch (err) {
    notices([err.message]);
  }
}
init();
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Manual check in the browser**

Run: `uv run uvicorn app.main:app --port 8000` (background), open http://localhost:8000.
Expected: Topo map of Greece, 🏠 at Athens, default dates = next Fri→Sun, slider label "Auto (from days)", Ask box disabled with the "unavailable" placeholder (until Task 10). Click **Search** → hikes appear as 🥾 pins (clustered) with a faint reach circle (or a yellow warning if Wikiloc is rate-limiting). Click a hike → 🏡 stay pins with price labels, list badges, map fits both. Switch mode to **Trip plan** → combos with dashed lines. Check the browser console has no errors. Stop the server.

- [ ] **Step 7: Commit**

```bash
git add static tests/test_api.py
git commit -m "feat(hick-toors): add map-first UI with hikes, stays, combos and Ask box

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: MCP tools and the Ask Claude box

**Files:**
- Create: `hick-toors/app/mcp_tools.py`, `hick-toors/app/ask.py`
- Modify: `hick-toors/app/main.py` (default `AskRunner`, `POST /api/ask`)
- Test: `hick-toors/tests/test_mcp_tools.py`, `hick-toors/tests/test_ask.py`, `hick-toors/tests/test_api.py` (add ask endpoint test)

**Interfaces:**
- Consumes: `Engine` (`search_hikes`, `search_stays`, `plan_trip`, `lookup`), `build_engine`, `Settings`, `BASE_DIR`, models.
- Produces:
  - `app.mcp_tools` module: `mcp = FastMCP("hicktoors")`; async tool functions `search_hikes(start, end, place=None, max_drive_minutes=None, unlimited=False) -> str`, `search_stays(start, end, hike_id=None, lat=None, lon=None, dog=True, guests=2, radius_km=None) -> str`, `plan_trip(start, end, dog=True, guests=2, max_drive_minutes=None, unlimited=False) -> str` (JSON strings); module global `_engine` (tests patch it); `python -m app.mcp_tools` runs stdio.
  - `app.ask`: `AskRequest(question: str 3..2000, context: dict = {})`, `AskCombo(hike, stay, note)`, `AskResult(summary, hikes, stays, combos, warnings)`, `extract_json(text) -> dict | None`, `AskRunner(settings, lookup, run=None)` with `available() -> bool`, `write_mcp_config() -> Path`, `build_command(prompt: str, mcp_config: Path) -> list[str]`, `build_prompt(question, context) -> str`, `parse_output(code, out, err) -> AskResult`, `async ask(req: AskRequest) -> AskResult`; constant `UNAVAILABLE`.
  - `POST /api/ask` → `AskResult`.

`claude -p --output-format json` prints one JSON object such as `{"type":"result","subtype":"success","is_error":false,"result":"<final text>",...}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_tools.py`:
```python
import json
from datetime import date

import pytest

import app.mcp_tools as tools
from app.models import Hike, HikeSearch, StaySearch, TripSearch


class FakeEngine:
    def __init__(self):
        self.queries = []

    async def search_hikes(self, q):
        self.queries.append(q)
        return HikeSearch(hikes=[Hike(id="wikiloc:1", name="h", url="u", lat=39.4, lon=23.0)])

    async def search_stays(self, q):
        self.queries.append(q)
        return StaySearch()

    async def plan_trip(self, q):
        self.queries.append(q)
        return TripSearch()


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(tools, "_engine", fake)
    return fake


async def test_search_hikes_near_home_with_limit(engine):
    out = json.loads(await tools.search_hikes("2026-10-23", "2026-10-25", max_drive_minutes=120))
    assert out["hikes"][0]["id"] == "wikiloc:1"
    q = engine.queries[0]
    assert (q.mode, q.drive_limit, q.start) == ("near_home", 120, date(2026, 10, 23))


async def test_search_hikes_place_and_unlimited(engine):
    await tools.search_hikes("2026-10-23", "2026-10-25", place="Zagori")
    await tools.search_hikes("2026-10-23", "2026-10-28", unlimited=True)
    assert (engine.queries[0].mode, engine.queries[0].place) == ("place", "Zagori")
    assert engine.queries[1].drive_limit == "unlimited"


async def test_search_stays_and_plan_trip(engine):
    await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1", dog=False)
    await tools.plan_trip("2026-10-23", "2026-10-25", guests=3)
    assert engine.queries[0].hike_id == "wikiloc:1" and engine.queries[0].dog is False
    assert engine.queries[1].guests == 3


async def test_invalid_input_returns_error_json(engine):
    out = json.loads(await tools.search_hikes("2026-10-25", "2026-10-23"))
    assert "end date must be on or after start date" in out["error"]
```

`tests/test_ask.py`:
```python
import asyncio
import json
import sys

import pytest

from app.ask import UNAVAILABLE, AskRequest, AskRunner, extract_json
from app.config import Settings
from app.models import Hike, Stay

HIKE = Hike(id="wikiloc:1", name="Portaria loop", url="u", lat=39.39, lon=22.99)
STAY = Stay(id="airbnb:7", source="airbnb", name="Stone house", url="u", lat=39.40, lon=23.0)
OBJECTS = {HIKE.id: HIKE, STAY.id: STAY}


def envelope(text, is_error=False):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text})


ANSWER = """Here is a plan.
```json
{"summary": "Do the Portaria loop, stay at the stone house.", "hike_ids": ["wikiloc:1"],
 "stay_ids": ["airbnb:7", "airbnb:999"], "combos": [{"hike_id": "wikiloc:1", "stay_id": "airbnb:7", "note": "5 min walk"}]}
```"""


@pytest.fixture
def settings(tmp_path):
    # sys.executable exists on PATH lookups, so available() is True
    return Settings(_env_file=None, cache_dir=tmp_path, claude_bin=sys.executable)


def runner(settings, run=None):
    return AskRunner(settings, lookup=OBJECTS.get, run=run)


def test_build_command_flags(settings):
    r = runner(settings)
    path = r.write_mcp_config()
    cmd = r.build_command("hello", path)
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-p", "hello"]
    assert cmd[cmd.index("--mcp-config") + 1] == str(path)
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__hicktoors"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--append-system-prompt" in cmd
    config = json.loads(path.read_text())
    server = config["mcpServers"]["hicktoors"]
    assert server["args"][-2:] == ["-m", "app.mcp_tools"]


def test_build_prompt_includes_context(settings):
    prompt = runner(settings).build_prompt("river hike", {"start": "2026-10-23", "dog": True})
    assert "river hike" in prompt and '"start": "2026-10-23"' in prompt


def test_extract_json_prefers_last_fenced_block():
    assert extract_json(ANSWER)["hike_ids"] == ["wikiloc:1"]
    assert extract_json('text {"summary": "x"} more') == {"summary": "x"}
    assert extract_json("no json here") is None


def test_parse_output_resolves_ids_and_warns_on_unknown(settings):
    result = runner(settings).parse_output(0, envelope(ANSWER), "")
    assert result.summary.startswith("Do the Portaria loop")
    assert [h.id for h in result.hikes] == ["wikiloc:1"]
    assert [s.id for s in result.stays] == ["airbnb:7"]
    assert result.combos[0].note == "5 min walk" and result.combos[0].stay.id == "airbnb:7"
    assert result.warnings == ["1 result(s) from Claude could not be found and were skipped"]


def test_parse_output_without_json_keeps_text(settings):
    result = runner(settings).parse_output(0, envelope("Try Pelion this weekend."), "")
    assert result.summary == "Try Pelion this weekend."
    assert result.warnings == ["Claude's answer had no map data"]


def test_parse_output_login_error(settings):
    result = runner(settings).parse_output(1, envelope("Invalid API key · Please run /login", True), "")
    assert result.warnings == [UNAVAILABLE]


def test_parse_output_other_error(settings):
    result = runner(settings).parse_output(1, "", "boom")
    assert result.warnings == ["Claude couldn't answer: boom"]


async def test_ask_happy_path(settings):
    seen = []

    async def run(cmd):
        seen.append(cmd)
        return 0, envelope(ANSWER), ""

    result = await runner(settings, run).ask(AskRequest(question="weekend hike", context={}))
    assert result.hikes[0].id == "wikiloc:1"
    assert seen[0][2].startswith("weekend hike")


async def test_ask_timeout(settings):
    async def run(cmd):
        raise asyncio.TimeoutError

    result = await runner(settings, run).ask(AskRequest(question="weekend hike"))
    assert result.warnings == ["Claude took longer than 120 s – try a simpler question"]


async def test_ask_when_cli_missing(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, claude_bin="definitely-not-installed-xyz")
    r = AskRunner(settings, lookup=OBJECTS.get)
    assert r.available() is False
    result = await r.ask(AskRequest(question="weekend hike"))
    assert result.warnings == [UNAVAILABLE]


async def test_ask_rejects_concurrent_runs(settings):
    gate = asyncio.Event()

    async def run(cmd):
        await gate.wait()
        return 0, envelope(ANSWER), ""

    r = runner(settings, run)
    first = asyncio.create_task(r.ask(AskRequest(question="weekend hike")))
    await asyncio.sleep(0)
    second = await r.ask(AskRequest(question="another one"))
    gate.set()
    await first
    assert second.warnings == ["Claude is still answering the previous question – please wait"]
```

Append to `tests/test_api.py`:
```python
from app.ask import AskResult


class FakeAskRunner:
    def __init__(self):
        self.requests = []

    def available(self):
        return True

    async def ask(self, req):
        self.requests.append(req)
        return AskResult(summary="Go to Pelion")


def test_ask_endpoint(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path)
    runner = FakeAskRunner()
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=runner, settings=settings))
    r = c.post("/api/ask", json={"question": "weekend hike", "context": {"dog": True}})
    assert r.status_code == 200 and r.json()["summary"] == "Go to Pelion"
    assert runner.requests[0].context == {"dog": True}
    assert c.post("/api/ask", json={"question": "x"}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py tests/test_ask.py tests/test_api.py -v`
Expected: ERROR `ModuleNotFoundError` for `app.mcp_tools` / `app.ask`.

- [ ] **Step 3: Implement `app/mcp_tools.py`**

```python
import json

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from app.models import HikeQuery, StaySearchQuery, TripQuery

mcp = FastMCP("hicktoors")
_engine = None


def engine():
    global _engine
    if _engine is None:
        from app.wiring import build_engine

        _engine = build_engine()
    return _engine


def _limit(max_drive_minutes: int | None, unlimited: bool):
    if unlimited:
        return "unlimited"
    return max_drive_minutes if max_drive_minutes else "auto"


def _error(exc: ValidationError) -> str:
    return json.dumps({"error": "; ".join(e["msg"] for e in exc.errors())})


@mcp.tool()
async def search_hikes(start: str, end: str, place: str | None = None,
                       max_drive_minutes: int | None = None, unlimited: bool = False) -> str:
    """Find hikes in Greece (Wikiloc) for a trip from start to end (YYYY-MM-DD).

    Without `place`, searches around home (Athens) within a drive limit that defaults from the
    number of days (1 day: 90 min, 2-3 days: 4 h, 4+ days: unlimited). Set max_drive_minutes to
    override, or unlimited=True to include islands (hikes flagged ferry_needed).
    With `place` (e.g. "Pelion", "Zagori"), searches that area instead.
    Returns JSON {hikes: [{id, name, url, lat, lon, distance_km, elevation_gain_m, difficulty,
    trailrank, near, drive_minutes, ferry_needed}], warnings}.
    """
    try:
        q = HikeQuery(start=start, end=end, mode="place" if place else "near_home", place=place,
                      drive_limit=_limit(max_drive_minutes, unlimited))
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_hikes(q)).model_dump_json()


@mcp.tool()
async def search_stays(start: str, end: str, hike_id: str | None = None, lat: float | None = None,
                       lon: float | None = None, dog: bool = True, guests: int = 2,
                       radius_km: float | None = None) -> str:
    """Find stays (Airbnb, Booking.com) near a hike (hike_id from search_hikes) or a lat/lon.

    With dog=True only stays that allow pets, or have a garden (badge garden_unconfirmed =
    pets not confirmed), are returned. Returns JSON {stays: [{id, source, name, url, lat, lon,
    total_price, currency, rating, badge, distance_km}], warnings}.
    """
    try:
        q = StaySearchQuery(start=start, end=end, hike_id=hike_id, lat=lat, lon=lon, dog=dog,
                            guests=guests, radius_km=radius_km)
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_stays(q)).model_dump_json()


@mcp.tool()
async def plan_trip(start: str, end: str, dog: bool = True, guests: int = 2,
                    max_drive_minutes: int | None = None, unlimited: bool = False) -> str:
    """Rank hike + stay combos for a trip from home (score /100 with breakdown).

    Returns JSON {combos: [{hike, stay, score, breakdown}], warnings}.
    """
    try:
        q = TripQuery(start=start, end=end, dog=dog, guests=guests,
                      drive_limit=_limit(max_drive_minutes, unlimited))
    except ValidationError as exc:
        return _error(exc)
    return (await engine().plan_trip(q)).model_dump_json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Implement `app/ask.py`**

```python
import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from app.config import BASE_DIR, Settings
from app.models import Hike, Stay

UNAVAILABLE = "Ask Claude unavailable – run `claude` once to log in"
BUSY = "Claude is still answering the previous question – please wait"
FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
LOGIN_RE = re.compile(r"log ?in|auth|credential|api key", re.I)

SYSTEM_PROMPT = """You are the trip planner inside hick-toors, a local site for finding hikes in Greece
plus a place to stay, usually travelling with a dog. Home is {home}.
Use the hicktoors tools (search_hikes, search_stays, plan_trip) to find real options; never invent
hikes, stays, prices or coordinates. Dates are YYYY-MM-DD.
Finish with a short friendly plan, then a final fenced JSON block exactly like:
```json
{{"summary": "<2-5 sentence plan>", "hike_ids": ["wikiloc:..."], "stay_ids": ["airbnb:..."],
 "combos": [{{"hike_id": "wikiloc:...", "stay_id": "airbnb:...", "note": "<why>"}}]}}
```
Only use ids returned by the tools."""

Run = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    context: dict = {}


class AskCombo(BaseModel):
    hike: Hike
    stay: Stay
    note: str = ""


class AskResult(BaseModel):
    summary: str = ""
    hikes: list[Hike] = []
    stays: list[Stay] = []
    combos: list[AskCombo] = []
    warnings: list[str] = []


def extract_json(text: str) -> dict | None:
    candidates = FENCED_JSON_RE.findall(text)[::-1]
    if "{" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class AskRunner:
    def __init__(self, settings: Settings, lookup: Callable[[str], Hike | Stay | None], run: Run | None = None):
        self.settings = settings
        self._lookup = lookup
        self._run = run or self._run_subprocess
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return shutil.which(self.settings.claude_bin) is not None

    def write_mcp_config(self) -> Path:
        path = Path(self.settings.cache_dir) / "mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        config = {"mcpServers": {"hicktoors": {
            "command": shutil.which("uv") or "uv",
            "args": ["run", "--directory", str(BASE_DIR), "python", "-m", "app.mcp_tools"],
        }}}
        path.write_text(json.dumps(config, indent=2))
        return path

    def build_prompt(self, question: str, context: dict) -> str:
        return f"{question}\n\nContext from the search form: {json.dumps(context, ensure_ascii=False)}"

    def build_command(self, prompt: str, mcp_config: Path) -> list[str]:
        return [
            shutil.which(self.settings.claude_bin) or self.settings.claude_bin,
            "-p", prompt,
            "--mcp-config", str(mcp_config),
            "--strict-mcp-config",
            "--allowedTools", "mcp__hicktoors",
            "--tools", "",
            "--append-system-prompt", SYSTEM_PROMPT.format(home=self.settings.home_name),
            "--output-format", "json",
        ]

    async def _run_subprocess(self, cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(BASE_DIR))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.settings.ask_timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    def parse_output(self, code: int, out: str, err: str) -> AskResult:
        try:
            envelope = json.loads(out)
        except ValueError:
            envelope = None
        if code != 0 or not isinstance(envelope, dict) or envelope.get("is_error"):
            text = ((envelope or {}).get("result") if isinstance(envelope, dict) else None) or err or out
            if LOGIN_RE.search(text or ""):
                return AskResult(warnings=[UNAVAILABLE])
            return AskResult(warnings=[f"Claude couldn't answer: {(text or 'no output').strip()[:200]}"])
        text = envelope.get("result") or ""
        payload = extract_json(text)
        if payload is None:
            return AskResult(summary=text.strip(), warnings=["Claude's answer had no map data"])
        missing = 0

        def resolve(obj_id, kind):
            nonlocal missing
            obj = self._lookup(str(obj_id))
            if isinstance(obj, kind):
                return obj
            missing += 1
            return None

        hikes = [h for h in (resolve(i, Hike) for i in payload.get("hike_ids") or []) if h]
        stays = [s for s in (resolve(i, Stay) for i in payload.get("stay_ids") or []) if s]
        combos = []
        for c in payload.get("combos") or []:
            if not isinstance(c, dict):
                continue
            hike, stay = resolve(c.get("hike_id"), Hike), resolve(c.get("stay_id"), Stay)
            if hike and stay:
                combos.append(AskCombo(hike=hike, stay=stay, note=str(c.get("note") or "")))
        warnings = [f"{missing} result(s) from Claude could not be found and were skipped"] if missing else []
        return AskResult(summary=str(payload.get("summary") or text).strip(), hikes=hikes, stays=stays,
                         combos=combos, warnings=warnings)

    async def ask(self, req: AskRequest) -> AskResult:
        if not self.available():
            return AskResult(warnings=[UNAVAILABLE])
        if self._lock.locked():
            return AskResult(warnings=[BUSY])
        async with self._lock:
            cmd = self.build_command(self.build_prompt(req.question, req.context), self.write_mcp_config())
            try:
                code, out, err = await self._run(cmd)
            except asyncio.TimeoutError:
                return AskResult(warnings=[
                    f"Claude took longer than {self.settings.ask_timeout_seconds} s – try a simpler question"])
            except FileNotFoundError:
                return AskResult(warnings=[UNAVAILABLE])
            return self.parse_output(code, out, err)
```

- [ ] **Step 5: Wire `/api/ask` into `app/main.py`**

Add import: `from app.ask import AskRequest, AskResult, AskRunner`.
In `create_app`, replace `app.state.ask = ask_runner` with:
```python
    ask_runner = ask_runner or AskRunner(settings, engine.lookup)
    app.state.ask = ask_runner
```
and add the endpoint after `/api/trips`:
```python
    @app.post("/api/ask", response_model=AskResult)
    async def ask(req: AskRequest):
        return await ask_runner.ask(req)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: the whole suite PASSES.

- [ ] **Step 7: Check the MCP server starts**

Run: `(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 8) | uv run python -m app.mcp_tools | head -c 300`
(macOS has no `timeout`; the `sleep` keeps stdin open long enough for the reply, then the server exits.)
Expected: a JSON-RPC response containing `"serverInfo"` and `"hicktoors"`.

- [ ] **Step 8: Commit**

```bash
git add app/mcp_tools.py app/ask.py app/main.py tests/test_mcp_tools.py tests/test_ask.py tests/test_api.py
git commit -m "feat(hick-toors): add MCP tools and Ask Claude box via headless claude -p

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: README and live smoke test

**Files:**
- Create: `hick-toors/README.md`
- Test: manual live run (network)

**Interfaces:**
- Consumes: the whole app.
- Produces: documentation; a verified live run.

- [ ] **Step 1: Write `README.md`**

````markdown
# 🥾 hick-toors

Find your next hike in Greece plus a dog-friendly place to stay. Runs locally.

## Run

```bash
cd hick-toors
cp .env.example .env        # optional: add a free ORS_API_KEY for real driving times
uv sync
uv run uvicorn app.main:app --reload
# open http://localhost:8000
```

## How it works
- **Dates → reach:** 1 day ≈ 1.5 h drive, 2–3 days ≈ 4 h, 4+ days anywhere (islands marked ⛴️). The 🚗 slider overrides it.
- **Modes:** *Near home* (around Athens), *Place* (e.g. "Zagori"), *Trip plan* (ranked hike + stay combos, score /100).
- **🐕 Dog:** stays must allow pets (🐕) or have a garden (⚠️ pets not confirmed, shown last).
- **Sources:** Wikiloc (hikes), Airbnb and Booking.com (stays; Booking is often blocked and then skipped), OpenRouteService (driving times; estimated without a key), OpenStreetMap Nominatim (place search). Results are cached in `.cache/`.
- **Ask Claude:** uses your local Claude Code login (`claude -p`) with this app's search tools only. It counts against your Claude Code plan's usage; no API key needed. Run `claude` once to log in if the box says unavailable.

## Use the tools from Claude Code
```bash
claude mcp add hicktoors -- uv run --directory "$(pwd)" python -m app.mcp_tools
```

## Tests
```bash
uv run pytest
```
````

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 3: Live API smoke test**

Run the server in the background: `uv run uvicorn app.main:app --port 8000`
Then:
```bash
curl -s -X POST localhost:8000/api/hikes -H 'Content-Type: application/json' \
  -d '{"start":"2026-10-23","end":"2026-10-25"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['hikes']), d['warnings'], d['hikes'][:1])"
```
Expected: some hikes (or a Wikiloc rate-limit warning — retry after a minute). Take the first hike id and:
```bash
curl -s -X POST localhost:8000/api/stays -H 'Content-Type: application/json' \
  -d '{"start":"2026-10-23","end":"2026-10-25","hike_id":"<id from above>","dog":true}' | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['stays']), d['warnings'], [(s['source'], s['badge']) for s in d['stays'][:5]])"
```
Expected: Airbnb stays with `pets` / `pets_garden` / `garden_unconfirmed` badges; Booking stays or a "Booking.com unavailable" warning.

- [ ] **Step 4: Live UI + Ask check**

Open http://localhost:8000 and repeat the Task 9 manual check with real data. Then type in the Ask box: "Weekend hike near a river, cabin with a garden" and submit.
Expected: elapsed-seconds status, then a short plan text and pins on the map within ~120 s (or a clear warning message). Stop the server.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(hick-toors): add README with run, sources, Ask Claude and MCP usage

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
