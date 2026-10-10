# hick-toors v2 — Search Filters + Draw-Area Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user set detailed hike and stay filters in two popups (persisted in the browser) and draw a rectangle or circle on the map to search hikes inside it, with every result honouring the chosen filters.

**Architecture:** Filter models (`HikeFilters`, `StayFilters`, `DrawnArea`) live in `app/models.py` and ride on the existing query models. A new `app/filters.py` owns the catalogue served at `GET /api/filters`, the post-fetch hike filtering, the stay price/rating post-check and the "which provider can't honour which filter" rule. Anonymously Wikiloc only supports `sort` server-side, so the engine fetches more pages and filters itself; with an optional `WIKILOC_COOKIE` the hike filters go to Wikiloc too (engine filter kept as a safety net, anonymous fallback when the login expires); Airbnb and Booking translate filters into their own params, and a provider that can't honour an active filter is skipped with a notice. The static UI builds both popups from the catalogue (`static/filters.js` pure logic + `static/dialog.js` DOM) and adds Leaflet.draw for the drawn area.

**Tech Stack:** Python 3.10+, FastAPI, pydantic v2, httpx, curl_cffi, FastMCP, pytest + pytest-asyncio; Leaflet 1.9.4, Leaflet.markercluster 1.5.3, Leaflet.draw 1.0.4 (unpkg); node (syntax check + logic test); Playwright with installed Chrome for the UI check script.

**Spec:** `docs/superpowers/specs/2026-10-10-filters-and-draw-design.md` (v1 context: `docs/superpowers/specs/2026-10-09-hick-toors-design.md`, `docs/superpowers/plans/2026-10-09-hick-toors.md`)

## Global Constraints

- Project folder: `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors` (git worktree, branch `feat/hick-toors`). All paths below are relative to it; run every command from it. Never `git add` files outside it.
- Tests: `env -u VIRTUAL_ENV uv run pytest`. Tests never touch the network (sources take injectable fetch callables). The 114 existing tests must keep passing except where a task explicitly changes a test's contract.
- Python must stay 3.10-compatible: no `type X = ...`, no `ExceptionGroup`, no `asyncio.timeout()`, no `typing.Self`.
- Defaults reproduce v1 exactly: `HikeFilters()` = hiking routes only (activities `[1]`), popular order; `StayFilters()` = no filter.
- Wikiloc: anonymous filter params return 0 results, so only `sort=trailrank` (for `top_rated`) is sent; every other hike filter is applied in the engine after fetching. When any post-fetch hike filter differs from defaults, fetch `hike_pages_filtered = 10` pages (up to 250 routes) instead of `hike_pages = 6`.
- Hikes with a missing value for a filtered field are dropped; with that field unfiltered they are kept.
- Wikiloc login mode (spec §3.1a): `wikiloc_cookie` (env `WIKILOC_COOKIE`) lives only in the gitignored `.env`; `.env.example` gets a commented placeholder, never a value; the cookie is never logged, printed, cached, returned by an API or put in a repr. With it: Cookie header + server params `a` (comma-joined activities), `lfr`/`lto` (km), `ufr`/`uto` (m), `sfr`/`sto` (difficulty clamped to 4; user max 5 → no `sto`), `t=1` (`loop_only`), and normal `hike_pages` (6). Engine post-filter runs in both modes. Anonymous `loop_only` → ignored with "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)". Logged-in page 0 empty or `forwardToLogin` → one anonymous retry + post-filter; if that yields matching hikes, warn "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env".
- Airbnb params (all AND): `price_min`/`price_max` (EUR per night), `min_bedrooms`, `min_beds`, `min_bathrooms`, `room_types[]=Entire home/apt`, `l2_property_type_ids[]` (1 House, 2 Guesthouse, 3 Apartment, 4 Hotel; villa→1), `guest_favorite=true`, `ib=true`, `flexible_cancellation=true`, `kg_and_tags[]=Tag:789`, `amenities[]` = 4 wifi, 8 kitchen, 9 free parking, 7 pool, 25 hot tub, 27 indoor fireplace, 33 washer, 5 AC, 30 heating, 47 workspace, 97 EV charger, 99 BBQ.
- Booking `nflt` tokens (repeat a field = OR, different fields = AND): `price=EUR-<min>-<max>-1` with literal `min`/`max` for open ends, `review_score=60|70|80|90` (rating×20 floored to step), `ht_id` (204 Hotels, 201 Apartments, 213 Villas, 220 Vacation Homes, 216 Guesthouses, 208 B&B, 223 Country Houses; house→220+223, guesthouse→216+208), `privacy_type=3`, `class=N..5`, `fc=2`, `mealplan=1`, `hotelfacility=` 2 parking, 107 wifi, 433 pool, 63 hot tub, 182 EV charging, 146 beachfront, 4 pets; `roomfacility=` 11 AC, 999 kitchen, 34 washer; `entire_place_bedroom_count=1..5`, `min_bathrooms=1..8`. Endpoint `https://www.booking.com/searchresults.en-gb.html`, a fresh curl_cffi chrome session per attempt, retry on 202.
- Provider skipping: an active filter a provider can't honour skips that provider with the notice `"<Provider> skipped – it can't filter by <labels>"`; all skipped → `"No source supports this combination of filters"`. Price and rating post-checks run for every provider and never drop a stay over missing data. Price is per night (`total_price / nights`).
- Notices verbatim: `"No hikes match your filters"`, `"No stays match your filters"`, `"Draw a rectangle or circle first"`.
- DrawnArea: `{shape: "rectangle", south, west, north, east}` or `{shape: "circle", lat, lon, radius_m}`; validation south<north, west<east, radius 200 m–300 km; clamped to Greece; widened to ≥ 5 km radius if tiny; circle trims hikes farther than its radius.
- Invalid ranges / areas → HTTP 422 with a readable message (pydantic validators).
- UI: buttons `⚙️ Hike filters •N`, `⚙️ Stay filters •N`, `✏️ Draw area`; fourth mode **Drawn area**. Popups are modal, generated from `GET /api/filters`, never search on their own: **Apply** stores + closes, **Reset to defaults**, **Cancel**. Min > max highlights and disables Apply. Persistence only through `loadFilters()` / `saveFilters()` (localStorage); corrupt/missing storage → defaults.
- Leaflet.draw 1.0.4 exactly, from unpkg (`https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js` and `.css`), rectangle + circle tools only; a new shape replaces the old one.
- Security: every scraped or user-supplied string rendered by new UI code goes through `textContent` or `esc()`; no `innerHTML` with unescaped data.
- `tests/ui/` holds scripts that are not pytest tests (`filters_logic.test.js` for node, `check_ui.py` for Playwright); never name them `test_*.py`.
- Commits end with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **A filter set that every provider must skip** (e.g. Beds + Breakfast included: Booking can't do beds, Airbnb can't do breakfast) → no provider request at all, one skip notice per provider plus "No source supports this combination of filters"; never an empty list with no explanation. Test: Task 6 `test_every_provider_skipped_gives_no_source_notice`.
2. **Booking ORs repeated `nflt` fields** — dog ON (the default) sends `hotelfacility=4`; adding "Free parking" (`hotelfacility=2`) would return parking-OR-pets places that v1 then marks pets-allowed. Expected: Booking is skipped ("Pets + Free parking together") whenever two values of `hotelfacility` (or of `roomfacility`) would be required. Tests: Task 2 `test_booking_cannot_require_two_values_of_one_facility_field`, Task 6 skip path.
3. **Circle or rectangle crossing the Greece box, or entirely outside it** → the search box is clamped to Greece, Greek hikes inside the shape are kept; a shape entirely outside gives "The drawn area is outside Greece – draw it over Greece" and no Wikiloc request (instead of an inverted box). Tests: Task 6 `test_circle_crossing_greece_border_is_clamped_but_keeps_greek_hikes`, `test_area_outside_greece_warns_without_fetching`.
4. **Price per night vs. total price** — stays carry the total for the stay; "≤ €120/night" over 2 nights must keep a €200 total and drop a €300 total; a same-day trip counts as 1 night; a stay without a price is kept. Tests: Task 2 `test_stay_post_check_uses_price_per_night`, Task 6 `test_price_post_check_is_per_night`.
5. **localStorage holding an old, partial or corrupt filter shape (or throwing in private mode)** → each bad field falls back to its default, unknown keys are dropped, good values are kept, the page loads without errors and never sends a shape the API rejects. Tests: Task 9 `tests/ui/filters_logic.test.js` (sanitize cases) and the corrupt-storage step in `tests/ui/check_ui.py`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/models.py` | Modify | `HikeFilters`, `StayFilters`, `RectangleArea`, `CircleArea`, `DrawnArea`; `Hike.activity`/`Hike.skill`; filters/area fields on `HikeQuery`, `StaySearchQuery`, `TripQuery`, `StayQuery` |
| `app/filters.py` | Create | Catalogue for the UI, `hike_filters_active`, `stay_filters_active`, `apply_hike_filters`, `stay_post_check`, `unsupported_filters`, `skip_notice` |
| `app/sources/wikiloc.py` | Modify | Keep every activity in `parse_search` (Greece-only), record picto/skill; `search(..., sort, pages)` |
| `app/sources/stays/airbnb.py` | Modify | `filter_params(StayFilters) -> dict` merged into search params |
| `app/sources/stays/booking.py` | Modify | `review_score`, `nflt_tokens`, en-gb endpoint, 4 retries |
| `app/config.py` | Modify | `hike_pages_filtered = 10` |
| `app/engine.py` | Modify | Filter pass-through, page count, post-checks, provider skipping, drawn area, cache keys |
| `app/main.py` | Modify | `GET /api/filters` |
| `app/mcp_tools.py` | Modify | `hike_filters` / `stay_filters` dict args, error messages with field path |
| `static/filters.js` | Create | Pure filter logic: sanitize, count, range validation, `loadFilters`/`saveFilters` |
| `static/dialog.js` | Create | Modal popup generated from the catalogue |
| `static/app.js` | Modify | Popup wiring, badges, filters in requests, Leaflet.draw, Drawn area mode |
| `static/index.html`, `static/style.css` | Modify | Buttons, mode, scripts, dialog styles |
| `tests/ui/filters_logic.test.js` | Create | node test of `static/filters.js` |
| `tests/ui/check_ui.py` | Create | Headless-Chrome check (Playwright, `channel="chrome"`) |
| `tests/test_*.py` | Modify/Create | Unit tests per task (`tests/test_filters.py` new) |
| `README.md` | Modify | Filters + draw usage, `WIKILOC_COOKIE`, UI check commands |
| `app/wiring.py`, `.env.example` | Modify | Pass `wikiloc_cookie` to `WikilocSource`; commented `WIKILOC_COOKIE` placeholder |
| `tests/test_wiring.py` | Create | Cookie setting → login mode, cookie never in repr |

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 (Task 3 needs Task 2's `apply_hike_filters`; Tasks 4–5 need Task 1's `StayFilters`; Task 6 needs 1–5; Task 7 (Wikiloc login mode) needs 3 and 6; Task 8 needs Task 7's `catalogue(wikiloc_login=...)`; Tasks 9–10 need 8).

---

### Task 1: Filter and drawn-area models

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (in `app.models`):
  - `Activity = Literal[1, 43, 48, 57, 66, 60]`, `PropertyType = Literal["house","apartment","guesthouse","hotel","villa"]`, `Amenity = Literal["wifi","kitchen","parking","pool","hot_tub","ac","washer","ev_charger","fireplace","heating","workspace","bbq"]`.
  - `HikeFilters` (extra="forbid"): `activities: list[Activity] = [1]` (min 1 item, deduped), `difficulty_min: int = 1`, `difficulty_max: int = 5` (1–5), `distance_min_km/distance_max_km: float | None` (≥0), `elevation_min_m/elevation_max_m: int | None` (≥0), `min_trailrank: int | None` (0–100), `sort: Literal["popular","top_rated"] = "popular"`. Range error text: `"<name>: minimum must not be greater than maximum"` with names `difficulty`, `distance`, `elevation gain`.
  - `StayFilters` (extra="forbid"): `price_min/price_max: int | None` (≥0, EUR/night; range name `price per night`), `min_rating: float | None` (0–5), `entire_place: bool`, `property_types: list[PropertyType]`, `min_bedrooms: int | None` (1–10), `min_beds: int | None` (1–16), `min_bathrooms: int | None` (1–8), `amenities: list[Amenity]`, `free_cancellation`, `instant_book`, `guest_favorite`, `breakfast: bool`, `min_stars: int | None` (1–5), `beachfront: bool`. Lists deduped.
  - `RectangleArea(shape="rectangle", south, west, north, east)`, `CircleArea(shape="circle", lat, lon, radius_m)`, `DrawnArea` = discriminated union on `shape`.
  - `Hike.activity: int | None`, `Hike.skill: int | None`.
  - `HikeQuery.mode: Literal["place","near_home","drawn"]`, `HikeQuery.area: DrawnArea | None`, `HikeQuery.filters: HikeFilters`; drawn mode without area → `"Draw a rectangle or circle first"`.
  - `StaySearchQuery.filters: StayFilters`; `TripQuery.area: DrawnArea | None`, `TripQuery.hike_filters: HikeFilters`, `TripQuery.stay_filters: StayFilters`; `StayQuery.filters: StayFilters` (so the stays cache key, `sq.model_dump_json()`, includes filters).

- [ ] **Step 1: Write the failing tests**

In `tests/test_models.py` replace the import line

```python
from app.models import HikeQuery, StaySearchQuery, TripQuery
```

with

```python
from app.models import (
    CircleArea, HikeFilters, HikeQuery, RectangleArea, StayFilters, StayQuery, StaySearchQuery, TripQuery,
)
```

and append at the end of the file:

```python


BASE = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))


def test_filter_defaults_reproduce_v1():
    f = HikeFilters()
    assert f.activities == [1] and (f.difficulty_min, f.difficulty_max) == (1, 5)
    assert f.sort == "popular" and f.distance_max_km is None and f.min_trailrank is None
    s = StayFilters()
    assert s.price_max is None and s.amenities == [] and s.entire_place is False
    assert HikeQuery(**BASE).filters == HikeFilters()
    assert StaySearchQuery(**BASE, lat=39.4, lon=23.0).filters == StayFilters()
    t = TripQuery(**BASE)
    assert t.hike_filters == HikeFilters() and t.stay_filters == StayFilters() and t.area is None


@pytest.mark.parametrize("kwargs,message", [
    ({"difficulty_min": 4, "difficulty_max": 2}, "difficulty: minimum must not be greater than maximum"),
    ({"distance_min_km": 12, "distance_max_km": 5}, "distance: minimum"),
    ({"elevation_min_m": 900, "elevation_max_m": 300}, "elevation gain: minimum"),
])
def test_hike_filter_ranges_are_validated(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        HikeFilters(**kwargs)


def test_hike_filter_rejects_unknown_activity_empty_list_and_unknown_key():
    with pytest.raises(ValidationError):
        HikeFilters(activities=[41])
    with pytest.raises(ValidationError):
        HikeFilters(activities=[])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HikeFilters(min_rank=50)


def test_stay_filter_validation():
    with pytest.raises(ValidationError, match="price per night: minimum"):
        StayFilters(price_min=150, price_max=100)
    assert StayFilters(price_min=100, price_max=100).price_max == 100
    with pytest.raises(ValidationError):
        StayFilters(amenities=["sauna"])
    with pytest.raises(ValidationError):
        StayFilters(min_rating=6)


def test_filter_lists_are_deduplicated():
    assert StayFilters(amenities=["wifi", "pool", "wifi"]).amenities == ["wifi", "pool"]
    assert HikeFilters(activities=[1, 43, 1]).activities == [1, 43]


def test_drawn_mode_requires_area_and_parses_shapes():
    with pytest.raises(ValidationError, match="Draw a rectangle or circle first"):
        HikeQuery(**BASE, mode="drawn")
    q = HikeQuery(**BASE, mode="drawn", area={"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000})
    assert isinstance(q.area, CircleArea) and q.area.radius_m == 8000
    q = HikeQuery(**BASE, mode="drawn",
                  area={"shape": "rectangle", "south": 38.4, "west": 22.4, "north": 38.6, "east": 22.8})
    assert isinstance(q.area, RectangleArea)


@pytest.mark.parametrize("area", [
    {"shape": "rectangle", "south": 38.6, "west": 22.4, "north": 38.4, "east": 22.8},
    {"shape": "rectangle", "south": 38.4, "west": 22.8, "north": 38.6, "east": 22.4},
    {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 150},
    {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 300_001},
    {"shape": "polygon", "points": []},
])
def test_invalid_areas_are_rejected(area):
    with pytest.raises(ValidationError):
        HikeQuery(**BASE, mode="drawn", area=area)


def test_stay_query_carries_filters_into_its_json():
    q = StayQuery(lat=39.4, lon=23.0, radius_km=15, check_in=date(2026, 10, 23),
                  check_out=date(2026, 10, 25), guests=2, dog=True, filters={"price_max": 120})
    assert '"price_max":120' in q.model_dump_json()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_models.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'CircleArea' from 'app.models'`.

- [ ] **Step 3: Implement the models**

In `app/models.py`, replace

```python
from pydantic import BaseModel, Field, model_validator

Badge = Literal["pets", "pets_garden", "garden", "garden_unconfirmed"]
DriveLimit = Union[Annotated[int, Field(gt=0)], Literal["auto", "unlimited"]]
```

with

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Badge = Literal["pets", "pets_garden", "garden", "garden_unconfirmed"]
DriveLimit = Union[Annotated[int, Field(gt=0)], Literal["auto", "unlimited"]]
Activity = Literal[1, 43, 48, 57, 66, 60]  # Wikiloc picto ids offered as hike activities
PropertyType = Literal["house", "apartment", "guesthouse", "hotel", "villa"]
Amenity = Literal["wifi", "kitchen", "parking", "pool", "hot_tub", "ac", "washer", "ev_charger",
                  "fireplace", "heating", "workspace", "bbq"]


def _check_range(low, high, name: str) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError(f"{name}: minimum must not be greater than maximum")


def _unique(values: list) -> list:
    return list(dict.fromkeys(values))
```

Replace (end of `class Hike`)

```python
    drive_estimated: bool = False
    ferry_needed: bool = False
```

with

```python
    drive_estimated: bool = False
    ferry_needed: bool = False
    activity: int | None = None  # Wikiloc picto id (1 = Hiking)
    skill: int | None = None  # Wikiloc difficulty 1-5
```

Replace the whole `HikeQuery` class

```python
class HikeQuery(TripDates):
    mode: Literal["place", "near_home"] = "near_home"
    place: str | None = None
    drive_limit: DriveLimit = "auto"

    @model_validator(mode="after")
    def _place_required(self):
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        return self
```

with

```python
class HikeFilters(BaseModel):
    """Hike filters; the defaults reproduce v1 (hiking routes only, popular order)."""

    model_config = ConfigDict(extra="forbid")

    activities: list[Activity] = Field(default_factory=lambda: [1], min_length=1)
    difficulty_min: int = Field(default=1, ge=1, le=5)
    difficulty_max: int = Field(default=5, ge=1, le=5)
    distance_min_km: float | None = Field(default=None, ge=0)
    distance_max_km: float | None = Field(default=None, ge=0)
    elevation_min_m: int | None = Field(default=None, ge=0)
    elevation_max_m: int | None = Field(default=None, ge=0)
    min_trailrank: int | None = Field(default=None, ge=0, le=100)
    sort: Literal["popular", "top_rated"] = "popular"

    _dedupe = field_validator("activities")(_unique)

    @model_validator(mode="after")
    def _ranges(self):
        _check_range(self.difficulty_min, self.difficulty_max, "difficulty")
        _check_range(self.distance_min_km, self.distance_max_km, "distance")
        _check_range(self.elevation_min_m, self.elevation_max_m, "elevation gain")
        return self


class StayFilters(BaseModel):
    """Stay filters; every default means "no filter"."""

    model_config = ConfigDict(extra="forbid")

    price_min: int | None = Field(default=None, ge=0)  # EUR per night
    price_max: int | None = Field(default=None, ge=0)  # EUR per night
    min_rating: float | None = Field(default=None, ge=0, le=5)
    entire_place: bool = False
    property_types: list[PropertyType] = []
    min_bedrooms: int | None = Field(default=None, ge=1, le=10)
    min_beds: int | None = Field(default=None, ge=1, le=16)
    min_bathrooms: int | None = Field(default=None, ge=1, le=8)
    amenities: list[Amenity] = []
    free_cancellation: bool = False
    instant_book: bool = False
    guest_favorite: bool = False
    breakfast: bool = False
    min_stars: int | None = Field(default=None, ge=1, le=5)
    beachfront: bool = False

    _dedupe = field_validator("property_types", "amenities")(_unique)

    @model_validator(mode="after")
    def _ranges(self):
        _check_range(self.price_min, self.price_max, "price per night")
        return self


class RectangleArea(BaseModel):
    shape: Literal["rectangle"]
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _ordered(self):
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("drawn rectangle needs south < north and west < east")
        return self


class CircleArea(BaseModel):
    shape: Literal["circle"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(ge=200, le=300_000)


DrawnArea = Annotated[Union[RectangleArea, CircleArea], Field(discriminator="shape")]


class HikeQuery(TripDates):
    mode: Literal["place", "near_home", "drawn"] = "near_home"
    place: str | None = None
    drive_limit: DriveLimit = "auto"
    area: DrawnArea | None = None
    filters: HikeFilters = Field(default_factory=HikeFilters)

    @model_validator(mode="after")
    def _mode_inputs(self):
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        if self.mode == "drawn" and self.area is None:
            raise ValueError("Draw a rectangle or circle first")
        return self
```

In `StaySearchQuery` replace

```python
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True

    @model_validator(mode="after")
    def _needs_center(self):
```

with

```python
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    filters: StayFilters = Field(default_factory=StayFilters)

    @model_validator(mode="after")
    def _needs_center(self):
```

Replace the `TripQuery` class

```python
class TripQuery(TripDates):
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
```

with

```python
class TripQuery(TripDates):
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    area: DrawnArea | None = None  # when set, combos come from hikes inside the drawn shape
    hike_filters: HikeFilters = Field(default_factory=HikeFilters)
    stay_filters: StayFilters = Field(default_factory=StayFilters)
```

In `StayQuery` replace

```python
    guests: int
    dog: bool


class HikeSearch(BaseModel):
```

with

```python
    guests: int
    dog: bool
    filters: StayFilters = Field(default_factory=StayFilters)


class HikeSearch(BaseModel):
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_models.py -v`
Expected: all PASS (19 tests).
Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS (nothing else uses the new fields yet).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat(hick-toors): hike/stay filter and drawn-area models

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Filter catalogue and engine-side filtering (`app/filters.py`)

**Files:**
- Create: `app/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: `HikeFilters`, `StayFilters`, `Hike`, `Stay` from Task 1 (`Hike.activity`, `Hike.skill`).
- Produces (in `app.filters`):
  - `AIRBNB = "Airbnb"`, `BOOKING = "Booking.com"` (equal to the providers' `name`).
  - `catalogue() -> dict` — `{"hike": {"fields": [...], "defaults": HikeFilters().model_dump()}, "stay": {"fields": [...], "defaults": StayFilters().model_dump()}}`. Each field: `key`, `type` (`"multi" | "range" | "number" | "select" | "bool"`), `label`, `sources` (`None` | `"Airbnb only"` | `"Booking only"` | `"Airbnb + Booking"`); `multi`/`select` have `options: [{value, label}]` (amenity options also carry `sources`); `range` has `min_key`, `max_key` and either `options` or `min`/`max`/`step`/`unit`; `number` has `min`/`max`/`step`.
  - `hike_filters_active(f: HikeFilters) -> bool` (ignores `sort`), `stay_filters_active(f: StayFilters) -> bool`.
  - `apply_hike_filters(hikes: list[Hike], f: HikeFilters) -> list[Hike]`.
  - `stay_post_check(stays: list[Stay], f: StayFilters, nights: int) -> list[Stay]`.
  - `unsupported_filters(provider_name: str, f: StayFilters, dog: bool = False) -> list[str]` — labels; empty = provider can run.
  - `skip_notice(provider_name: str, labels: list[str]) -> str` → `"Booking.com skipped – it can't filter by Fireplace"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_filters.py`:

```python
import pytest

from app.filters import (
    apply_hike_filters, catalogue, hike_filters_active, skip_notice, stay_filters_active, stay_post_check,
    unsupported_filters,
)
from app.models import Hike, HikeFilters, Stay, StayFilters


def hike(id, activity=1, skill=2, km=10.0, gain=500, rank=50):
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=39.4, lon=23.0, activity=activity,
                skill=skill, distance_km=km, elevation_gain_m=gain, trailrank=rank)


def stay(id, price=200.0, rating=4.6):
    return Stay(id=f"airbnb:{id}", source="airbnb", name=str(id), url="u", lat=39.4, lon=23.0,
                total_price=price, rating=rating)


def ids(items):
    return [i.id.split(":")[1] for i in items]


def test_catalogue_shape_and_defaults():
    cat = catalogue()
    assert cat["hike"]["defaults"] == HikeFilters().model_dump()
    assert cat["stay"]["defaults"] == StayFilters().model_dump()
    hike_keys = [f["key"] for f in cat["hike"]["fields"]]
    assert hike_keys == ["activities", "difficulty", "distance", "elevation", "min_trailrank", "sort"]
    fields = {f["key"]: f for f in cat["stay"]["fields"]}
    assert fields["price"]["min_key"] == "price_min" and fields["price"]["unit"] == "€/night"
    assert fields["min_beds"]["sources"] == "Airbnb only"
    assert fields["breakfast"]["sources"] == "Booking only"
    assert fields["entire_place"]["sources"] == "Airbnb + Booking"
    amenity = {o["value"]: o for o in fields["amenities"]["options"]}
    assert amenity["fireplace"] == {"value": "fireplace", "label": "Fireplace", "sources": "Airbnb only"}
    assert amenity["wifi"]["sources"] == "Airbnb + Booking"
    # Every key the UI can set must be a real model field, so popups and models can't drift.
    model_keys = set(HikeFilters.model_fields) | set(StayFilters.model_fields)
    for kind in ("hike", "stay"):
        for f in cat[kind]["fields"]:
            keys = [f["min_key"], f["max_key"]] if f["type"] == "range" else [f["key"]]
            assert set(keys) <= model_keys, f


def test_hike_filters_active_ignores_sort():
    assert hike_filters_active(HikeFilters()) is False
    assert hike_filters_active(HikeFilters(sort="top_rated")) is False
    assert hike_filters_active(HikeFilters(min_trailrank=10)) is True
    assert hike_filters_active(HikeFilters(activities=[1, 43])) is True


def test_stay_filters_active():
    assert stay_filters_active(StayFilters()) is False
    assert stay_filters_active(StayFilters(beachfront=True)) is True


def test_default_hike_filters_keep_only_hiking_routes():
    hikes = [hike(1), hike(2, activity=43), hike(3, activity=None), hike(4, skill=None, km=None, gain=None, rank=None)]
    assert ids(apply_hike_filters(hikes, HikeFilters())) == ["1", "4"]


@pytest.mark.parametrize("filters,expected", [
    (HikeFilters(activities=[43, 66]), ["2"]),
    (HikeFilters(difficulty_min=2, difficulty_max=3), ["1", "3"]),
    (HikeFilters(distance_min_km=8, distance_max_km=12), ["1"]),
    (HikeFilters(distance_max_km=12), ["1", "3"]),
    (HikeFilters(elevation_min_m=400), ["1", "4"]),
    (HikeFilters(min_trailrank=60), ["4"]),
])
def test_apply_hike_filters_each_field(filters, expected):
    hikes = [hike(1), hike(2, activity=43, skill=1, km=20, gain=100, rank=40),
             hike(3, skill=3, km=5, gain=300, rank=55), hike(4, skill=5, km=30, gain=1500, rank=90)]
    assert ids(apply_hike_filters(hikes, filters)) == expected


def test_missing_value_is_dropped_only_when_that_field_is_filtered():
    bare = hike(9, skill=None, km=None, gain=None, rank=None)
    assert ids(apply_hike_filters([bare], HikeFilters(sort="top_rated"))) == ["9"]
    for f in (HikeFilters(difficulty_max=4), HikeFilters(distance_min_km=1),
              HikeFilters(elevation_max_m=2000), HikeFilters(min_trailrank=0)):
        assert apply_hike_filters([bare], f) == []


def test_stay_post_check_uses_price_per_night():
    stays = [stay(1, price=200.0), stay(2, price=300.0), stay(3, price=100.0), stay(4, price=None)]
    # 2 nights: 100/night, 150/night, 50/night, unknown (kept)
    assert ids(stay_post_check(stays, StayFilters(price_max=120), nights=2)) == ["1", "3", "4"]
    assert ids(stay_post_check(stays, StayFilters(price_min=60, price_max=120), nights=2)) == ["1", "4"]
    assert ids(stay_post_check(stays, StayFilters(price_max=120), nights=0)) == ["3", "4"]  # treated as 1 night


def test_stay_post_check_rating_keeps_unrated():
    stays = [stay(1, rating=4.9), stay(2, rating=4.2), stay(3, rating=None)]
    assert ids(stay_post_check(stays, StayFilters(min_rating=4.5), nights=1)) == ["1", "3"]
    assert ids(stay_post_check(stays, StayFilters(), nights=1)) == ["1", "2", "3"]


def test_unsupported_filters_per_provider():
    assert unsupported_filters("Airbnb", StayFilters()) == []
    assert unsupported_filters("Booking.com", StayFilters()) == []
    f = StayFilters(price_max=120, min_rating=4.5, entire_place=True, amenities=["parking"],
                    free_cancellation=True)
    assert unsupported_filters("Airbnb", f) == []
    assert unsupported_filters("Booking.com", f) == []
    f = StayFilters(amenities=["fireplace", "wifi"], min_beds=2, breakfast=True)
    assert unsupported_filters("Airbnb", f) == ["Breakfast included"]
    assert unsupported_filters("Booking.com", f) == ["Beds", "Fireplace"]
    assert unsupported_filters("Somewhere", StayFilters(beachfront=True)) == ["Beachfront"]
    assert unsupported_filters("Somewhere", StayFilters()) == []


def test_booking_cannot_require_two_values_of_one_facility_field():
    assert unsupported_filters("Booking.com", StayFilters(amenities=["parking"]), dog=False) == []
    assert unsupported_filters("Booking.com", StayFilters(amenities=["parking"]), dog=True) == [
        "Pets + Free parking together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["wifi", "pool"])) == [
        "Wifi + Pool together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["ac", "kitchen", "parking"])) == [
        "Air conditioning + Kitchen together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["pool"], beachfront=True)) == [
        "Pool + Beachfront together"]
    assert unsupported_filters("Airbnb", StayFilters(amenities=["wifi", "pool"]), dog=True) == []


def test_skip_notice_text():
    assert skip_notice("Booking.com", ["Fireplace"]) == "Booking.com skipped – it can't filter by Fireplace"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_filters.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'app.filters'`.

- [ ] **Step 3: Implement `app/filters.py`**

Create `app/filters.py`:

```python
"""Filter catalogue (served to the UI) and the filtering the engine does itself.

Wikiloc ignores filter params for anonymous callers, so every hike filter except `sort` is
applied here after fetching. Stay filters go to the providers as query params; price and rating
are re-checked here because providers apply them loosely.
"""

from app.models import Hike, HikeFilters, Stay, StayFilters

AIRBNB = "Airbnb"
BOOKING = "Booking.com"
BOTH = frozenset({AIRBNB, BOOKING})

ACTIVITY_OPTIONS = [(1, "Hiking"), (43, "Walking"), (48, "Trail running"), (57, "Nordic walking"),
                    (66, "Canicross (with dog)"), (60, "Via ferrata")]
DIFFICULTY_OPTIONS = [(1, "Easy"), (2, "Moderate"), (3, "Hard"), (4, "Very Hard"), (5, "Experts Only")]
PROPERTY_OPTIONS = [("house", "House"), ("apartment", "Apartment"), ("guesthouse", "Guesthouse / B&B"),
                    ("hotel", "Hotel"), ("villa", "Villa")]
RATING_OPTIONS = [(None, "Any"), (3.5, "3.5+"), (4.0, "4+"), (4.5, "4.5+"), (4.8, "4.8+")]
STAR_OPTIONS = [(None, "Any"), (1, "1+"), (2, "2+"), (3, "3+"), (4, "4+"), (5, "5")]

# amenity key -> (label, providers that can filter by it)
AMENITIES = {
    "wifi": ("Wifi", BOTH),
    "kitchen": ("Kitchen", BOTH),
    "parking": ("Free parking", BOTH),
    "pool": ("Pool", BOTH),
    "hot_tub": ("Hot tub", BOTH),
    "ac": ("Air conditioning", BOTH),
    "washer": ("Washer", BOTH),
    "ev_charger": ("EV charger", BOTH),
    "fireplace": ("Fireplace", frozenset({AIRBNB})),
    "heating": ("Heating", frozenset({AIRBNB})),
    "workspace": ("Workspace", frozenset({AIRBNB})),
    "bbq": ("BBQ grill", frozenset({AIRBNB})),
}

# stay field (or range group) -> (label, providers that honour it, server-side or by post-check)
STAY_FIELDS = {
    "price": ("Price per night", BOTH),
    "min_rating": ("Minimum rating", BOTH),
    "entire_place": ("Entire place", BOTH),
    "property_types": ("Property type", BOTH),
    "min_bedrooms": ("Bedrooms", BOTH),
    "min_beds": ("Beds", frozenset({AIRBNB})),
    "min_bathrooms": ("Bathrooms", BOTH),
    "free_cancellation": ("Free cancellation", BOTH),
    "instant_book": ("Instant book", frozenset({AIRBNB})),
    "guest_favorite": ("Guest favourite", frozenset({AIRBNB})),
    "breakfast": ("Breakfast included", frozenset({BOOKING})),
    "min_stars": ("Hotel stars", frozenset({BOOKING})),
    "beachfront": ("Beachfront", BOTH),
}

# Booking ORs repeated values of one nflt field, so only one value per field can be required.
BOOKING_HOTEL_FACILITY = {"parking", "wifi", "pool", "hot_tub", "ev_charger"}
BOOKING_ROOM_FACILITY = {"ac", "kitchen", "washer"}


def _source_tag(providers: frozenset) -> str:
    if providers == BOTH:
        return "Airbnb + Booking"
    return "Airbnb only" if AIRBNB in providers else "Booking only"


def _options(pairs, providers_by_value: dict | None = None) -> list[dict]:
    out = []
    for value, label in pairs:
        option = {"value": value, "label": label}
        if providers_by_value is not None:
            option["sources"] = _source_tag(providers_by_value[value])
        out.append(option)
    return out


def _stay(key: str, field: dict) -> dict:
    label, providers = STAY_FIELDS[key]
    return {"key": key, "label": label, "sources": _source_tag(providers), **field}


def catalogue() -> dict:
    """Everything the filter popups render: fields, options, defaults and source tags."""
    hike_fields = [
        {"key": "activities", "type": "multi", "label": "Activities", "sources": None,
         "options": _options(ACTIVITY_OPTIONS)},
        {"key": "difficulty", "type": "range", "label": "Difficulty", "sources": None,
         "min_key": "difficulty_min", "max_key": "difficulty_max", "options": _options(DIFFICULTY_OPTIONS)},
        {"key": "distance", "type": "range", "label": "Distance", "sources": None, "unit": "km",
         "min_key": "distance_min_km", "max_key": "distance_max_km", "min": 0, "max": 200, "step": 0.1},
        {"key": "elevation", "type": "range", "label": "Elevation gain", "sources": None, "unit": "m",
         "min_key": "elevation_min_m", "max_key": "elevation_max_m", "min": 0, "max": 5000, "step": 1},
        {"key": "min_trailrank", "type": "number", "label": "Minimum TrailRank", "sources": None,
         "min": 0, "max": 100, "step": 1},
        {"key": "sort", "type": "select", "label": "Wikiloc order", "sources": None,
         "options": _options([("popular", "Most popular"), ("top_rated", "Top rated (TrailRank)")])},
    ]
    stay_fields = [
        _stay("price", {"type": "range", "unit": "€/night", "min_key": "price_min", "max_key": "price_max",
                        "min": 0, "max": 2000, "step": 1}),
        _stay("min_rating", {"type": "select", "options": _options(RATING_OPTIONS)}),
        _stay("entire_place", {"type": "bool"}),
        _stay("property_types", {"type": "multi", "options": _options(PROPERTY_OPTIONS)}),
        _stay("min_bedrooms", {"type": "number", "min": 1, "max": 10, "step": 1}),
        _stay("min_beds", {"type": "number", "min": 1, "max": 16, "step": 1}),
        _stay("min_bathrooms", {"type": "number", "min": 1, "max": 8, "step": 1}),
        {"key": "amenities", "type": "multi", "label": "Amenities", "sources": None,
         "options": _options([(k, v[0]) for k, v in AMENITIES.items()],
                             {k: v[1] for k, v in AMENITIES.items()})},
        _stay("free_cancellation", {"type": "bool"}),
        _stay("instant_book", {"type": "bool"}),
        _stay("guest_favorite", {"type": "bool"}),
        _stay("breakfast", {"type": "bool"}),
        _stay("min_stars", {"type": "select", "options": _options(STAR_OPTIONS)}),
        _stay("beachfront", {"type": "bool"}),
    ]
    return {
        "hike": {"fields": hike_fields, "defaults": HikeFilters().model_dump()},
        "stay": {"fields": stay_fields, "defaults": StayFilters().model_dump()},
    }


def hike_filters_active(f: HikeFilters) -> bool:
    """True when a post-fetch hike filter narrows results (sort is applied by Wikiloc itself)."""
    return f.model_copy(update={"sort": "popular"}) != HikeFilters()


def stay_filters_active(f: StayFilters) -> bool:
    return f != StayFilters()


def _within(value, low, high) -> bool:
    if low is None and high is None:
        return True
    if value is None:  # the user asked for a range; an unknown value can't be shown as matching
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def apply_hike_filters(hikes: list[Hike], f: HikeFilters) -> list[Hike]:
    difficulty_filtered = (f.difficulty_min, f.difficulty_max) != (1, 5)
    out = []
    for h in hikes:
        if h.activity not in f.activities:
            continue
        if difficulty_filtered and not _within(h.skill, f.difficulty_min, f.difficulty_max):
            continue
        if not _within(h.distance_km, f.distance_min_km, f.distance_max_km):
            continue
        if not _within(h.elevation_gain_m, f.elevation_min_m, f.elevation_max_m):
            continue
        if not _within(h.trailrank, f.min_trailrank, None):
            continue
        out.append(h)
    return out


def stay_post_check(stays: list[Stay], f: StayFilters, nights: int) -> list[Stay]:
    """Enforce price per night and minimum rating; a stay with the value missing is kept."""
    nights = max(nights, 1)
    out = []
    for s in stays:
        if s.total_price is not None:
            per_night = s.total_price / nights
            if f.price_min is not None and per_night < f.price_min:
                continue
            if f.price_max is not None and per_night > f.price_max:
                continue
        if f.min_rating is not None and s.rating is not None and s.rating < f.min_rating:
            continue
        out.append(s)
    return out


def _active_fields(f: StayFilters) -> list[str]:
    defaults = StayFilters()
    keys = []
    for key in STAY_FIELDS:
        if key == "price":
            if f.price_min is not None or f.price_max is not None:
                keys.append(key)
        elif getattr(f, key) != getattr(defaults, key):
            keys.append(key)
    return keys


def unsupported_filters(provider_name: str, f: StayFilters, dog: bool = False) -> list[str]:
    """Labels of the active filters this provider can't honour (empty list = provider can run)."""
    labels = [STAY_FIELDS[k][0] for k in _active_fields(f) if provider_name not in STAY_FIELDS[k][1]]
    labels += [AMENITIES[a][0] for a in f.amenities if provider_name not in AMENITIES[a][1]]
    if provider_name == BOOKING:
        hotel = [AMENITIES[a][0] for a in f.amenities if a in BOOKING_HOTEL_FACILITY]
        if f.beachfront:
            hotel.append("Beachfront")
        if dog and hotel:
            hotel.insert(0, "Pets")
        room = [AMENITIES[a][0] for a in f.amenities if a in BOOKING_ROOM_FACILITY]
        for group in (hotel, room):
            if len(group) > 1:
                labels.append(" + ".join(group) + " together")
    return labels


def skip_notice(provider_name: str, labels: list[str]) -> str:
    return f"{provider_name} skipped – it can't filter by {', '.join(labels)}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_filters.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/filters.py tests/test_filters.py
git commit -m "feat(hick-toors): filter catalogue, hike filtering, stay post-check, provider support rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wikiloc sort/pages, activities kept in parse, engine applies the activity filter

**Files:**
- Modify: `app/sources/wikiloc.py`
- Modify: `app/engine.py` (three small edits: import, cache key, apply filters)
- Test: `tests/test_wikiloc.py`, `tests/test_engine.py`

**Contract change (deliberate):** `parse_search` no longer drops non-hiking pictos. The v1 test `test_parse_search_keeps_only_greek_hiking_routes` is replaced by `test_parse_search_keeps_greek_routes_of_every_activity` (Greece-only filtering stays). To keep v1 behaviour end to end, the engine now applies `apply_hike_filters(hikes, q.filters)` (default = Hiking only) in the same task, and the `hike()` builder in `tests/test_engine.py` gains `activity=1` so existing engine tests keep their meaning. The hike cache key gets a `v2` segment because v1 cache entries (7-day TTL) hold hikes without `activity`, which the new filter would drop entirely.

**Interfaces:**
- Consumes: `Hike.activity`, `Hike.skill`, `HikeQuery.filters` (Task 1); `apply_hike_filters` (Task 2).
- Produces: `WikilocSource.search(bbox: BBox, query: str = "", sort: str = "popular", pages: int | None = None) -> list[Hike]` — sends `sort=trailrank` only when `sort == "top_rated"`; `pages` overrides the constructor page count. `parse_search` sets `Hike.activity` (picto int or None) and `Hike.skill` (1–5 or None). `HIKING_PICTO` constant removed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_wikiloc.py`, in `test_parse_search_maps_fields` replace

```python
    assert hike.difficulty == "Hard"
    assert hike.trailrank == 89
```

with

```python
    assert hike.difficulty == "Hard" and hike.skill == 3
    assert hike.activity == 1
    assert hike.trailrank == 89
```

Replace the whole test

```python
def test_parse_search_keeps_only_greek_hiking_routes():
    data = {"spas": [
        wikiloc_spa(1, "Greek script", 39.3, 22.9, near="Miléai, Θεσσαλία (Ελλάδα)"),
        wikiloc_spa(2, "English", 39.3, 22.9, near="Portariá, Thessaly (Greece)"),
        wikiloc_spa(3, "Spanish", 39.3, 22.9, near="Volos, Tesalia (Grecia)"),
        wikiloc_spa(4, "Diving", 39.3, 22.9, picto=41),
        wikiloc_spa(5, "Turkish coast", 39.3, 26.7, near="Ayvalık, Balıkesir (Türkiye)"),
    ]}
    assert [h.id for h in parse_search(data)] == ["wikiloc:1", "wikiloc:2", "wikiloc:3"]
```

with

```python
def test_parse_search_keeps_greek_routes_of_every_activity():
    # v2 contract change: activities are filtered in the engine (HikeFilters.activities), so
    # parse_search keeps non-hiking pictos and records them in Hike.activity. Greece-only stays.
    data = {"spas": [
        wikiloc_spa(1, "Greek script", 39.3, 22.9, near="Miléai, Θεσσαλία (Ελλάδα)"),
        wikiloc_spa(2, "English", 39.3, 22.9, near="Portariá, Thessaly (Greece)"),
        wikiloc_spa(3, "Spanish", 39.3, 22.9, near="Volos, Tesalia (Grecia)"),
        wikiloc_spa(4, "Diving", 39.3, 22.9, picto=41),
        wikiloc_spa(5, "Turkish coast", 39.3, 26.7, near="Ayvalık, Balıkesir (Türkiye)"),
    ]}
    hikes = parse_search(data)
    assert [h.id for h in hikes] == ["wikiloc:1", "wikiloc:2", "wikiloc:3", "wikiloc:4"]
    assert [h.activity for h in hikes] == [1, 1, 1, 41]


def test_parse_search_ignores_unknown_skill():
    [hike] = parse_search({"spas": [wikiloc_spa(1, "Odd", 39.3, 22.9, skill=9)]})
    assert hike.skill is None and hike.difficulty is None
```

Insert directly above `async def test_search_raises_when_first_page_fails():`

```python
async def test_search_sort_param_only_for_top_rated():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return {"spas": []}

    source = WikilocSource(fetch=fetch)
    await source.search(BOX)
    await source.search(BOX, sort="top_rated")
    assert "sort" not in calls[0]
    assert calls[1]["sort"] == "trailrank"


async def test_search_pages_override():
    calls = []

    async def fetch(url, params):
        calls.append(params["from"])
        return {"spas": [wikiloc_spa(params["from"] + i, "r", 39.3, 22.9) for i in range(25)]}

    source = WikilocSource(fetch=fetch, pages=2)
    assert len(await source.search(BOX)) == 50
    calls.clear()
    assert len(await source.search(BOX, pages=4)) == 100
    assert calls == [0, 25, 50, 75]


```

In `tests/test_engine.py` replace

```python
def hike(id, trailrank, lat=39.4, lon=23.0):
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=lat, lon=lon, trailrank=trailrank)
```

with

```python
def hike(id, trailrank, lat=39.4, lon=23.0, activity=1, distance_km=None):
    # activity=1 (Hiking): the engine now applies the default activity filter itself.
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=lat, lon=lon, trailrank=trailrank,
                activity=activity, distance_km=distance_km)
```

and append at the end of `tests/test_engine.py`:

```python


# ---- v2: filters ----

async def test_default_filters_drop_non_hiking_routes(settings):
    hikes = [hike(1, 50, lat=38.1), hike(2, 90, lat=38.1, activity=41)]
    engine = make_engine(settings, wikiloc=FakeWikiloc(hikes), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_v1_hike_cache_entries_are_not_reused(settings):
    # v1 cached hiking-only routes without `activity`; reusing them would now filter them all out.
    from app.models import BBox
    box = BBox(south=39.3, west=22.9, north=39.5, east=23.2)
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(2, 90, lat=39.4)]), router=FakeRouter({39.4: 10}),
                         geocoder=FakeGeocoder(box))
    engine.cache.set("hikes:39.300,22.900,39.500,23.200",
                     [hike(7, 99, lat=39.4, activity=None).model_dump(mode="json")], 3600)
    result = await engine.search_hikes(HikeQuery(start=FRI, end=FRI, mode="place", place="Pelion"))
    assert [h.id for h in result.hikes] == ["wikiloc:2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_wikiloc.py tests/test_engine.py -v`
Expected: FAIL — `test_parse_search_maps_fields` (`hike.skill` is None), `test_parse_search_keeps_greek_routes_of_every_activity` (diving dropped), `test_search_sort_param_only_for_top_rated` / `test_search_pages_override` (`TypeError: ... unexpected keyword argument`), `test_default_filters_drop_non_hiking_routes` (diving kept), `test_v1_hike_cache_entries_are_not_reused` (stale `wikiloc:7` returned).

- [ ] **Step 3: Implement**

In `app/sources/wikiloc.py` replace

```python
PAGE_SIZE = 25
HIKING_PICTO = 1
GREECE_MARKERS = ("Greece", "Ελλάδα", "Grecia")
```

with

```python
PAGE_SIZE = 25
GREECE_MARKERS = ("Greece", "Ελλάδα", "Grecia")
```

Replace the whole `parse_search` function

```python
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
```

with

```python
def parse_search(data: dict) -> list[Hike]:
    """Every Greek route of any activity; the engine filters activities (HikeFilters.activities)."""
    hikes = []
    for spa in data.get("spas") or []:
        if not _in_greece(spa.get("near")):
            continue
        if spa.get("lat") is None or spa.get("lon") is None:
            continue
        slope = _num(spa.get("slope"))
        thumbs = spa.get("thumbs") or []
        skill = spa.get("skill")
        hikes.append(
            Hike(
                id=f"wikiloc:{spa['id']}",
                name=spa.get("name") or "Unnamed route",
                url=f"{BASE_URL}{spa.get('prettyURL', '')}",
                lat=spa["lat"],
                lon=spa["lon"],
                distance_km=_num(spa.get("distance")),
                elevation_gain_m=int(slope) if slope is not None else None,
                difficulty=SKILL.get(skill),
                trailrank=spa.get("trailrank"),
                near=spa.get("near"),
                thumbnail=thumbs[0].get("url") if thumbs else None,
                activity=spa.get("picto") if isinstance(spa.get("picto"), int) else None,
                skill=skill if skill in SKILL else None,
            )
        )
    return hikes
```

In `WikilocSource` replace

```python
    async def search(self, bbox: BBox, query: str = "") -> list[Hike]:
        hikes: list[Hike] = []
        seen: set[str] = set()
        for page in range(self._pages):
```

with

```python
    async def search(self, bbox: BBox, query: str = "", sort: str = "popular",
                     pages: int | None = None) -> list[Hike]:
        """Routes in bbox. sort="top_rated" asks Wikiloc for its TrailRank order (the only
        server-side filter that works anonymously); pages overrides the configured page count."""
        hikes: list[Hike] = []
        seen: set[str] = set()
        for page in range(pages or self._pages):
```

and replace

```python
                "q": query,
            }
            data = await self._fetch(SEARCH_URL, params)
```

with

```python
                "q": query,
            }
            if sort == "top_rated":
                params["sort"] = "trailrank"
            data = await self._fetch(SEARCH_URL, params)
```

In `app/engine.py` replace

```python
from app.dog_rule import apply_dog_rule, badge_tier
```

with

```python
from app.dog_rule import apply_dog_rule, badge_tier
from app.filters import apply_hike_filters
```

replace

```python
    async def _wikiloc_cached(self, box) -> list[Hike]:
        key = f"hikes:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
```

with

```python
    async def _wikiloc_cached(self, box) -> list[Hike]:
        # v2 key: entries now hold every activity (v1 entries were hiking-only, without `activity`).
        key = f"hikes:v2:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
```

and replace

```python
        try:
            hikes = await self._wikiloc_cached(box)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
```

with

```python
        try:
            hikes = await self._wikiloc_cached(box)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
        hikes = apply_hike_filters(hikes, q.filters)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_wikiloc.py tests/test_engine.py -v`
Expected: all PASS.
Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sources/wikiloc.py app/engine.py tests/test_wikiloc.py tests/test_engine.py
git commit -m "feat(hick-toors): wikiloc sort/pages; activities filtered in engine, not parse

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Airbnb filter → params

**Files:**
- Modify: `app/sources/stays/airbnb.py`
- Test: `tests/test_airbnb.py`

**Interfaces:**
- Consumes: `StayFilters`, `StayQuery.filters` (Task 1).
- Produces: `app.sources.stays.airbnb.filter_params(f: StayFilters) -> dict` (list values = repeated query keys, which httpx encodes as `key=a&key=b`); `AirbnbProvider._search_params` merges it, so both the pets and the no-pets searches carry the filters. Default filters add no params (v1 requests unchanged).

- [ ] **Step 1: Write the failing tests**

In `tests/test_airbnb.py` replace

```python
import pytest

from app.models import StayQuery
from app.sources.stays.airbnb import AirbnbParseError, AirbnbProvider, parse_details, parse_search
```

with

```python
import httpx
import pytest

from app.models import StayFilters, StayQuery
from app.sources.stays.airbnb import (
    AirbnbParseError, AirbnbProvider, filter_params, parse_details, parse_search,
)
```

Insert directly above `async def test_dog_search_keeps_pet_results_when_no_pets_search_fails():`

```python
def test_filter_params_maps_every_supported_filter():
    f = StayFilters(price_min=60, price_max=120, min_rating=4.5, entire_place=True,
                    property_types=["villa", "apartment", "house"], min_bedrooms=2, min_beds=3,
                    min_bathrooms=1, amenities=["parking", "wifi", "fireplace"], free_cancellation=True,
                    instant_book=True, guest_favorite=True, beachfront=True)
    assert filter_params(f) == {
        "price_min": 60, "price_max": 120,
        "room_types[]": ["Entire home/apt"],
        "l2_property_type_ids[]": [1, 3],
        "min_bedrooms": 2, "min_beds": 3, "min_bathrooms": 1,
        "amenities[]": [4, 9, 27],
        "guest_favorite": "true", "ib": "true", "flexible_cancellation": "true",
        "kg_and_tags[]": ["Tag:789"],
    }


def test_default_filters_add_no_params():
    assert filter_params(StayFilters()) == {}


def test_list_params_encode_as_repeated_keys():
    params = filter_params(StayFilters(amenities=["wifi", "kitchen"]))
    url = httpx.Request("GET", "https://www.airbnb.com/s/homes", params=params).url
    assert parse_qs(url.query.decode())["amenities[]"] == ["4", "8"]


async def test_filters_reach_both_dog_searches():
    fetch = FakeFetch(
        search_with_pets=airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)]),
        search_without=airbnb_search_html([]),
        details={1: airbnb_listing_html(["Pets allowed"])},
    )
    await AirbnbProvider(fetch=fetch).search(Q.model_copy(update={"filters": StayFilters(price_max=120)}))
    searches = [params for url, params in fetch.calls if url.endswith("/s/homes")]
    assert len(searches) == 2 and all(p["price_max"] == 120 for p in searches)


```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_airbnb.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'filter_params'`.

- [ ] **Step 3: Implement**

In `app/sources/stays/airbnb.py` replace

```python
from app.models import LatLon, Stay, StayQuery
```

with

```python
from app.models import LatLon, Stay, StayFilters, StayQuery
```

replace

```python
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}
```

with

```python
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}
# Verified search params (probed 2026-10-10); villa has no own type on Airbnb, so it maps to House.
PROPERTY_TYPE_IDS = {"house": 1, "guesthouse": 2, "apartment": 3, "hotel": 4, "villa": 1}
AMENITY_IDS = {"wifi": 4, "ac": 5, "pool": 7, "kitchen": 8, "parking": 9, "hot_tub": 25, "fireplace": 27,
               "heating": 30, "washer": 33, "workspace": 47, "ev_charger": 97, "bbq": 99}
BEACHFRONT_TAG = "Tag:789"
```

insert directly above `def parse_details(html: str) -> tuple[bool | None, bool]:`

```python
def filter_params(f: StayFilters) -> dict:
    """StayFilters -> Airbnb search params (all AND together). List values become repeated keys.

    min_rating, breakfast and min_stars have no Airbnb param: rating is post-checked by the
    engine, and the other two make the engine skip Airbnb.
    """
    params: dict = {}
    if f.price_min is not None:
        params["price_min"] = f.price_min
    if f.price_max is not None:
        params["price_max"] = f.price_max
    if f.entire_place:
        params["room_types[]"] = ["Entire home/apt"]
    if f.property_types:
        params["l2_property_type_ids[]"] = sorted({PROPERTY_TYPE_IDS[t] for t in f.property_types})
    for key in ("min_bedrooms", "min_beds", "min_bathrooms"):
        if getattr(f, key) is not None:
            params[key] = getattr(f, key)
    if f.amenities:
        params["amenities[]"] = sorted(AMENITY_IDS[a] for a in f.amenities)
    if f.guest_favorite:
        params["guest_favorite"] = "true"
    if f.instant_book:
        params["ib"] = "true"
    if f.free_cancellation:
        params["flexible_cancellation"] = "true"
    if f.beachfront:
        params["kg_and_tags[]"] = [BEACHFRONT_TAG]
    return params


```

and in `AirbnbProvider._search_params` replace

```python
            "adults": q.guests, "currency": "EUR",
        }
```

with

```python
            "adults": q.guests, "currency": "EUR",
            **filter_params(q.filters),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_airbnb.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sources/stays/airbnb.py tests/test_airbnb.py
git commit -m "feat(hick-toors): translate stay filters to Airbnb search params

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Booking filter → `nflt`, en-gb endpoint, fresh session per attempt

**Files:**
- Modify: `app/sources/stays/booking.py`
- Test: `tests/test_booking.py`

**Interfaces:**
- Consumes: `StayFilters`, `StayQuery.filters` (Task 1).
- Produces: `review_score(min_rating: float) -> int | None`; `nflt_tokens(f: StayFilters, dog: bool) -> list[str]` (pets token first, so dog-only stays exactly `"hotelfacility=4"` as in v1); `BookingProvider.search` sends `nflt=";".join(tokens)` (omitted when empty) to `SEARCH_URL = "https://www.booking.com/searchresults.en-gb.html"` with `lang=en-gb`; default `retries=4`. `default_fetch` already opens a new `AsyncSession` per call, i.e. a fresh session per attempt — keep that and document it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_booking.py` replace

```python
from app.models import StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import BookingBlocked, BookingProvider, parse_search
```

with

```python
from app.models import StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import (
    BookingBlocked, BookingProvider, nflt_tokens, parse_search, review_score,
)
```

Insert directly above `async def test_retries_after_challenge_then_succeeds():`

```python
def test_nflt_tokens_map_every_supported_filter():
    f = StayFilters(price_min=60, price_max=120, min_rating=4.5, entire_place=True,
                    property_types=["house", "guesthouse"], min_bedrooms=7, min_bathrooms=2,
                    amenities=["parking", "ac"], free_cancellation=True, breakfast=True, min_stars=4,
                    beachfront=True)
    assert nflt_tokens(f, dog=False) == [
        "price=EUR-60-120-1", "review_score=90", "privacy_type=3",
        "ht_id=208", "ht_id=216", "ht_id=220", "ht_id=223",
        "entire_place_bedroom_count=5", "min_bathrooms=2",
        "hotelfacility=2", "roomfacility=11",
        "fc=2", "mealplan=1", "class=4", "class=5", "hotelfacility=146",
    ]


@pytest.mark.parametrize("low,high,token", [
    (None, 100, "price=EUR-min-100-1"),
    (80, None, "price=EUR-80-max-1"),
])
def test_open_price_ends_use_literal_words(low, high, token):
    assert nflt_tokens(StayFilters(price_min=low, price_max=high), dog=False) == [token]


@pytest.mark.parametrize("rating,score", [(3.5, 70), (4.0, 80), (4.5, 90), (4.8, 90), (2.5, None)])
def test_review_score_floors_to_step(rating, score):
    assert review_score(rating) == score


async def test_filters_join_pets_token_and_use_en_gb_endpoint():
    calls = []

    async def fetch(url, params):
        calls.append((url, params))
        return 200, booking_search_html([])

    q = Q.model_copy(update={"filters": StayFilters(price_max=120, free_cancellation=True)})
    await BookingProvider(fetch=fetch, sleep=no_sleep).search(q)
    url, params = calls[0]
    assert url == "https://www.booking.com/searchresults.en-gb.html"
    assert params["nflt"] == "hotelfacility=4;price=EUR-min-120-1;fc=2"


async def test_each_attempt_is_a_separate_fetch_call():
    # default_fetch opens a fresh curl_cffi session per call, so retrying = fresh session.
    calls = []

    async def fetch(url, params):
        calls.append(url)
        return (202, "") if len(calls) < 3 else (200, booking_search_html([]))

    assert await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q) == []
    assert len(calls) == 3


```

(The existing `test_search_with_dog_uses_pets_filter_and_marks_pets` keeps asserting `nflt == "hotelfacility=4"` and `test_search_without_dog_has_no_filter` keeps asserting no `nflt` — both must still pass.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_booking.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'nflt_tokens'`.

- [ ] **Step 3: Implement**

In `app/sources/stays/booking.py` replace

```python
from app.models import Stay, StayQuery
from app.sources.stays.base import ProviderUnavailable

SEARCH_URL = "https://www.booking.com/searchresults.html"
PETS_FILTER = "hotelfacility=4"
```

with

```python
from app.models import Stay, StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable

# The plain /searchresults.html is always challenged; the en-gb page passes about half the time.
SEARCH_URL = "https://www.booking.com/searchresults.en-gb.html"
PETS_FILTER = "hotelfacility=4"
# Verified nflt tokens (probed 2026-10-10). Repeating a field ORs its values; fields AND together.
PROPERTY_TYPE_IDS = {"house": [220, 223], "apartment": [201], "guesthouse": [216, 208], "hotel": [204],
                     "villa": [213]}
FACILITY_TOKENS = {"parking": "hotelfacility=2", "wifi": "hotelfacility=107", "pool": "hotelfacility=433",
                   "hot_tub": "hotelfacility=63", "ev_charger": "hotelfacility=182",
                   "ac": "roomfacility=11", "kitchen": "roomfacility=999", "washer": "roomfacility=34"}
BEACHFRONT = "hotelfacility=146"
```

replace

```python
async def default_fetch(url: str, params: dict) -> tuple[int, str]:
    from curl_cffi.requests import AsyncSession
```

with

```python
def review_score(min_rating: float) -> int | None:
    """0-5 rating -> Booking review_score step (60/70/80/90), floored; below 3.0 no token."""
    score = int(round(min_rating * 20, 6)) // 10 * 10
    return min(score, 90) if score >= 60 else None


def nflt_tokens(f: StayFilters, dog: bool) -> list[str]:
    """StayFilters (+ dog) -> Booking nflt tokens. min_beds, instant_book, guest_favorite and the
    Airbnb-only amenities have no token; the engine skips Booking when they are active."""
    tokens = [PETS_FILTER] if dog else []
    if f.price_min is not None or f.price_max is not None:
        low = f.price_min if f.price_min is not None else "min"
        high = f.price_max if f.price_max is not None else "max"
        tokens.append(f"price=EUR-{low}-{high}-1")
    if f.min_rating is not None and review_score(f.min_rating) is not None:
        tokens.append(f"review_score={review_score(f.min_rating)}")
    if f.entire_place:
        tokens.append("privacy_type=3")
    ht_ids = sorted({i for t in f.property_types for i in PROPERTY_TYPE_IDS[t]})
    tokens += [f"ht_id={i}" for i in ht_ids]
    if f.min_bedrooms is not None:
        tokens.append(f"entire_place_bedroom_count={min(f.min_bedrooms, 5)}")
    if f.min_bathrooms is not None:
        tokens.append(f"min_bathrooms={f.min_bathrooms}")
    tokens += [FACILITY_TOKENS[a] for a in f.amenities if a in FACILITY_TOKENS]
    if f.free_cancellation:
        tokens.append("fc=2")
    if f.breakfast:
        tokens.append("mealplan=1")
    if f.min_stars is not None:
        tokens += [f"class={n}" for n in range(f.min_stars, 6)]
    if f.beachfront:
        tokens.append(BEACHFRONT)
    return tokens


async def default_fetch(url: str, params: dict) -> tuple[int, str]:
    """One request in a brand-new impersonated session: each retry gets a fresh fingerprint."""
    from curl_cffi.requests import AsyncSession
```

replace

```python
    def __init__(self, fetch: FetchStatus | None = None, retries: int = 3,
```

with

```python
    def __init__(self, fetch: FetchStatus | None = None, retries: int = 4,
```

and replace

```python
            "lang": "en-us", "selected_currency": "EUR",
        }
        if q.dog:
            params["nflt"] = PETS_FILTER
```

with

```python
            "lang": "en-gb", "selected_currency": "EUR",
        }
        tokens = nflt_tokens(q.filters, q.dog)
        if tokens:
            params["nflt"] = ";".join(tokens)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_booking.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sources/stays/booking.py tests/test_booking.py
git commit -m "feat(hick-toors): Booking nflt filters, en-gb endpoint, more retries

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Engine — page count, provider skipping, post-checks, drawn area, Trip plan with area

**Files:**
- Modify: `app/config.py`
- Modify: `app/engine.py` (full replacement below)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Task 1 models; Task 2 `apply_hike_filters`, `hike_filters_active`, `stay_filters_active`, `stay_post_check`, `unsupported_filters`, `skip_notice`; Task 3 `wikiloc.search(box, sort=..., pages=...)`; `app.geo.ensure_min_size`.
- Produces:
  - `Settings.hike_pages_filtered: int = 10`.
  - `app.engine.area_search_box(area) -> BBox | None` (clamped to Greece, widened to `MIN_AREA_RADIUS_KM = 5.0`; `None` when the shape misses Greece), `app.engine.in_area(h: Hike, area) -> bool`.
  - Constants `NO_HIKES_MATCH`, `NO_STAYS_MATCH`, `NO_SOURCE`, `OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"`.
  - `Engine.search_hikes` handles `mode == "drawn"` (no drive-limit filtering, like place mode), fetches `hike_pages_filtered` pages when `hike_filters_active`, hike cache key `hikes:v2:{sort}:{pages}:{bbox}`.
  - `Engine.search_stays` skips providers with `unsupported_filters(p.name, q.filters, dog=q.dog)`, post-checks with nights = `(check_out - check_in).days`, passes filters in `StayQuery`.
  - `Engine.plan_trip` uses `mode="drawn"` + `area` when `q.area` is set, passes `hike_filters` / `stay_filters`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py` replace

```python
from app.models import Hike, HikeQuery, LatLon, Stay, StaySearchQuery, TripQuery
```

with

```python
from app.models import BBox, Hike, HikeQuery, LatLon, Stay, StaySearchQuery, TripQuery
```

replace

```python
class FakeWikiloc:
    def __init__(self, hikes=None, fail=False):
        self.hikes, self.fail, self.boxes = hikes or [], fail, []

    async def search(self, bbox, query=""):
        self.boxes.append(bbox)
```

with

```python
class FakeWikiloc:
    def __init__(self, hikes=None, fail=False):
        self.hikes, self.fail, self.boxes, self.calls = hikes or [], fail, [], []

    async def search(self, bbox, query="", sort="popular", pages=None):
        self.boxes.append(bbox)
        self.calls.append({"sort": sort, "pages": pages})
```

and append at the end of the file (after the Task 3 tests):

```python


async def test_filtered_search_fetches_more_pages_and_passes_sort(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"sort": "top_rated"}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10}))
    assert wikiloc.calls == [{"sort": "popular", "pages": 6}, {"sort": "top_rated", "pages": 6},
                             {"sort": "popular", "pages": 10}]


async def test_hike_cache_is_shared_by_post_fetch_filters(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1, distance_km=8.0), hike(2, 90, lat=38.1, distance_km=20.0)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    a = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"distance_max_km": 10}))
    b = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"distance_min_km": 10}))
    assert len(wikiloc.calls) == 1  # same sort + page count -> same raw Wikiloc fetch
    assert [h.id for h in a.hikes] == ["wikiloc:1"] and [h.id for h in b.hikes] == ["wikiloc:2"]


async def test_hike_filters_leaving_nothing_give_notice(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 95}))
    assert result.hikes == [] and result.warnings == ["No hikes match your filters"]


# ---- v2: drawn area ----

def drawn(area, **kw):
    return HikeQuery(start=FRI, end=SUN, mode="drawn", area=area, **kw)


async def test_drawn_rectangle_searches_its_box_without_drive_limit(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.5, lon=22.6)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.5: 999}))
    rect = {"shape": "rectangle", "south": 38.3, "west": 22.3, "north": 38.7, "east": 22.9}
    result = await engine.search_hikes(drawn(rect))
    assert wikiloc.boxes == [BBox(south=38.3, west=22.3, north=38.7, east=22.9)]
    assert [h.id for h in result.hikes] == ["wikiloc:1"] and result.drive_limit_minutes is None


async def test_drawn_circle_drops_hikes_outside_radius(settings):
    inside, corner = hike(1, 50, lat=39.03, lon=22.02), hike(2, 90, lat=39.08, lon=22.10)
    engine = make_engine(settings, wikiloc=FakeWikiloc([inside, corner]),
                         router=FakeRouter({39.03: 100, 39.08: 100}))
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000}))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_circle_crossing_greece_border_is_clamped_but_keeps_greek_hikes(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=41.75, lon=24.0)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({41.75: 200}))
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 41.7, "lon": 24.0, "radius_m": 30_000}))
    box = wikiloc.boxes[0]
    assert box.north == 41.8 and box.south == pytest.approx(41.7 - 30 / 111.32)
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_area_outside_greece_warns_without_fetching(settings):
    wikiloc = FakeWikiloc([hike(1, 50)])
    engine = make_engine(settings, wikiloc=wikiloc)
    rome = {"shape": "rectangle", "south": 41.0, "west": 12.0, "north": 42.0, "east": 13.0}
    result = await engine.search_hikes(drawn(rome))
    assert result.hikes == [] and result.warnings == ["The drawn area is outside Greece – draw it over Greece"]
    assert wikiloc.boxes == []


async def test_tiny_shapes_are_widened_to_five_km(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.52, lon=22.5)])  # ~2.2 km north of the centre
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.52: 100}))
    tiny_rect = {"shape": "rectangle", "south": 38.5, "west": 22.5, "north": 38.505, "east": 22.505}
    await engine.search_hikes(drawn(tiny_rect))
    box = wikiloc.boxes[0]
    assert box.north - box.south == pytest.approx(2 * 5 / 111.32)
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 38.5, "lon": 22.5, "radius_m": 300}))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


# ---- v2: stay filters ----

async def test_unsupported_filter_skips_provider_with_notice(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", [stay(2, "booking")])
    engine = make_engine(settings, providers=[airbnb, booking])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"amenities": ["fireplace"]}))
    assert result.warnings == ["Booking.com skipped – it can't filter by Fireplace"]
    assert booking.queries == [] and airbnb.queries[0].filters.amenities == ["fireplace"]
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_every_provider_skipped_gives_no_source_notice(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", [stay(2, "booking")])
    engine = make_engine(settings, providers=[airbnb, booking])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"min_beds": 2, "breakfast": True}))
    assert result.stays == [] and airbnb.queries == [] and booking.queries == []
    assert result.warnings == ["Airbnb skipped – it can't filter by Breakfast included",
                               "Booking.com skipped – it can't filter by Beds",
                               "No source supports this combination of filters"]


async def test_price_post_check_is_per_night(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, price=200.0), stay(2, price=300.0)])  # 2 nights: 100 / 150
    engine = make_engine(settings, providers=[airbnb])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"price_max": 120}))
    assert [s.id for s in result.stays] == ["airbnb:1"] and result.warnings == []


async def test_stay_filters_leaving_nothing_give_notice(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, price=200.0)])
    engine = make_engine(settings, providers=[airbnb])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"price_max": 50}))
    assert result.stays == [] and result.warnings == ["No stays match your filters"]


async def test_stay_cache_key_includes_filters(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    base = dict(start=FRI, end=SUN, lat=39.4, lon=23.0)
    for filters in ({"price_max": 120}, {"price_max": 200}, {"price_max": 120}):
        await engine.search_stays(StaySearchQuery(**base, filters=filters))
    assert [q.filters.price_max for q in airbnb.queries] == [120, 200]


async def test_plan_trip_with_area_and_filters(settings):
    inside, outside = hike(1, 50, lat=39.03, lon=22.02), hike(2, 99, lat=39.3, lon=22.4)
    wikiloc = FakeWikiloc([inside, outside])
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.031, lon=22.021, price=100)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb],
                         router=FakeRouter({39.03: 500, 39.3: 500}))
    result = await engine.plan_trip(TripQuery(
        start=FRI, end=SUN, area={"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000},
        hike_filters={"min_trailrank": 10}, stay_filters={"price_max": 120}))
    assert [c.hike.id for c in result.combos] == ["wikiloc:1"]  # 500 min drive ignored inside a shape
    assert wikiloc.boxes[0].north == pytest.approx(39.0 + 10 / 111.32)
    assert wikiloc.calls[0]["pages"] == 10 and airbnb.queries[0].filters.price_max == 120
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_engine.py -v`
Expected: the new tests FAIL (e.g. `wikiloc.calls` shows `pages: None`; drawn-mode tests fall into the near-home branch; no skip notices), the v1 tests still PASS.

- [ ] **Step 3: Implement**

In `app/config.py` replace

```python
    hike_pages: int = 6  # 25 routes per Wikiloc request
```

with

```python
    hike_pages: int = 6  # 25 routes per Wikiloc request
    hike_pages_filtered: int = 10  # when hike filters narrow results, fetch up to 250 routes
```

Replace the whole content of `app/engine.py` with:

```python
import asyncio
import sys
from datetime import date

from app.cache import DAY, HOUR, DiskCache
from app.config import Settings
from app.dog_rule import apply_dog_rule, badge_tier
from app.filters import (
    apply_hike_filters, hike_filters_active, skip_notice, stay_filters_active, stay_post_check,
    unsupported_filters,
)
from app.geo import GREECE_BBOX, bbox_around, clamp_to_greece, ensure_min_size, haversine_km
from app.models import (
    BBox, CircleArea, DriveLimit, Hike, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
    StaySearchQuery, TripQuery, TripSearch,
)
from app.ranking import rank_combos
from app.sources.stays.base import ProviderUnavailable, StayProvider
from app.sources.wikiloc import WikilocUnavailable
from app.trip_window import max_drive_minutes, stay_window

STRAIGHT_LINE_KM_PER_MIN = 1.5  # generous: nothing within the drive limit falls outside the box
STAY_RADIUS_SLACK = 1.5  # providers search a square; drop results far outside the circle
MIN_AREA_RADIUS_KM = 5.0  # tiny drawn shapes are widened to at least this radius
NO_HIKES_MATCH = "No hikes match your filters"
NO_STAYS_MATCH = "No stays match your filters"
NO_SOURCE = "No source supports this combination of filters"
OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def area_search_box(area) -> BBox | None:
    """Bounding box Wikiloc is searched in for a drawn shape (None when it misses Greece)."""
    if isinstance(area, CircleArea):
        box = bbox_around(LatLon(lat=area.lat, lon=area.lon), area.radius_m / 1000)
    else:
        box = BBox(south=area.south, west=area.west, north=area.north, east=area.east)
    box = clamp_to_greece(box)
    if box.south >= box.north or box.west >= box.east:
        return None
    return ensure_min_size(box, MIN_AREA_RADIUS_KM)


def in_area(h: Hike, area) -> bool:
    """Circle: within the (min-size widened) radius of its centre. Rectangle: the box already trims."""
    if not isinstance(area, CircleArea):
        return True
    radius_km = max(area.radius_m / 1000, MIN_AREA_RADIUS_KM)
    return haversine_km(LatLon(lat=area.lat, lon=area.lon), LatLon(lat=h.lat, lon=h.lon)) <= radius_km


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

    async def _wikiloc_cached(self, box, sort: str, pages: int) -> list[Hike]:
        # v2 key: entries now hold every activity (v1 entries were hiking-only, without `activity`).
        # Post-fetch filters are not in the key, so they share one raw fetch per sort + page count.
        key = f"hikes:v2:{sort}:{pages}:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box, sort=sort, pages=pages)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes

    async def search_hikes(self, q: HikeQuery) -> HikeSearch:
        warnings: list[str] = []
        limit = None
        if q.mode == "place":
            try:
                box = await self.geocoder.geocode(q.place)
            except Exception as exc:
                print(f"geocoding failed: {exc}", file=sys.stderr)
                return HikeSearch(warnings=["Place search unavailable – try again later"])
            if box is None:
                return HikeSearch(warnings=[f"Couldn't find '{q.place}' in Greece"])
        elif q.mode == "drawn":
            box = area_search_box(q.area)
            if box is None:
                return HikeSearch(warnings=[OUTSIDE_GREECE])
        else:
            limit = self.drive_limit_minutes(q.start, q.end, q.drive_limit)
            box = GREECE_BBOX if limit is None else clamp_to_greece(
                bbox_around(self.home, limit * STRAIGHT_LINE_KM_PER_MIN))
        pages = self.settings.hike_pages_filtered if hike_filters_active(q.filters) else self.settings.hike_pages
        try:
            fetched = await self._wikiloc_cached(box, q.filters.sort, pages)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
        hikes = apply_hike_filters(fetched, q.filters)
        if fetched and not hikes:
            return HikeSearch(warnings=[NO_HIKES_MATCH], drive_limit_minutes=limit)
        if q.mode == "drawn":
            hikes = [h for h in hikes if in_area(h, q.area)]
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
                       check_out=check_out, guests=q.guests, dog=q.dog, filters=q.filters)
        warnings: list[str] = []
        runnable = []
        for p in self.providers:
            missing = unsupported_filters(p.name, q.filters, dog=q.dog)
            if missing:
                warnings.append(skip_notice(p.name, missing))
            else:
                runnable.append(p)
        if self.providers and not runnable:
            return StaySearch(warnings=warnings + [NO_SOURCE], center=center)
        results = await asyncio.gather(*(self._provider_search(p, sq) for p in runnable))
        warnings += [w for _, w in results if w]
        stays = []
        for provider_stays, _ in results:
            for s in provider_stays:
                km = haversine_km(center, LatLon(lat=s.lat, lon=s.lon))
                if km <= radius * STAY_RADIUS_SLACK:
                    stays.append(s.model_copy(update={"distance_km": round(km, 1)}))
        stays = stay_post_check(stays, q.filters, (check_out - check_in).days)
        stays = apply_dog_rule(stays, q.dog)
        stays.sort(key=lambda s: (badge_tier(s) if q.dog else 0, s.distance_km))
        if not stays and stay_filters_active(q.filters) and any(w is None for _, w in results):
            warnings.append(NO_STAYS_MATCH)
        self._register(stays)
        return StaySearch(stays=stays, warnings=_dedupe(warnings), center=center)

    async def plan_trip(self, q: TripQuery) -> TripSearch:
        hikes = await self.search_hikes(HikeQuery(
            start=q.start, end=q.end, drive_limit=q.drive_limit, mode="drawn" if q.area else "near_home",
            area=q.area, filters=q.hike_filters))
        warnings = list(hikes.warnings)
        semaphore = asyncio.Semaphore(2)

        async def stays_for(h: Hike):
            async with semaphore:
                return h, await self.search_stays(StaySearchQuery(
                    start=q.start, end=q.end, hike_id=h.id, guests=q.guests, dog=q.dog, filters=q.stay_filters))

        results = await asyncio.gather(*(stays_for(h) for h in hikes.hikes[: self.settings.trip_top_hikes]))
        pairs = []
        for h, found in results:
            warnings.extend(found.warnings)
            pairs.extend((h, s) for s in found.stays[: self.settings.trip_stays_per_hike])
        combos = rank_combos(pairs, self.settings.weights, self.settings.stay_radius_km, q.dog)
        return TripSearch(combos=combos[: self.settings.trip_max_combos], warnings=_dedupe(warnings))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_engine.py -v`
Expected: all PASS.
Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/engine.py tests/test_engine.py
git commit -m "feat(hick-toors): engine filters, provider skipping, drawn-area and trip-area search

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Wikiloc login mode (`WIKILOC_COOKIE`) — server-side hike filters, `loop_only`, expiry fallback

Spec §3.1a. With a logged-in session cookie Wikiloc honours `a`, `lfr/lto`, `ufr/uto`, `sfr/sto`, `t`; anonymously they return 0 results. The engine post-filter stays on in both modes (idempotent safety net).

**Files:**
- Modify: `app/models.py`, `app/filters.py`, `app/sources/wikiloc.py`, `app/config.py`, `app/wiring.py`, `app/engine.py`, `.env.example`
- Test: `tests/test_wikiloc.py`, `tests/test_filters.py`, `tests/test_engine.py`, `tests/test_wiring.py` (new)

**Interfaces:**
- Consumes: Task 3 `WikilocSource`, `make_fetch`; Task 6 `Engine._wikiloc_cached`, `search_hikes`; Task 2 `catalogue()`, `hike_filters_active`.
- Produces:
  - `HikeFilters.loop_only: bool = False`.
  - `app.filters.catalogue(wikiloc_login: bool = False) -> dict` — adds top-level `"wikiloc_login"` and hike field `{"key": "loop_only", "type": "bool", "label": "Circular routes only", "sources": None | "Needs Wikiloc login", "disabled": not wikiloc_login}` (between `min_trailrank` and `sort`). `hike_filters_active` ignores `sort` and `loop_only`.
  - `app.sources.wikiloc.WikilocLoginExpired(Exception)`, `server_filter_params(f: HikeFilters) -> dict` (always `a`; `lfr`/`lto` formatted with `:g`; `ufr`/`uto`; `sfr=min(dmin,4)` and `sto=min(dmax,4)` only when difficulty is not 1–5, no `sto` when dmax is 5; `t=1` for `loop_only`).
  - `make_fetch(...)` returns `fetch(url, params, headers=None)`; headers are never logged.
  - `WikilocSource(fetch=None, pages=2, cookie: str | None = None)`, property `logged_in: bool`, `__repr__` without the cookie; `search(bbox, query="", sort="popular", pages=None, filters: HikeFilters | None = None, use_login: bool = True)` — when logged in and `use_login`, calls `fetch(url, params, {"Cookie": cookie})` with `server_filter_params(filters)` merged into params (when `filters` is given); anonymous calls keep the two-argument `fetch(url, params)`. Logged-in page 0 with `forwardToLogin` truthy, or with server params and no `spas`, raises `WikilocLoginExpired`.
  - `Settings.wikiloc_cookie: str | None = None` (env `WIKILOC_COOKIE`); `build_engine` passes it as `cookie=`.
  - `Engine.wikiloc_login: bool` (from `self.wikiloc.logged_in`, default False); constants `LOOP_NEEDS_LOGIN = "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"`, `LOGIN_EXPIRED = "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"`; `_wikiloc_cached(box, f: HikeFilters, pages: int, login: bool)` with key `hikes:v2:{anon | login:<json server params>}:{sort}:{pages}:{bbox}`.
  - Page count: logged in → `hike_pages` (6) always; anonymous → `hike_pages_filtered` (10) when `hike_filters_active`, else 6.

- [ ] **Step 1: Write the failing tests**

In `tests/test_wikiloc.py` replace

```python
from app.models import BBox
from app.sources.wikiloc import WikilocSource, WikilocUnavailable, make_fetch, parse_search
```

with

```python
from app.models import BBox, HikeFilters
from app.sources.wikiloc import (
    WikilocLoginExpired, WikilocSource, WikilocUnavailable, make_fetch, parse_search, server_filter_params,
)
```

and insert directly above `async def test_make_fetch_gives_up_after_retries():`

```python
def test_server_filter_params():
    assert server_filter_params(HikeFilters()) == {"a": "1"}
    f = HikeFilters(activities=[1, 43], distance_min_km=10, distance_max_km=15.5, elevation_min_m=800,
                    elevation_max_m=1500, difficulty_min=3, difficulty_max=4, loop_only=True,
                    min_trailrank=50, sort="top_rated")
    assert server_filter_params(f) == {"a": "1,43", "lfr": "10", "lto": "15.5", "ufr": 800, "uto": 1500,
                                       "sfr": 3, "sto": 4, "t": 1}
    assert server_filter_params(HikeFilters(difficulty_min=5)) == {"a": "1", "sfr": 4}  # max 5: no sto
    assert server_filter_params(HikeFilters(difficulty_max=2)) == {"a": "1", "sfr": 1, "sto": 2}


async def test_logged_in_search_sends_cookie_and_server_filters():
    calls = []

    async def fetch(url, params, headers=None):
        calls.append((params, headers))
        return {"spas": [wikiloc_spa(1, "r", 39.3, 22.9)], "forwardToLogin": None, "restricted": False}

    source = WikilocSource(fetch=fetch, cookie="SESSION=abc")
    assert source.logged_in and "abc" not in repr(source)
    hikes = await source.search(BOX, filters=HikeFilters(distance_min_km=10, loop_only=True))
    params, headers = calls[0]
    assert headers == {"Cookie": "SESSION=abc"}
    assert (params["a"], params["lfr"], params["t"]) == ("1", "10", 1)
    assert len(hikes) == 1


async def test_anonymous_search_sends_no_cookie_and_no_filter_params():
    calls = []

    async def fetch(url, params):  # two-argument fetch: anonymous calls must not pass headers
        calls.append(params)
        return {"spas": []}

    await WikilocSource(fetch=fetch).search(BOX, filters=HikeFilters(distance_min_km=10))
    await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(
        BOX, filters=HikeFilters(distance_min_km=10), use_login=False)
    assert all(not {"a", "lfr", "t"} & set(p) for p in calls)


@pytest.mark.parametrize("payload", [{"spas": [], "count": 0}, {"spas": None, "forwardToLogin": True}])
async def test_logged_in_empty_or_forward_to_login_raises_expired(payload):
    async def fetch(url, params, headers=None):
        return payload

    with pytest.raises(WikilocLoginExpired):
        await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(BOX, filters=HikeFilters())


async def test_make_fetch_sends_extra_headers():
    seen = []

    def handler(request):
        seen.append((request.headers.get("cookie"), request.headers.get("user-agent")))
        return httpx.Response(200, json={"spas": []})

    fetch = make_fetch(transport=httpx.MockTransport(handler))
    await fetch("https://es.wikiloc.com/wikiloc/find.do", {}, {"Cookie": "SESSION=abc"})
    await fetch("https://es.wikiloc.com/wikiloc/find.do", {})
    assert seen == [("SESSION=abc", "wikiloc-app/1.0"), (None, "wikiloc-app/1.0")]


```

In `tests/test_filters.py` replace

```python
    assert hike_keys == ["activities", "difficulty", "distance", "elevation", "min_trailrank", "sort"]
```

with

```python
    assert hike_keys == ["activities", "difficulty", "distance", "elevation", "min_trailrank", "loop_only",
                         "sort"]
```

and replace

```python
def test_hike_filters_active_ignores_sort():
    assert hike_filters_active(HikeFilters()) is False
    assert hike_filters_active(HikeFilters(sort="top_rated")) is False
```

with

```python
def test_catalogue_marks_loop_only_by_login():
    anon = {f["key"]: f for f in catalogue()["hike"]["fields"]}["loop_only"]
    assert catalogue()["wikiloc_login"] is False
    assert anon["disabled"] is True and anon["sources"] == "Needs Wikiloc login"
    cat = catalogue(wikiloc_login=True)
    logged = {f["key"]: f for f in cat["hike"]["fields"]}["loop_only"]
    assert cat["wikiloc_login"] is True and logged["disabled"] is False and logged["sources"] is None


def test_hike_filters_active_ignores_sort():
    assert hike_filters_active(HikeFilters()) is False
    assert hike_filters_active(HikeFilters(sort="top_rated")) is False
    assert hike_filters_active(HikeFilters(loop_only=True)) is False  # server-side only
```

In `tests/test_engine.py` replace

```python
from app.sources.wikiloc import WikilocUnavailable
```

with

```python
from app.sources.wikiloc import WikilocLoginExpired, WikilocUnavailable
```

replace

```python
class FakeWikiloc:
    def __init__(self, hikes=None, fail=False):
        self.hikes, self.fail, self.boxes, self.calls = hikes or [], fail, [], []

    async def search(self, bbox, query="", sort="popular", pages=None):
        self.boxes.append(bbox)
        self.calls.append({"sort": sort, "pages": pages})
```

with

```python
class FakeWikiloc:
    def __init__(self, hikes=None, fail=False, logged_in=False, expired=False):
        self.hikes, self.fail, self.boxes, self.calls = hikes or [], fail, [], []
        self.logged_in, self.expired, self.modes = logged_in, expired, []

    async def search(self, bbox, query="", sort="popular", pages=None, filters=None, use_login=True):
        self.boxes.append(bbox)
        self.calls.append({"sort": sort, "pages": pages})
        self.modes.append({"filters": filters, "use_login": use_login})
        if self.expired and use_login and self.logged_in:
            raise WikilocLoginExpired()
```

and append at the end of the file:

```python


# ---- v2: Wikiloc login mode ----

async def test_logged_in_search_sends_filters_with_normal_page_count(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10,
                                                                              "loop_only": True}))
    assert wikiloc.calls == [{"sort": "popular", "pages": 6}]
    assert wikiloc.modes[0]["use_login"] is True and wikiloc.modes[0]["filters"].loop_only is True
    assert [h.id for h in result.hikes] == ["wikiloc:1"] and result.warnings == []


async def test_anonymous_loop_only_is_ignored_with_notice(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"loop_only": True}))
    assert result.warnings == ["Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"]
    assert [h.id for h in result.hikes] == ["wikiloc:1"]
    assert wikiloc.modes == [{"filters": None, "use_login": False}] and wikiloc.calls[0]["pages"] == 6


async def test_expired_login_falls_back_to_local_filtering_with_warning(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True, expired=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10}))
    assert [m["use_login"] for m in wikiloc.modes] == [True, False]
    assert wikiloc.calls[1]["pages"] == 10  # anonymous fallback fetches the filtered page count
    assert result.warnings == [
        "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"]
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_empty_area_with_login_does_not_claim_expiry(settings):
    # Server-filtered page empty, anonymous retry has routes but none match -> genuinely no match.
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True, expired=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 95}))
    assert result.warnings == ["No hikes match your filters"]


async def test_login_and_anonymous_results_are_cached_separately(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    q = lambda f: HikeQuery(start=FRI, end=SUN, filters=f)  # noqa: E731
    await engine.search_hikes(q({"activities": [1]}))
    await engine.search_hikes(q({"activities": [1, 43]}))  # different server params -> new fetch
    await engine.search_hikes(q({"activities": [1]}))  # cached
    assert len(wikiloc.calls) == 2
```

Create `tests/test_wiring.py`:

```python
from app.config import Settings
from app.wiring import build_engine


def test_wikiloc_cookie_setting_turns_on_login_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("WIKILOC_COOKIE", raising=False)
    anon = build_engine(Settings(_env_file=None, cache_dir=tmp_path))
    assert anon.wikiloc_login is False
    logged = build_engine(Settings(_env_file=None, cache_dir=tmp_path, wikiloc_cookie="SESSION=abc"))
    assert logged.wikiloc_login is True and "abc" not in repr(logged.wikiloc)
    blank = build_engine(Settings(_env_file=None, cache_dir=tmp_path, wikiloc_cookie=""))
    assert blank.wikiloc_login is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_wikiloc.py tests/test_filters.py tests/test_engine.py tests/test_wiring.py -v`
Expected: collection ERRORs — `ImportError: cannot import name 'WikilocLoginExpired'` (test_wikiloc, test_engine); test_filters FAILs on the catalogue keys / `loop_only` (`ValidationError: Extra inputs are not permitted`); test_wiring FAILs (`Settings` has no `wikiloc_cookie`).

- [ ] **Step 3: Implement models, catalogue, config, wiring, `.env.example`**

In `app/models.py` (class `HikeFilters`) replace

```python
    min_trailrank: int | None = Field(default=None, ge=0, le=100)
    sort: Literal["popular", "top_rated"] = "popular"
```

with

```python
    min_trailrank: int | None = Field(default=None, ge=0, le=100)
    loop_only: bool = False  # circular routes; Wikiloc server-side only (needs WIKILOC_COOKIE)
    sort: Literal["popular", "top_rated"] = "popular"
```

In `app/filters.py` replace

```python
def catalogue() -> dict:
    """Everything the filter popups render: fields, options, defaults and source tags."""
```

with

```python
def catalogue(wikiloc_login: bool = False) -> dict:
    """Everything the filter popups render: fields, options, defaults and source tags.

    wikiloc_login: a Wikiloc session cookie is configured, so server-only filters (loop_only) work.
    """
```

replace

```python
         "min": 0, "max": 100, "step": 1},
        {"key": "sort", "type": "select", "label": "Wikiloc order", "sources": None,
```

with

```python
         "min": 0, "max": 100, "step": 1},
        {"key": "loop_only", "type": "bool", "label": "Circular routes only",
         "sources": None if wikiloc_login else "Needs Wikiloc login", "disabled": not wikiloc_login},
        {"key": "sort", "type": "select", "label": "Wikiloc order", "sources": None,
```

and replace

```python
    return {
        "hike": {"fields": hike_fields, "defaults": HikeFilters().model_dump()},
        "stay": {"fields": stay_fields, "defaults": StayFilters().model_dump()},
    }


def hike_filters_active(f: HikeFilters) -> bool:
    """True when a post-fetch hike filter narrows results (sort is applied by Wikiloc itself)."""
    return f.model_copy(update={"sort": "popular"}) != HikeFilters()
```

with

```python
    return {
        "wikiloc_login": wikiloc_login,
        "hike": {"fields": hike_fields, "defaults": HikeFilters().model_dump()},
        "stay": {"fields": stay_fields, "defaults": StayFilters().model_dump()},
    }


def hike_filters_active(f: HikeFilters) -> bool:
    """True when a post-fetch hike filter narrows results (sort and loop_only are server-side only)."""
    return f.model_copy(update={"sort": "popular", "loop_only": False}) != HikeFilters()
```

In `app/config.py` replace

```python
    hike_pages_filtered: int = 10  # when hike filters narrow results, fetch up to 250 routes
```

with

```python
    hike_pages_filtered: int = 10  # anonymous + hike filters: fetch up to 250 routes, filter locally
    wikiloc_cookie: str | None = None  # WIKILOC_COOKIE: logged-in session cookie -> server-side filters
```

In `app/wiring.py` replace

```python
        wikiloc=WikilocSource(pages=settings.hike_pages),
```

with

```python
        wikiloc=WikilocSource(pages=settings.hike_pages, cookie=settings.wikiloc_cookie),
```

Append to `.env.example` (on a new line after `AIRBNB_DETAIL_TOP_N=10`; a placeholder only — never put a real cookie in this tracked file):

```
# Optional: your logged-in Wikiloc browser cookie (whole Cookie header value) enables server-side
# hike filters and "circular routes only". Keep it only in .env (gitignored).
# WIKILOC_COOKIE=
```

- [ ] **Step 4: Implement the Wikiloc login mode**

In `app/sources/wikiloc.py` replace

```python
from app.models import BBox, Hike
```

with

```python
from app.models import BBox, Hike, HikeFilters
```

replace

```python
Fetch = Callable[[str, dict], Awaitable[Any]]


class WikilocUnavailable(Exception):
    pass
```

with

```python
Fetch = Callable[..., Awaitable[Any]]  # fetch(url, params) or fetch(url, params, headers)


class WikilocUnavailable(Exception):
    pass


class WikilocLoginExpired(Exception):
    """A logged-in, server-filtered search came back empty or asked to log in again."""


def server_filter_params(f: HikeFilters) -> dict:
    """HikeFilters -> find.do filter params. Wikiloc honours them only for logged-in callers
    (probed 2026-10-10); anonymously they return count 0, so they are sent only with a cookie."""
    params: dict = {"a": ",".join(str(a) for a in f.activities)}
    if f.distance_min_km is not None:
        params["lfr"] = f"{f.distance_min_km:g}"
    if f.distance_max_km is not None:
        params["lto"] = f"{f.distance_max_km:g}"
    if f.elevation_min_m is not None:
        params["ufr"] = f.elevation_min_m
    if f.elevation_max_m is not None:
        params["uto"] = f.elevation_max_m
    if (f.difficulty_min, f.difficulty_max) != (1, 5):
        params["sfr"] = min(f.difficulty_min, 4)  # the filter scale is 1-4, results are 1-5
        if f.difficulty_max < 5:
            params["sto"] = min(f.difficulty_max, 4)
    if f.loop_only:
        params["t"] = 1
    return params
```

In `make_fetch` replace

```python
    async def fetch(url: str, params: dict) -> Any:
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, params=params, timeout=30.0)
```

with

```python
    async def fetch(url: str, params: dict, headers: dict | None = None) -> Any:
        # headers may carry the Wikiloc session cookie: never log them.
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, params=params, headers=headers, timeout=30.0)
```

In `WikilocSource` replace

```python
    def __init__(self, fetch: Fetch | None = None, pages: int = 2):
        self._fetch = fetch or make_fetch()
        self._pages = pages

    async def search(self, bbox: BBox, query: str = "", sort: str = "popular",
                     pages: int | None = None) -> list[Hike]:
        """Routes in bbox. sort="top_rated" asks Wikiloc for its TrailRank order (the only
        server-side filter that works anonymously); pages overrides the configured page count."""
        hikes: list[Hike] = []
        seen: set[str] = set()
```

with

```python
    def __init__(self, fetch: Fetch | None = None, pages: int = 2, cookie: str | None = None):
        self._fetch = fetch or make_fetch()
        self._pages = pages
        self._cookie = cookie or None  # Wikiloc session cookie (WIKILOC_COOKIE); never logged

    @property
    def logged_in(self) -> bool:
        return self._cookie is not None

    def __repr__(self) -> str:  # keep the cookie out of any debug output
        return f"WikilocSource(pages={self._pages}, logged_in={self.logged_in})"

    async def search(self, bbox: BBox, query: str = "", sort: str = "popular",
                     pages: int | None = None, filters: HikeFilters | None = None,
                     use_login: bool = True) -> list[Hike]:
        """Routes in bbox. sort="top_rated" asks Wikiloc for its TrailRank order; pages overrides
        the configured page count. With a cookie (and use_login) the cookie is sent, plus
        `filters` as server params; raises WikilocLoginExpired when that comes back empty or
        Wikiloc asks to log in, so the caller can retry anonymously."""
        login = use_login and self._cookie is not None
        headers = {"Cookie": self._cookie} if login else None
        server = server_filter_params(filters) if login and filters is not None else {}
        hikes: list[Hike] = []
        seen: set[str] = set()
```

and replace

```python
            if sort == "top_rated":
                params["sort"] = "trailrank"
            data = await self._fetch(SEARCH_URL, params)
            if not isinstance(data, dict):
                if page == 0:
                    raise WikilocUnavailable(
                        "Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute"
                    )
                break
```

with

```python
            if sort == "top_rated":
                params["sort"] = "trailrank"
            params.update(server)
            data = await (self._fetch(SEARCH_URL, params, headers) if login else self._fetch(SEARCH_URL, params))
            if not isinstance(data, dict):
                if page == 0:
                    raise WikilocUnavailable(
                        "Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute"
                    )
                break
            if page == 0 and login and (data.get("forwardToLogin") or (server and not data.get("spas"))):
                raise WikilocLoginExpired()
```

- [ ] **Step 5: Engine — login mode, fallback, notices, cache key**

In `app/engine.py` replace

```python
import asyncio
import sys
```

with

```python
import asyncio
import json
import sys
```

replace

```python
    BBox, CircleArea, DriveLimit, Hike, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
```

with

```python
    BBox, CircleArea, DriveLimit, Hike, HikeFilters, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
```

replace

```python
from app.sources.wikiloc import WikilocUnavailable
```

with

```python
from app.sources.wikiloc import WikilocLoginExpired, WikilocUnavailable, server_filter_params
```

replace

```python
OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"
```

with

```python
OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"
LOOP_NEEDS_LOGIN = "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"
LOGIN_EXPIRED = "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"
```

replace the whole `_wikiloc_cached` method

```python
    async def _wikiloc_cached(self, box, sort: str, pages: int) -> list[Hike]:
        # v2 key: entries now hold every activity (v1 entries were hiking-only, without `activity`).
        # Post-fetch filters are not in the key, so they share one raw fetch per sort + page count.
        key = f"hikes:v2:{sort}:{pages}:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box, sort=sort, pages=pages)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes
```

with

```python
    @property
    def wikiloc_login(self) -> bool:
        return bool(getattr(self.wikiloc, "logged_in", False))

    async def _wikiloc_cached(self, box, f: HikeFilters, pages: int, login: bool) -> list[Hike]:
        # v2 key: entries now hold every activity (v1 entries were hiking-only, without `activity`).
        # Anonymous fetches ignore post-fetch filters, so they share one raw fetch per sort + page
        # count; logged-in fetches are server-filtered, so their key carries the server params.
        mode = "login:" + json.dumps(server_filter_params(f), sort_keys=True) if login else "anon"
        key = f"hikes:v2:{mode}:{f.sort}:{pages}:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box, sort=f.sort, pages=pages, filters=f if login else None,
                                          use_login=login)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes
```

and in `search_hikes` replace

```python
        pages = self.settings.hike_pages_filtered if hike_filters_active(q.filters) else self.settings.hike_pages
        try:
            fetched = await self._wikiloc_cached(box, q.filters.sort, pages)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
        hikes = apply_hike_filters(fetched, q.filters)
        if fetched and not hikes:
            return HikeSearch(warnings=[NO_HIKES_MATCH], drive_limit_minutes=limit)
```

with

```python
        # Anonymous: Wikiloc can't filter, so fetch more and filter here. Logged in: the server
        # filters (the local filter below stays as an idempotent safety net).
        anon_pages = self.settings.hike_pages_filtered if hike_filters_active(q.filters) else self.settings.hike_pages
        login = self.wikiloc_login
        if q.filters.loop_only and not login:
            warnings.append(LOOP_NEEDS_LOGIN)
        try:
            try:
                fetched = await self._wikiloc_cached(box, q.filters, self.settings.hike_pages if login else anon_pages,
                                                     login)
            except WikilocLoginExpired:
                fetched = await self._wikiloc_cached(box, q.filters, anon_pages, False)
                if apply_hike_filters(fetched, q.filters):
                    warnings.append(LOGIN_EXPIRED)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=warnings + [str(exc)], drive_limit_minutes=limit)
        hikes = apply_hike_filters(fetched, q.filters)
        if fetched and not hikes:
            return HikeSearch(warnings=warnings + [NO_HIKES_MATCH], drive_limit_minutes=limit)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_wikiloc.py tests/test_filters.py tests/test_engine.py tests/test_wiring.py -v`
Expected: all PASS (all Task 3/6 engine tests unchanged and green — the default FakeWikiloc is anonymous).
Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.
Run: `git diff --cached --stat; git status --short` before committing and confirm `.env` is not listed (it is gitignored) and `.env.example` contains no cookie value.

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/filters.py app/sources/wikiloc.py app/config.py app/wiring.py app/engine.py .env.example tests/test_wikiloc.py tests/test_filters.py tests/test_engine.py tests/test_wiring.py
git commit -m "feat(hick-toors): Wikiloc login mode – server-side hike filters, circular-only, expiry fallback

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `GET /api/filters`, new API fields, MCP tool filter args

**Files:**
- Modify: `app/main.py`
- Modify: `app/mcp_tools.py` (full replacement below)
- Test: `tests/test_api.py`, `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `app.filters.catalogue()` (Task 2); query models (Task 1) — existing POST endpoints accept `filters`, `area`, `mode="drawn"`, `hike_filters`, `stay_filters` with no code change, returning 422 on invalid input.
- Produces: `GET /api/filters` → `catalogue(wikiloc_login=bool(settings.wikiloc_cookie))` JSON (used by Task 9; never contains the cookie). MCP tools: `search_hikes(..., hike_filters: dict | None = None)`, `search_stays(..., stay_filters: dict | None = None)`, `plan_trip(..., hike_filters: dict | None = None, stay_filters: dict | None = None)`; invalid input → `{"error": "<loc>: <msg>; ..."}` with the `"Value error, "` prefix removed (model-level errors have no loc and keep just the message).

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py` replace the first line

```python
from fastapi.testclient import TestClient
```

with

```python
import json

from fastapi.testclient import TestClient
```

and insert directly above `def test_index_loads_leaflet_and_app(tmp_path):`

```python
def test_filters_catalogue_endpoint(tmp_path):
    body = client(tmp_path).get("/api/filters").json()
    assert body["hike"]["defaults"]["activities"] == [1]
    assert body["stay"]["defaults"]["price_max"] is None
    assert [f["key"] for f in body["hike"]["fields"]][0] == "activities"
    assert any(f["key"] == "amenities" for f in body["stay"]["fields"])
    assert body["wikiloc_login"] is False


def test_filters_catalogue_reports_wikiloc_login(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, wikiloc_cookie="SESSION=abc")
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings))
    body = c.get("/api/filters").json()
    assert body["wikiloc_login"] is True and "SESSION" not in json.dumps(body)


def test_filters_and_area_reach_engine(tmp_path):
    engine = FakeEngine()
    c = client(tmp_path, engine)
    r = c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "drawn",
                                   "area": {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000},
                                   "filters": {"min_trailrank": 60, "sort": "top_rated"}})
    assert r.status_code == 200
    q = engine.calls[0][1]
    assert q.area.radius_m == 8000 and q.filters.min_trailrank == 60 and q.filters.sort == "top_rated"
    c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1",
                               "filters": {"price_max": 120, "amenities": ["parking"]}})
    assert engine.calls[1][1].filters.amenities == ["parking"]
    c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25",
                               "area": {"shape": "rectangle", "south": 38.3, "west": 22.3, "north": 38.7,
                                        "east": 22.9},
                               "hike_filters": {"difficulty_max": 3}, "stay_filters": {"entire_place": True}})
    trip = engine.calls[2][1]
    assert trip.area.shape == "rectangle" and trip.hike_filters.difficulty_max == 3
    assert trip.stay_filters.entire_place is True


def test_invalid_filter_range_and_missing_area_are_422(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1",
                                   "filters": {"price_min": 200, "price_max": 100}})
    assert r.status_code == 422 and "price per night: minimum must not be greater than maximum" in r.text
    r = c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "drawn"})
    assert r.status_code == 422 and "Draw a rectangle or circle first" in r.text


```

In `tests/test_mcp_tools.py` append at the end:

```python


async def test_filter_dicts_are_passed_through(engine):
    await tools.search_hikes("2026-10-23", "2026-10-25", hike_filters={"min_trailrank": 60})
    await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1",
                             stay_filters={"price_max": 120, "amenities": ["parking"]})
    await tools.plan_trip("2026-10-23", "2026-10-25", hike_filters={"sort": "top_rated"},
                          stay_filters={"entire_place": True})
    assert engine.queries[0].filters.min_trailrank == 60
    assert engine.queries[1].filters.price_max == 120
    assert engine.queries[2].hike_filters.sort == "top_rated" and engine.queries[2].stay_filters.entire_place


async def test_invalid_filters_return_error_json(engine):
    out = json.loads(await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1",
                                              stay_filters={"price_min": 200, "price_max": 100}))
    assert "price per night: minimum must not be greater than maximum" in out["error"]
    out = json.loads(await tools.search_hikes("2026-10-23", "2026-10-25", hike_filters={"min_rank": 5}))
    assert out["error"] == "filters.min_rank: Extra inputs are not permitted"
    assert engine.queries == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_api.py tests/test_mcp_tools.py -v`
Expected: FAIL — `/api/filters` returns 404 (JSON has no `hike`), MCP tools raise `TypeError: ... unexpected keyword argument 'hike_filters'`.

- [ ] **Step 3: Implement**

In `app/main.py` replace

```python
from app.config import BASE_DIR, Settings
from app.models import HikeQuery, HikeSearch, StaySearch, StaySearchQuery, TripQuery, TripSearch
```

with

```python
from app.config import BASE_DIR, Settings
from app.filters import catalogue
from app.models import HikeQuery, HikeSearch, StaySearch, StaySearchQuery, TripQuery, TripSearch
```

and replace

```python
    @app.post("/api/hikes", response_model=HikeSearch)
```

with

```python
    @app.get("/api/filters")
    async def filters():
        return catalogue(wikiloc_login=bool(settings.wikiloc_cookie))

    @app.post("/api/hikes", response_model=HikeSearch)
```

Replace the whole content of `app/mcp_tools.py` with (docstrings are literal — FastMCP reads them at decoration time):

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
    def one(e: dict) -> str:
        msg = str(e["msg"]).replace("Value error, ", "")
        loc = ".".join(str(p) for p in e.get("loc", ()))
        return f"{loc}: {msg}" if loc else msg

    return json.dumps({"error": "; ".join(one(e) for e in exc.errors())})


@mcp.tool()
async def search_hikes(start: str, end: str, place: str | None = None,
                       max_drive_minutes: int | None = None, unlimited: bool = False,
                       hike_filters: dict | None = None) -> str:
    """Find hikes in Greece (Wikiloc) for a trip from start to end (YYYY-MM-DD).

    Without `place`, searches around home (Athens) within a drive limit that defaults from the
    number of days (1 day: 90 min, 2-3 days: 4 h, 4+ days: unlimited). Set max_drive_minutes to
    override, or unlimited=True to include islands (hikes flagged ferry_needed).
    With `place` (e.g. "Pelion", "Zagori"), searches that area instead.
    hike_filters (optional dict, every key optional): activities (list of ids: 1 Hiking [default],
    43 Walking, 48 Trail running, 57 Nordic walking, 66 Canicross, 60 Via ferrata),
    difficulty_min / difficulty_max (1 Easy .. 5 Experts Only), distance_min_km / distance_max_km,
    elevation_min_m / elevation_max_m, min_trailrank (0-100), loop_only (circular routes; needs
    WIKILOC_COOKIE, otherwise ignored with a warning), sort ("popular" or "top_rated").
    Returns JSON {hikes: [{id, name, url, lat, lon, distance_km, elevation_gain_m, difficulty,
    trailrank, near, drive_minutes, ferry_needed}], warnings}.
    """
    try:
        q = HikeQuery(start=start, end=end, mode="place" if place else "near_home", place=place,
                      drive_limit=_limit(max_drive_minutes, unlimited), filters=hike_filters or {})
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_hikes(q)).model_dump_json()


@mcp.tool()
async def search_stays(start: str, end: str, hike_id: str | None = None, lat: float | None = None,
                       lon: float | None = None, dog: bool = True, guests: int = 2,
                       radius_km: float | None = None, stay_filters: dict | None = None) -> str:
    """Find stays (Airbnb, Booking.com) near a hike (hike_id from search_hikes) or a lat/lon.

    With dog=True only stays that allow pets, or have a garden (badge garden_unconfirmed =
    pets not confirmed), are returned.
    stay_filters (optional dict, every key optional): price_min / price_max (EUR per night),
    min_rating (0-5), entire_place, property_types (house, apartment, guesthouse, hotel, villa),
    min_bedrooms, min_beds, min_bathrooms, amenities (wifi, kitchen, parking, pool, hot_tub, ac,
    washer, ev_charger, fireplace, heating, workspace, bbq), free_cancellation, instant_book,
    guest_favorite, breakfast, min_stars (1-5), beachfront. A source that can't apply a filter is
    skipped and a warning says so (e.g. Booking.com can't filter by fireplace).
    Returns JSON {stays: [{id, source, name, url, lat, lon, total_price, currency, rating, badge,
    distance_km}], warnings}.
    """
    try:
        q = StaySearchQuery(start=start, end=end, hike_id=hike_id, lat=lat, lon=lon, dog=dog,
                            guests=guests, radius_km=radius_km, filters=stay_filters or {})
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_stays(q)).model_dump_json()


@mcp.tool()
async def plan_trip(start: str, end: str, dog: bool = True, guests: int = 2,
                    max_drive_minutes: int | None = None, unlimited: bool = False,
                    hike_filters: dict | None = None, stay_filters: dict | None = None) -> str:
    """Rank hike + stay combos for a trip from home (score /100 with breakdown).

    hike_filters / stay_filters take the same keys as in search_hikes / search_stays.
    Returns JSON {combos: [{hike, stay, score, breakdown}], warnings}.
    """
    try:
        q = TripQuery(start=start, end=end, dog=dog, guests=guests,
                      drive_limit=_limit(max_drive_minutes, unlimited),
                      hike_filters=hike_filters or {}, stay_filters=stay_filters or {})
    except ValidationError as exc:
        return _error(exc)
    return (await engine().plan_trip(q)).model_dump_json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_api.py tests/test_mcp_tools.py -v`
Expected: all PASS (the v1 `test_invalid_input_returns_error_json` still finds "end date must be on or after start date").
Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/mcp_tools.py tests/test_api.py tests/test_mcp_tools.py
git commit -m "feat(hick-toors): /api/filters catalogue and filter args on MCP tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: UI — filter buttons, two modal popups from the catalogue, localStorage, badges

**Files:**
- Create: `static/filters.js`, `static/dialog.js`
- Modify: `static/app.js`, `static/index.html`, `static/style.css`
- Create: `tests/ui/filters_logic.test.js`, `tests/ui/check_ui.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `GET /api/filters` (Task 8) shape from Tasks 2 and 7 (top-level `wikiloc_login`; hike field `loop_only` with `disabled: true` when not logged in); request fields `filters` (hikes, stays), `hike_filters` / `stay_filters` (trips), `guests`.
- Produces (browser globals, classic scripts loaded in order `filters.js`, `dialog.js`, `app.js`):
  - `static/filters.js`: `sanitizeFilters(raw, catalogue, defaultGuests) -> {hike, stay, guests}`, `sanitizeSection(raw, section)`, `countActive(values, section) -> int` (one per popup field; a range counts once), `invalidRanges(values, section) -> string[]` (field keys), `loadFilters(catalogue, defaultGuests)`, `saveFilters(filters)` — storage key `"hicktoors.filters.v1"`; CommonJS export for node.
  - `static/dialog.js`: `createFilterDialog({id, title, section, shared, getValues, getShared, onApply}) -> {dialog, open()}`; a `bool` field with `disabled: true` (loop_only without login) renders a disabled checkbox with its "Needs Wikiloc login" tag; DOM ids `hike-dialog`, `stay-dialog`; controls named by model key (`min_trailrank`, `price_min`, …), shared controls named `shared_<name>`; buttons `[data-act=apply|reset|cancel]`; error line `.form-error`; source tags `.src-tag`.
  - `static/app.js`: `state.catalogue`, `state.filters`, `state.dialogs`; `guests()`, `updateFilterBadges()`, `applyShared(shared)`, `setupFilterDialogs()`.
  - DOM: `#hike-filters-btn`, `#stay-filters-btn` (disabled until the catalogue loads), badges `#hike-filter-count`, `#stay-filter-count` (text `•N`, hidden when 0).

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py` append at the end:

```python


def test_index_has_filter_buttons_and_scripts(tmp_path):
    c = client(tmp_path)
    html = c.get("/").text
    assert 'id="hike-filters-btn"' in html and 'id="stay-filters-btn"' in html
    assert html.index("/static/filters.js") < html.index("/static/dialog.js") < html.index("/static/app.js")
    assert c.get("/static/filters.js").status_code == 200 and c.get("/static/dialog.js").status_code == 200
```

Create `tests/ui/filters_logic.test.js`:

```javascript
// Run: node tests/ui/filters_logic.test.js   (plain node, no packages; not collected by pytest)
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const { sanitizeFilters, countActive, invalidRanges, loadFilters } = require(
  path.join(__dirname, "..", "..", "static", "filters.js"));

// Same shapes as GET /api/filters (trimmed to one field of each type).
const catalogue = {
  hike: {
    defaults: { activities: [1], difficulty_min: 1, difficulty_max: 5, min_trailrank: null, sort: "popular" },
    fields: [
      { key: "activities", type: "multi", options: [{ value: 1, label: "Hiking" }, { value: 43, label: "Walking" }] },
      { key: "difficulty", type: "range", min_key: "difficulty_min", max_key: "difficulty_max",
        options: [1, 2, 3, 4, 5].map((v) => ({ value: v, label: String(v) })) },
      { key: "min_trailrank", type: "number", min: 0, max: 100, step: 1 },
      { key: "sort", type: "select", options: [{ value: "popular", label: "p" }, { value: "top_rated", label: "t" }] },
    ],
  },
  stay: {
    defaults: { price_min: null, price_max: null, min_rating: null, entire_place: false, amenities: [] },
    fields: [
      { key: "price", type: "range", min_key: "price_min", max_key: "price_max", min: 0, max: 2000, step: 1 },
      { key: "min_rating", type: "select", options: [{ value: null, label: "Any" }, { value: 4.5, label: "4.5+" }] },
      { key: "entire_place", type: "bool" },
      { key: "amenities", type: "multi", options: [{ value: "wifi", label: "Wifi" }, { value: "fireplace", label: "F" }] },
    ],
  },
};
const defaults = sanitizeFilters(null, catalogue, 2);

// missing / corrupt storage -> defaults
for (const raw of [null, undefined, "garbage", 42, [], { hike: "x", stay: [1, 2], guests: "many" }]) {
  assert.deepEqual(sanitizeFilters(raw, catalogue, 2), defaults, `raw=${JSON.stringify(raw)}`);
}
assert.deepEqual(defaults, { hike: catalogue.hike.defaults, stay: catalogue.stay.defaults, guests: 2 });

// old/partial shapes: unknown keys dropped, bad values replaced field by field, good ones kept
const old = sanitizeFilters({
  hike: { activities: [43, 999, 43], difficulty_min: null, difficulty_max: 3, min_rank: 50, min_trailrank: 101,
    sort: "newest" },
  stay: { price_min: 300, price_max: 100, min_rating: 4.5, entire_place: "yes", amenities: ["sauna", "wifi"] },
  guests: 4,
}, catalogue, 2);
assert.deepEqual(old.hike, { activities: [43], difficulty_min: 1, difficulty_max: 3, min_trailrank: null, sort: "popular" });
assert.deepEqual(old.stay, { price_min: null, price_max: null, min_rating: 4.5, entire_place: false, amenities: ["wifi"] });
assert.equal(old.guests, 4);
assert.deepEqual(sanitizeFilters({ hike: { activities: [] } }, catalogue, 2).hike.activities, [1]);

// badge counts: one per field, a range counts once, list order ignored
assert.equal(countActive(defaults.hike, catalogue.hike), 0);
assert.equal(countActive({ ...defaults.stay, price_min: 50, price_max: 120, amenities: ["wifi"] }, catalogue.stay), 2);
assert.equal(countActive({ ...defaults.hike, activities: [1] }, catalogue.hike), 0);
assert.equal(countActive({ ...defaults.hike, activities: [43, 1] }, catalogue.hike), 1);

// min > max detection
assert.deepEqual(invalidRanges({ ...defaults.stay, price_min: 200, price_max: 100 }, catalogue.stay), ["price"]);
assert.deepEqual(invalidRanges({ ...defaults.stay, price_min: 200 }, catalogue.stay), []);

// localStorage that throws (private mode) -> defaults, no exception
global.localStorage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
assert.deepEqual(loadFilters(catalogue, 2), defaults);
global.localStorage = { getItem: () => "{not json" };
assert.deepEqual(loadFilters(catalogue, 2), defaults);

console.log("filters_logic: all assertions passed");
```

Create `tests/ui/check_ui.py`:

```python
"""Headless-Chrome check of the filter popups (not collected by pytest).

Start the app first, then run the check against it:
    env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765
    env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765

The script talks only to the local server. Search endpoints are answered by Playwright with
canned JSON, so no Wikiloc/Airbnb/Booking request is made; the captured request bodies are
asserted instead.
"""

import json
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HIKE = {"id": "wikiloc:1", "name": "Test hike", "url": "https://es.wikiloc.com/x", "lat": 38.5, "lon": 22.6,
        "distance_km": 9.5, "elevation_gain_m": 600, "difficulty": "Moderate", "trailrank": 70}
CANNED = {
    "/api/hikes": {"hikes": [HIKE], "warnings": [], "drive_limit_minutes": None},
    "/api/stays": {"stays": [], "warnings": [], "center": None},
    "/api/trips": {"combos": [], "warnings": []},
}


def main() -> None:
    requests: list[tuple[str, dict]] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def answer(route):
            path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
            requests.append((path, route.request.post_data_json or {}))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path]))

        for path in CANNED:
            page.route(f"**{path}", answer)

        def load(clear: bool = False) -> None:
            page.goto(BASE)
            if clear:
                page.evaluate("localStorage.clear()")
                page.reload()
            expect(page.locator("#hike-filters-btn")).to_be_enabled(timeout=15000)

        load(clear=True)
        expect(page.locator("#hike-filter-count")).to_be_hidden()
        expect(page.locator("#stay-filter-count")).to_be_hidden()
        print("PASS buttons enabled, no badges on defaults")

        # Hike popup: generated from the catalogue, Apply stores and shows the badge.
        page.click("#hike-filters-btn")
        dialog = page.locator("#hike-dialog")
        expect(dialog).to_be_visible()
        expect(dialog.locator('[data-key="activities"] .chip')).to_have_count(6)
        login = page.evaluate("state.catalogue.wikiloc_login")  # depends on WIKILOC_COOKIE in .env
        loop_box = expect(dialog.locator('input[name="loop_only"]'))
        loop_box.to_be_enabled() if login else loop_box.to_be_disabled()
        dialog.locator('[name="min_trailrank"]').fill("50")
        dialog.locator('[data-act="apply"]').click()
        expect(dialog).to_be_hidden()
        expect(page.locator("#hike-filter-count")).to_have_text("•1")
        print("PASS hike popup apply + badge")

        # Cancel discards edits.
        page.click("#hike-filters-btn")
        dialog.locator('[name="min_trailrank"]').fill("80")
        dialog.locator('[data-act="cancel"]').click()
        page.click("#hike-filters-btn")
        expect(dialog.locator('[name="min_trailrank"]')).to_have_value("50")
        dialog.locator('[data-act="cancel"]').click()
        print("PASS cancel keeps stored values")

        # Stay popup: source tags, min > max blocks Apply, shared settings sync to the header.
        page.click("#stay-filters-btn")
        stay = page.locator("#stay-dialog")
        expect(stay.locator('.chip:has(input[value="\\"fireplace\\""]) .src-tag')).to_have_text("Airbnb only")
        stay.locator('[name="price_min"]').fill("200")
        stay.locator('[name="price_max"]').fill("100")
        expect(stay.locator('[data-act="apply"]')).to_be_disabled()
        expect(stay.locator(".form-error")).to_contain_text("minimum is above maximum")
        stay.locator('[name="price_max"]').fill("300")
        expect(stay.locator('[data-act="apply"]')).to_be_enabled()
        stay.locator('input[name="amenities"][value="\\"parking\\""]').check()
        stay.locator('[name="shared_guests"]').fill("3")
        stay.locator('[name="shared_dog"]').uncheck()
        stay.locator('[data-act="apply"]').click()
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        expect(page.locator("#dog")).not_to_be_checked()
        print("PASS stay popup validation, tags, header sync")

        # Filters reach the API.
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        path, body = requests[-1]
        assert path == "/api/hikes" and body["filters"]["min_trailrank"] == 50, body
        page.locator("#hike-list li").first.click()
        page.wait_for_function("document.querySelector('#stay-count').textContent === '(0)'")
        path, body = requests[-1]
        assert path == "/api/stays", path
        assert body["filters"]["amenities"] == ["parking"] and body["filters"]["price_max"] == 300, body
        assert body["guests"] == 3 and body["dog"] is False, body
        print("PASS filters sent with hike and stay searches")

        # Persisted across reloads; corrupt storage falls back to defaults without errors.
        load()
        expect(page.locator("#hike-filter-count")).to_have_text("•1")
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        page.evaluate("localStorage.setItem('hicktoors.filters.v1', '{not json')")
        load()
        expect(page.locator("#hike-filter-count")).to_be_hidden()
        print("PASS localStorage persistence + corrupt storage")

        # Reset to defaults.
        page.evaluate("""localStorage.setItem('hicktoors.filters.v1',
            JSON.stringify({stay: {price_max: 90, beachfront: true}}))""")
        load()
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        page.click("#stay-filters-btn")
        stay.locator('[data-act="reset"]').click()
        expect(stay.locator('[name="price_max"]')).to_have_value("")
        stay.locator('[data-act="apply"]').click()
        expect(page.locator("#stay-filter-count")).to_be_hidden()
        print("PASS reset to defaults")

        browser.close()
    if errors:
        raise SystemExit(f"FAIL page errors: {errors}")
    print("ALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_api.py -v`
Expected: FAIL `test_index_has_filter_buttons_and_scripts` (no `hike-filters-btn` in the page).
Run: `node tests/ui/filters_logic.test.js`
Expected: FAIL — `Error: Cannot find module '.../static/filters.js'`.

- [ ] **Step 3: Create `static/filters.js`**

```javascript
"use strict";
// Filter values: sanitising, counting, validation and browser storage. No DOM here, so
// tests/ui/filters_logic.test.js can run it under node. `section` is one half of
// GET /api/filters: {fields: [...], defaults: {...}}.

const FILTERS_STORAGE_KEY = "hicktoors.filters.v1";

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}
function optionValues(field) {
  return (field.options || []).map((o) => o.value);
}
function acceptsNumber(value, field, allowNull) {
  if (value === null) return allowNull;
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  if (field.options) return optionValues(field).includes(value);
  if (field.min != null && value < field.min) return false;
  if (field.max != null && value > field.max) return false;
  return true;
}

// Any stored/raw object -> a complete, valid values object; anything unusable falls back to defaults.
function sanitizeSection(raw, section) {
  const out = cloneJson(section.defaults);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const field of section.fields) {
    if (field.type === "multi") {
      const value = raw[field.key];
      if (!Array.isArray(value)) continue;
      const allowed = optionValues(field);
      const kept = [...new Set(value.filter((v) => allowed.includes(v)))];
      if (kept.length || out[field.key].length === 0) out[field.key] = kept;
    } else if (field.type === "range") {
      const lo = field.min_key in raw && acceptsNumber(raw[field.min_key], field, out[field.min_key] === null)
        ? raw[field.min_key] : out[field.min_key];
      const hi = field.max_key in raw && acceptsNumber(raw[field.max_key], field, out[field.max_key] === null)
        ? raw[field.max_key] : out[field.max_key];
      if (lo === null || hi === null || lo <= hi) {
        out[field.min_key] = lo;
        out[field.max_key] = hi;
      }
    } else if (field.type === "number") {
      if (field.key in raw && acceptsNumber(raw[field.key], field, true)) out[field.key] = raw[field.key];
    } else if (field.type === "select") {
      if (field.key in raw && optionValues(field).includes(raw[field.key])) out[field.key] = raw[field.key];
    } else if (field.type === "bool") {
      if (typeof raw[field.key] === "boolean") out[field.key] = raw[field.key];
    }
  }
  return out;
}

function sanitizeFilters(raw, catalogue, defaultGuests) {
  const obj = raw && typeof raw === "object" ? raw : {};
  const guests = Number.isInteger(obj.guests) && obj.guests >= 1 && obj.guests <= 16 ? obj.guests : defaultGuests;
  return {
    hike: sanitizeSection(obj.hike, catalogue.hike),
    stay: sanitizeSection(obj.stay, catalogue.stay),
    guests,
  };
}

function sameValue(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) {
    return JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  }
  return a === b;
}

// One per popup field that differs from its default (a range counts once).
function countActive(values, section) {
  let n = 0;
  for (const field of section.fields) {
    const keys = field.type === "range" ? [field.min_key, field.max_key] : [field.key];
    if (keys.some((k) => !sameValue(values[k], section.defaults[k]))) n += 1;
  }
  return n;
}

// Keys of range fields whose minimum is above their maximum.
function invalidRanges(values, section) {
  return section.fields
    .filter((f) => f.type === "range" && values[f.min_key] !== null && values[f.max_key] !== null
      && values[f.min_key] > values[f.max_key])
    .map((f) => f.key);
}

// Storage lives behind these two functions so a backend store can replace them later.
function loadFilters(catalogue, defaultGuests) {
  let raw = null;
  try {
    raw = JSON.parse(localStorage.getItem(FILTERS_STORAGE_KEY));
  } catch (err) {
    raw = null;
  }
  return sanitizeFilters(raw, catalogue, defaultGuests);
}
function saveFilters(filters) {
  try {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(filters));
  } catch (err) {
    // storage full or disabled: filters still apply for this page session
  }
}

if (typeof module !== "undefined") {
  module.exports = { sanitizeFilters, sanitizeSection, countActive, invalidRanges, loadFilters, saveFilters };
}
```

Run: `node tests/ui/filters_logic.test.js`
Expected: `filters_logic: all assertions passed`.

- [ ] **Step 4: Create `static/dialog.js`**

```javascript
"use strict";
// Modal filter popups generated from the GET /api/filters catalogue. Every label is set with
// textContent; option values travel JSON-encoded in value attributes.

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(name, value === true ? "" : String(value));
  }
  if (text != null) node.textContent = text;
  return node;
}
function sourceTag(sources) {
  return sources ? el("span", { class: "src-tag" }, sources) : null;
}
function controlFor(name, field) {
  if (field.options) {
    const select = el("select", { name });
    for (const o of field.options) {
      select.appendChild(el("option", { value: o.value === null ? "" : JSON.stringify(o.value) }, o.label));
    }
    return select;
  }
  return el("input", { type: "number", name, min: field.min, max: field.max, step: field.step ?? "any",
    placeholder: "Any" });
}
function renderField(field) {
  const box = el("div", { class: "field", "data-key": field.key });
  const head = el("div", { class: "field-head" });
  head.append(el("span", { class: "field-label" }, field.label));
  const tag = sourceTag(field.sources);
  if (tag) head.append(tag);
  box.append(head);
  if (field.type === "multi") {
    const chips = el("div", { class: "chips" });
    for (const o of field.options) {
      const label = el("label", { class: "chip" });
      label.append(el("input", { type: "checkbox", name: field.key, value: JSON.stringify(o.value) }),
        document.createTextNode(` ${o.label}`));
      const optionTag = sourceTag(o.sources);
      if (optionTag) label.append(optionTag);
      chips.append(label);
    }
    box.append(chips);
  } else if (field.type === "range") {
    const row = el("div", { class: "range-row" });
    row.append(controlFor(field.min_key, field), el("span", {}, "–"), controlFor(field.max_key, field));
    if (field.unit) row.append(el("span", { class: "unit" }, field.unit));
    box.append(row);
  } else if (field.type === "number" || field.type === "select") {
    box.append(controlFor(field.key, field));
  } else if (field.type === "bool") {
    const label = el("label", { class: "chip" });
    // disabled: e.g. loop_only without a Wikiloc login (catalogue sets field.disabled)
    label.append(el("input", { type: "checkbox", name: field.key, disabled: Boolean(field.disabled) }),
      document.createTextNode(" Yes"));
    box.append(label);
  }
  return box;
}
function readControl(control) {
  if (control.tagName === "SELECT") return control.value === "" ? null : JSON.parse(control.value);
  return control.value === "" ? null : Number(control.value);
}
function writeControl(control, value) {
  if (control.tagName === "SELECT") control.value = value === null ? "" : JSON.stringify(value);
  else control.value = value === null ? "" : String(value);
}

// shared: [{name, label, type: "date"|"checkbox"|"number"|"slider", min, max, step, format}]
// getShared() -> {name: value}; onApply(values, shared) stores them.
function createFilterDialog({ id, title, section, shared, getValues, getShared, onApply }) {
  const dialog = el("dialog", { id, class: "filters-dialog" });
  const form = el("form", { novalidate: true });
  form.append(el("h2", {}, title));
  const sharedBox = el("fieldset", { class: "shared" });
  sharedBox.append(el("legend", {}, "Search settings"));
  for (const s of shared) {
    const label = el("label", { class: "shared-item" }, `${s.label} `);
    const input = el("input", { type: s.type === "slider" ? "range" : s.type, name: `shared_${s.name}`,
      min: s.min, max: s.max, step: s.step });
    label.append(input);
    if (s.format) {
      const out = el("span", { class: "slider-text" });
      input.addEventListener("input", () => { out.textContent = s.format(Number(input.value)); });
      label.append(out);
    }
    sharedBox.append(label);
  }
  form.append(sharedBox);
  const fields = el("div", { class: "fields" });
  for (const field of section.fields) fields.append(renderField(field));
  form.append(fields);
  const error = el("p", { class: "form-error", role: "alert" });
  form.append(error);
  const buttons = el("div", { class: "dialog-buttons" });
  const reset = el("button", { type: "button", "data-act": "reset" }, "Reset to defaults");
  const cancel = el("button", { type: "button", "data-act": "cancel" }, "Cancel");
  const apply = el("button", { type: "button", "data-act": "apply", class: "primary" }, "Apply");
  buttons.append(reset, cancel, apply);
  form.append(buttons);
  dialog.append(form);
  document.body.append(dialog);

  const control = (name) => form.querySelector(`[name="${name}"]`);
  function fill(values) {
    for (const field of section.fields) {
      if (field.type === "multi") {
        const chosen = new Set(values[field.key].map((v) => JSON.stringify(v)));
        for (const box of form.querySelectorAll(`input[name="${field.key}"]`)) box.checked = chosen.has(box.value);
      } else if (field.type === "range") {
        writeControl(control(field.min_key), values[field.min_key]);
        writeControl(control(field.max_key), values[field.max_key]);
      } else if (field.type === "bool") {
        control(field.key).checked = Boolean(values[field.key]);
      } else {
        writeControl(control(field.key), values[field.key]);
      }
    }
  }
  function read() {
    const values = {};
    for (const field of section.fields) {
      if (field.type === "multi") {
        values[field.key] = [...form.querySelectorAll(`input[name="${field.key}"]:checked`)]
          .map((box) => JSON.parse(box.value));
      } else if (field.type === "range") {
        values[field.min_key] = readControl(control(field.min_key));
        values[field.max_key] = readControl(control(field.max_key));
      } else if (field.type === "bool") {
        values[field.key] = control(field.key).checked;
      } else {
        values[field.key] = readControl(control(field.key));
      }
    }
    return values;
  }
  function fillShared(values) {
    for (const s of shared) {
      const input = control(`shared_${s.name}`);
      if (s.type === "checkbox") input.checked = Boolean(values[s.name]);
      else input.value = String(values[s.name]);
      input.dispatchEvent(new Event("input"));
    }
  }
  function readShared() {
    const values = {};
    for (const s of shared) {
      const input = control(`shared_${s.name}`);
      values[s.name] = s.type === "checkbox" ? input.checked
        : s.type === "date" ? input.value : Number(input.value);
    }
    return values;
  }
  function validate() {
    for (const node of form.querySelectorAll(".invalid")) node.classList.remove("invalid");
    const problems = [];
    for (const input of form.querySelectorAll("input, select")) {
      if (!input.checkValidity()) {
        input.classList.add("invalid");
        problems.push("Check the highlighted values");
      }
    }
    const values = read();
    for (const key of invalidRanges(values, section)) {
      const field = section.fields.find((f) => f.key === key);
      control(field.min_key).classList.add("invalid");
      control(field.max_key).classList.add("invalid");
      problems.push(`${field.label}: minimum is above maximum`);
    }
    const start = control("shared_start"), end = control("shared_end");
    if (start && end && start.value && end.value && end.value < start.value) {
      end.classList.add("invalid");
      problems.push("End date is before start date");
    }
    error.textContent = [...new Set(problems)].join(" · ");
    apply.disabled = problems.length > 0;
    return problems.length === 0;
  }

  form.addEventListener("input", validate);
  form.addEventListener("change", validate);
  reset.addEventListener("click", () => { fill(cloneJson(section.defaults)); validate(); });
  cancel.addEventListener("click", () => dialog.close());
  apply.addEventListener("click", () => {
    if (!validate()) return;
    onApply(read(), readShared());
    dialog.close();
  });
  return {
    dialog,
    open() {
      fill(getValues());
      fillShared(getShared());
      validate();
      dialog.showModal();
    },
  };
}
```

- [ ] **Step 5: Wire the popups into `static/app.js`**

Replace

```javascript
const state = { config: null, hikes: new Map(), stays: new Map(), markers: new Map(), items: new Map(), comboView: false, staysRequest: 0 };
```

with

```javascript
const state = { config: null, hikes: new Map(), stays: new Map(), markers: new Map(), items: new Map(), comboView: false, staysRequest: 0,
  catalogue: null, filters: null, dialogs: {} };
```

Replace

```javascript
const dates = () => ({ start: $("start").value, end: $("end").value });
```

with

```javascript
const dates = () => ({ start: $("start").value, end: $("end").value });
const guests = () => state.filters?.guests ?? state.config?.default_guests ?? 2;

// ---- filter popups ----
function updateFilterBadges() {
  for (const kind of ["hike", "stay"]) {
    const n = state.filters ? countActive(state.filters[kind], state.catalogue[kind]) : 0;
    const badge = $(`${kind}-filter-count`);
    badge.textContent = `•${n}`;
    badge.hidden = n === 0;
  }
}
function applyShared(shared) {
  if ("start" in shared) { $("start").value = shared.start; $("end").value = shared.end; }
  if ("drive" in shared) { $("drive").value = String(shared.drive); $("drive-label").textContent = driveText(driveLimit()); }
  if ("dog" in shared) $("dog").checked = shared.dog;
  if ("guests" in shared) state.filters.guests = shared.guests;
}
function setupFilterDialogs() {
  const dateFields = [{ name: "start", label: "📅 From", type: "date" }, { name: "end", label: "to", type: "date" }];
  const make = (kind, title, shared, readShared) => createFilterDialog({
    id: `${kind}-dialog`, title, section: state.catalogue[kind], shared,
    getValues: () => state.filters[kind],
    getShared: readShared,
    onApply: (values, sharedValues) => {
      state.filters[kind] = values;
      applyShared(sharedValues);
      saveFilters(state.filters);
      updateFilterBadges();
    },
  });
  state.dialogs.hike = make("hike", "⚙️ Hike filters", [
    ...dateFields,
    { name: "drive", label: "🚗 Drive", type: "slider", min: 0, max: DRIVE_STEPS.length - 1, step: 1,
      format: (i) => driveText(DRIVE_STEPS[i]) },
  ], () => ({ ...dates(), drive: Number($("drive").value) }));
  state.dialogs.stay = make("stay", "⚙️ Stay filters", [
    ...dateFields,
    { name: "dog", label: "🐕 Dog", type: "checkbox" },
    { name: "guests", label: "👥 Guests", type: "number", min: 1, max: 16, step: 1 },
  ], () => ({ ...dates(), dog: $("dog").checked, guests: guests() }));
  for (const kind of ["hike", "stay"]) {
    const button = $(`${kind}-filters-btn`);
    button.disabled = false;
    button.addEventListener("click", () => state.dialogs[kind].open());
  }
  updateFilterBadges();
}
```

Replace

```javascript
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit() };
```

with

```javascript
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit(), filters: state.filters?.hike };
```

Replace

```javascript
    const data = await api("/api/stays", { ...dates(), hike_id: id, dog: $("dog").checked,
      guests: state.config?.default_guests ?? 2 });
```

with

```javascript
    const data = await api("/api/stays", { ...dates(), hike_id: id, dog: $("dog").checked,
      guests: guests(), filters: state.filters?.stay });
```

Replace

```javascript
  const data = await api("/api/trips", { ...dates(), drive_limit: driveLimit(), dog: $("dog").checked,
    guests: state.config?.default_guests ?? 2 });
```

with

```javascript
  const body = { ...dates(), drive_limit: driveLimit(), dog: $("dog").checked, guests: guests(),
    hike_filters: state.filters?.hike, stay_filters: state.filters?.stay };
  const data = await api("/api/trips", body);
```

In `init()` replace

```javascript
      $("ask-input").placeholder = "Ask Claude unavailable – run `claude` once to log in";
    }
  } catch (err) {
```

with

```javascript
      $("ask-input").placeholder = "Ask Claude unavailable – run `claude` once to log in";
    }
    state.catalogue = await api("/api/filters");
    state.filters = loadFilters(state.catalogue, state.config.default_guests);
    setupFilterDialogs();
  } catch (err) {
```

(While `state.filters` is null — catalogue failed to load — requests omit the filter fields, `JSON.stringify` drops `undefined`, and the API uses defaults.)

- [ ] **Step 6: Buttons, scripts and styles**

In `static/index.html` replace

```html
    <label class="dog"><input type="checkbox" id="dog" checked> 🐕 Dog</label>
    <fieldset id="modes">
```

with

```html
    <label class="dog"><input type="checkbox" id="dog" checked> 🐕 Dog</label>
    <button type="button" id="hike-filters-btn" class="tool-btn" disabled>⚙️ Hike filters <span id="hike-filter-count" class="count" hidden></span></button>
    <button type="button" id="stay-filters-btn" class="tool-btn" disabled>⚙️ Stay filters <span id="stay-filter-count" class="count" hidden></span></button>
    <fieldset id="modes">
```

and replace

```html
  <script src="/static/app.js"></script>
```

with

```html
  <script src="/static/filters.js"></script>
  <script src="/static/dialog.js"></script>
  <script src="/static/app.js"></script>
```

In `static/style.css` replace

```css
@media (max-width: 800px) {
```

with

```css
.tool-btn { padding: 5px 10px; border: 1px solid #c8d0c6; border-radius: 6px; background: #fff; cursor: pointer; }
.tool-btn:disabled { opacity: .5; cursor: default; }
.tool-btn .count { margin-left: 4px; padding: 0 6px; border-radius: 10px; background: #2f6f4e; color: #fff; font-size: 12px; }
.filters-dialog { width: min(640px, calc(100vw - 32px)); max-height: calc(100vh - 48px); overflow: auto; border: 0; border-radius: 10px; padding: 0; box-shadow: 0 10px 40px rgba(0,0,0,.25); }
.filters-dialog::backdrop { background: rgba(20, 30, 25, .45); }
.filters-dialog form { display: flex; flex-direction: column; gap: 10px; padding: 16px; }
.filters-dialog h2 { margin: 0; font-size: 17px; }
.filters-dialog .shared { display: flex; flex-wrap: wrap; gap: 8px 14px; border: 1px solid #dde3dc; border-radius: 8px; }
.filters-dialog .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 16px; }
.filters-dialog .field-head { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; font-weight: 600; }
.filters-dialog .chips { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.filters-dialog .chip { font-weight: normal; }
.filters-dialog .range-row { display: flex; gap: 6px; align-items: center; }
.filters-dialog input[type=number] { width: 90px; padding: 3px 6px; border: 1px solid #c8d0c6; border-radius: 6px; }
.filters-dialog select { padding: 3px 6px; border: 1px solid #c8d0c6; border-radius: 6px; }
.filters-dialog .invalid { outline: 2px solid #c0392b; }
.filters-dialog .form-error { margin: 0; min-height: 18px; color: #c0392b; }
.filters-dialog .dialog-buttons { display: flex; justify-content: flex-end; gap: 8px; }
.filters-dialog .dialog-buttons button { padding: 6px 14px; border: 1px solid #c8d0c6; border-radius: 6px; background: #fff; cursor: pointer; }
.filters-dialog .dialog-buttons .primary { border: 0; background: #2f6f4e; color: #fff; }
.filters-dialog .dialog-buttons .primary:disabled { opacity: .5; cursor: not-allowed; }
.src-tag { font-size: 10px; font-weight: normal; padding: 0 5px; border-radius: 8px; background: #eef1ed; color: #5d6b63; margin-left: 4px; }
@media (max-width: 800px) {
```

- [ ] **Step 7: Run all checks**

Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.
Run: `node --check static/filters.js && node --check static/dialog.js && node --check static/app.js && node tests/ui/filters_logic.test.js`
Expected: no syntax errors; `filters_logic: all assertions passed`.
Run in the background: `env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765`
Then run: `env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765`
Expected: seven `PASS ...` lines and `ALL UI CHECKS PASSED`. Stop the server afterwards.
Security check: `grep -n "innerHTML" static/filters.js static/dialog.js` → no output.

- [ ] **Step 8: Commit**

```bash
git add static/filters.js static/dialog.js static/app.js static/index.html static/style.css tests/ui/filters_logic.test.js tests/ui/check_ui.py tests/test_api.py
git commit -m "feat(hick-toors): hike/stay filter popups from the catalogue, localStorage, badges

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: UI — Leaflet.draw area, Drawn area mode, Trip plan with area, README

**Files:**
- Modify: `static/index.html`, `static/app.js`
- Modify: `tests/ui/check_ui.py`, `tests/test_api.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 9 `static/app.js` (`state.filters`, `guests()`, `searchHikes`, `planTrip` bodies); API `mode="drawn"` + `area` (hikes) and `area` (trips) from Tasks 1/6.
- Produces (browser globals in `app.js`): `drawnItems` (`L.FeatureGroup`, at most one layer), `drawControl` (`L.Control.Draw`, rectangle + circle only), `drawnArea() -> {shape:"rectangle",south,west,north,east} | {shape:"circle",lat,lon,radius_m} | null`, `areaProblem(area) -> string | null`, `setMode(value)`. DOM: `#draw-btn`, radio `input[name=mode][value=drawn]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py` append at the end:

```python


def test_index_has_draw_tools(tmp_path):
    html = client(tmp_path).get("/").text
    assert "leaflet-draw@1.0.4/dist/leaflet.draw.js" in html and "leaflet-draw@1.0.4/dist/leaflet.draw.css" in html
    assert 'id="draw-btn"' in html and 'value="drawn"' in html
```

In `tests/ui/check_ui.py` replace

```python
        load(clear=True)
        expect(page.locator("#hike-filter-count")).to_be_hidden()
```

with

```python
        def search_and_capture() -> tuple[str, dict]:
            before = len(requests)
            page.click("#search")
            for _ in range(50):
                if len(requests) > before:
                    return requests[-1]
                page.wait_for_timeout(100)
            raise SystemExit("FAIL search sent no request")

        load(clear=True)
        expect(page.locator("#hike-filter-count")).to_be_hidden()
```

and replace

```python
        print("PASS reset to defaults")

        browser.close()
```

with

```python
        print("PASS reset to defaults")

        # Drawn area mode with no shape: notice, no request.
        sent = len(requests)
        page.check('input[name="mode"][value="drawn"]')
        page.click("#search")
        expect(page.locator("#notices")).to_contain_text("Draw a rectangle or circle first")
        assert len(requests) == sent, requests[sent:]
        print("PASS drawn mode without a shape")

        # Real mouse drag with the ✏️ button (exercises Leaflet.draw's rectangle handler).
        page.check('input[name="mode"][value="near_home"]')
        page.click("#draw-btn")
        box = page.locator("#map").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 120, y + 80, steps=8)
        page.mouse.up()
        expect(page.locator('input[name="mode"][value="drawn"]')).to_be_checked()
        assert page.evaluate("drawnItems.getLayers().length") == 1
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        path, body = requests[-1]
        assert path == "/api/hikes" and body["mode"] == "drawn" and body["area"]["shape"] == "rectangle", body
        assert body["area"]["south"] < body["area"]["north"] and body["area"]["west"] < body["area"]["east"], body
        print("PASS rectangle drawn with the mouse and searched")

        # A new shape replaces the old one; Trip plan uses it.
        page.evaluate("""map.fire(L.Draw.Event.CREATED,
            {layer: L.circle([38.5, 22.6], {radius: 8000}), layerType: 'circle'})""")
        assert page.evaluate("drawnItems.getLayers().length") == 1
        page.check('input[name="mode"][value="trip"]')
        path, body = search_and_capture()
        assert path == "/api/trips" and body["area"] == {
            "shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000}, body
        print("PASS circle replaces rectangle; Trip plan sends the area")

        # Without a shape Trip plan falls back to the drive radius (no area sent).
        page.evaluate("drawnItems.clearLayers()")
        path, body = search_and_capture()
        assert path == "/api/trips" and "area" not in body, body
        print("PASS Trip plan without a shape")

        browser.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_api.py -v`
Expected: FAIL `test_index_has_draw_tools`.
(With the server from Task 9 running, `check_ui.py` would fail at "drawn mode without a shape" — the radio doesn't exist yet.)

- [ ] **Step 3: Load Leaflet.draw and add the controls**

In `static/index.html` replace

```html
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
```

with

```html
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css">
```

replace

```html
    <button type="button" id="stay-filters-btn" class="tool-btn" disabled>⚙️ Stay filters <span id="stay-filter-count" class="count" hidden></span></button>
    <fieldset id="modes">
      <label><input type="radio" name="mode" value="near_home" checked> Near home</label>
      <label><input type="radio" name="mode" value="place"> Place</label>
      <label><input type="radio" name="mode" value="trip"> Trip plan</label>
    </fieldset>
```

with

```html
    <button type="button" id="stay-filters-btn" class="tool-btn" disabled>⚙️ Stay filters <span id="stay-filter-count" class="count" hidden></span></button>
    <button type="button" id="draw-btn" class="tool-btn">✏️ Draw area</button>
    <fieldset id="modes">
      <label><input type="radio" name="mode" value="near_home" checked> Near home</label>
      <label><input type="radio" name="mode" value="place"> Place</label>
      <label><input type="radio" name="mode" value="trip"> Trip plan</label>
      <label><input type="radio" name="mode" value="drawn"> Drawn area</label>
    </fieldset>
```

and replace

```html
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
```

with

```html
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
  <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
```

- [ ] **Step 4: Drawing logic in `static/app.js`**

Replace

```javascript
let reachCircle = null;
```

with

```javascript
let reachCircle = null;

// ---- drawn search area (Leaflet.draw: rectangle + circle only, one shape at a time) ----
const SHAPE_STYLE = { color: "#b5562f", weight: 2, fillOpacity: 0.08 };
const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    polyline: false, polygon: false, marker: false, circlemarker: false,
    rectangle: { shapeOptions: SHAPE_STYLE, showArea: false },
    circle: { shapeOptions: SHAPE_STYLE },
  },
  edit: { featureGroup: drawnItems },
});
map.addControl(drawControl);
map.on(L.Draw.Event.CREATED, (event) => {
  drawnItems.clearLayers();
  drawnItems.addLayer(event.layer);
  setMode("drawn");
});
function drawnArea() {
  const layer = drawnItems.getLayers()[0];
  if (!layer) return null;
  if (layer instanceof L.Circle) {
    const c = layer.getLatLng();
    return { shape: "circle", lat: c.lat, lon: c.lng, radius_m: Math.round(layer.getRadius()) };
  }
  const b = layer.getBounds();
  return { shape: "rectangle", south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() };
}
function areaProblem(area) {
  if (!area) return "Draw a rectangle or circle first";
  if (area.shape === "circle" && (area.radius_m < 200 || area.radius_m > 300000)) {
    return "Draw a circle with a radius between 200 m and 300 km";
  }
  if (area.shape === "rectangle" && (area.south >= area.north || area.west >= area.east)) {
    return "Draw a bigger rectangle";
  }
  return null;
}
```

Replace

```javascript
const guests = () => state.filters?.guests ?? state.config?.default_guests ?? 2;
```

with

```javascript
const guests = () => state.filters?.guests ?? state.config?.default_guests ?? 2;
function setMode(value) {
  document.querySelector(`input[name=mode][value="${value}"]`).checked = true;
  $("place").hidden = value !== "place";
}
```

In `searchHikes()` replace

```javascript
  if (body.mode === "place") body.place = $("place").value;
  const data = await api("/api/hikes", body);
```

with

```javascript
  if (body.mode === "place") body.place = $("place").value;
  if (body.mode === "drawn") {
    const area = drawnArea();
    const problem = areaProblem(area);
    if (problem) { notices([problem]); return; }
    body.area = area;
  }
  const data = await api("/api/hikes", body);
```

In `planTrip()` replace

```javascript
    hike_filters: state.filters?.hike, stay_filters: state.filters?.stay };
  const data = await api("/api/trips", body);
```

with

```javascript
    hike_filters: state.filters?.hike, stay_filters: state.filters?.stay };
  const area = drawnArea();  // a drawn shape replaces the drive-time radius for Trip plan
  if (area) {
    const problem = areaProblem(area);
    if (problem) { notices([problem]); return; }
    body.area = area;
  }
  const data = await api("/api/trips", body);
```

In `init()` replace

```javascript
  $("search").addEventListener("click", () =>
```

with

```javascript
  $("draw-btn").addEventListener("click", () => {
    new L.Draw.Rectangle(map, drawControl.options.draw.rectangle).enable();
  });
  $("search").addEventListener("click", () =>
```

- [ ] **Step 5: README**

In `README.md` replace

```markdown
- **🐕 Dog:** stays must allow pets (🐕) or have a garden (⚠️ pets not confirmed, shown last).
```

with

```markdown
- **🐕 Dog:** stays must allow pets (🐕) or have a garden (⚠️ pets not confirmed, shown last).
- **⚙️ Filters:** *Hike filters* (activity, difficulty, distance, elevation gain, TrailRank, order) and *Stay filters* (price per night, rating, entire place, type, rooms, amenities, cancellation, …) open popups; **Apply** stores them in this browser, nothing searches until you press Search. `•N` shows how many differ from the defaults. Hike filters are applied after fetching from Wikiloc (more routes are fetched when filters are on). A stay source that can't apply a chosen filter is skipped with a notice (e.g. Booking.com can't filter by fireplace).
- **✏️ Draw area:** draw a rectangle (button) or a circle (map toolbar) to search hikes inside it (*Drawn area* mode). With *Trip plan* selected, a drawn shape replaces the drive radius.
- **Wikiloc login (optional):** put your logged-in Wikiloc browser cookie in `.env` as `WIKILOC_COOKIE=...` (never commit it). Wikiloc then filters hikes server-side and *Circular routes only* works; without it hikes are filtered locally and that option is disabled. If the login expires the app falls back to local filtering and says so.
```

and replace

````markdown
## Tests
```bash
uv run pytest
```
````

with

````markdown
## Tests
```bash
uv run pytest
node tests/ui/filters_logic.test.js
# headless Chrome check (needs Google Chrome; start the app on port 8765 first)
uv run uvicorn app.main:app --port 8765 &
uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765
```
````

- [ ] **Step 6: Run all checks**

Run: `env -u VIRTUAL_ENV uv run pytest`
Expected: all PASS.
Run: `node --check static/app.js && node tests/ui/filters_logic.test.js`
Expected: no syntax error; `filters_logic: all assertions passed`.
Run in the background: `env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765`
Then run: `env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765`
Expected: eleven `PASS ...` lines and `ALL UI CHECKS PASSED`. Stop the server afterwards.

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/app.js tests/ui/check_ui.py tests/test_api.py README.md
git commit -m "feat(hick-toors): draw a rectangle/circle to search hikes; trip plan inside the shape

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage map

| Spec section | Task |
|---|---|
| §2 buttons, •N badges, Drawn area mode | 9, 10 |
| §2 popups from `/api/filters`, prefilled, shared settings synced, Apply/Reset/Cancel, source tags, min>max validation | 8, 9 |
| §2 localStorage behind `loadFilters()/saveFilters()`, corrupt → defaults | 9 |
| §2 Leaflet.draw 1.0.4 rectangle + circle, replace/edit/delete, switch to Drawn area | 10 |
| §2 Trip plan uses the shape when present; Drawn area without shape → notice, no request | 6, 10 |
| §3.1 Wikiloc sort only, filters in engine, `hike_pages_filtered = 10` | 3, 6 |
| §3.1a Wikiloc login mode: `WIKILOC_COOKIE`, server params, `loop_only`, expiry fallback, `wikiloc_login` in `/api/filters`, disabled checkbox | 7, 8, 9, 10 (README) |
| §3.2 Airbnb params | 4 |
| §3.3 Booking nflt, en-gb endpoint, fresh session per attempt, retry on 202 | 5 |
| §4.1 HikeFilters + missing-value rule | 1, 2 |
| §4.2 StayFilters, mapping table, post-checks, provider skipping | 1, 2, 4, 5, 6 |
| §4.3 DrawnArea, clamp, ≥ 5 km, validation, circle trim | 1, 6 |
| §5 `app/filters.py`, models, API, MCP args, cache keys | 1, 2, 6, 7, 8 |
| §6 422s, skip notices, "No source…", "No … match your filters" | 1, 6, 8 |
| §7 tests (provider, filters, engine, API, MCP, node --check, headless Chrome) | 2–10 |
