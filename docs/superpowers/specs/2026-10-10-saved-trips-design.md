# Saved trips — design

Date: 2026-10-10 · Status: approved in chat ("yes go on")

## Goal

Keep trips you like. A trip is a named bucket of hikes and stays (plus dates, guests, dog and
notes) that you build from **⭐ Suggest trips** picks or from any hike/stay search result, store on
the backend, edit later, and export as a Markdown file.

## Decisions (from the brainstorm)

- **Storage:** backend SQLite (stdlib `sqlite3`, no new dependency). One shared trip list.
- **Access:** no protection — anyone who can open the site can view/change/delete trips.
- **Shape:** bucket — name, dates, guests, dog, notes, ordered hikes, ordered stays. No per-day
  itinerary.
- **Snapshots:** each saved item stores a full copy of the `Hike` / `Stay` as seen when added
  (price/rating are "as seen"); a trip still renders when the sources are blocked later.
- **Ask Claude / MCP:** read-only (`list_saved_trips`, `get_saved_trip`).

## Data model (`app/models.py`)

```
SavedTripIn            # body of POST / PUT
  name: str            # stripped; 1..120 chars, blank -> 422
  start: date | None = None
  end: date | None = None        # if both set: end >= start, else 422
  guests: int = 2      # 1..16
  dog: bool = True
  notes: str = ""      # <= 5000 chars
  source: "manual" | "suggested" = "manual"
  score: float | None = None     # combo score when saved from a Trip pick (0..100)
  hikes: list[Hike] = []         # <= 50; duplicates by id dropped, first occurrence kept
  stays: list[Stay] = []         # <= 50; same dedupe

SavedTrip(SavedTripIn)
  id: str              # uuid4 hex
  created_at: datetime # UTC
  updated_at: datetime # UTC

SavedTripSummary
  id, name, start, end, source, hike_count, stay_count, updated_at

TripItemAdd            # body of POST /items: exactly one of hike / stay, else 422
  hike: Hike | None = None
  stay: Stay | None = None
```

## Storage (`app/trips_store.py`)

- `TripStore(path: Path)`; creates parent dirs and the schema on construction.
- Table `trips(id TEXT PRIMARY KEY, doc TEXT NOT NULL, updated_at TEXT NOT NULL)`; `doc` is the
  `SavedTrip` JSON. `PRAGMA user_version = 1`. A connection per call (`timeout=10`), so it is safe
  from FastAPI's threadpool.
- Methods: `list_trips() -> list[SavedTripSummary]` (newest `updated_at` first), `get(id)`,
  `create(SavedTripIn) -> SavedTrip`, `replace(id, SavedTripIn) -> SavedTrip | None` (keeps
  `id`/`created_at`, bumps `updated_at`), `delete(id) -> bool`,
  `add_item(id, TripItemAdd) -> SavedTrip | None` (appends; an item already present by id is a
  no-op that still returns the trip; a 51st item raises `TripFull`).
- Setting `trips_db: Path = BASE_DIR / "data" / "trips.db"` (env `TRIPS_DB`). Outside `.cache/`,
  so clearing the cache never loses trips. `data/` is git- and docker-ignored.
- Docker: `/app/data` owned by the `hick` user; compose mounts the named volume `hick-data` there.

## API (`app/main.py`, sync handlers)

| Method | Path | Result |
|---|---|---|
| GET | `/api/saved-trips` | `list[SavedTripSummary]` |
| POST | `/api/saved-trips` | 201 `SavedTrip` |
| GET | `/api/saved-trips/{id}` | `SavedTrip` / 404 |
| PUT | `/api/saved-trips/{id}` | `SavedTrip` / 404 (full replace) |
| DELETE | `/api/saved-trips/{id}` | 204 / 404 |
| POST | `/api/saved-trips/{id}/items` | `SavedTrip` / 404 / 409 when full |
| GET | `/api/saved-trips/{id}/export.md` | `text/markdown; charset=utf-8`, attachment / 404 |

404 detail: `"Trip not found"`. 409 detail: `"A trip holds at most 50 hikes and 50 stays"`.
Export filename: `slug(name) + ".md"` — lowercase ASCII letters/digits, other runs become `-`,
trimmed, max 60 chars, `trip` when empty; header also carries `filename*=UTF-8''<percent-encoded
original name>.md`. `create_app(..., trip_store=None)` builds `TripStore(settings.trips_db)` when
none is passed.

## Markdown (`app/trip_markdown.py`)

`trip_markdown(trip: SavedTrip, today: date) -> str`, pure. Layout:

```
# Zagori long weekend

📅 Fri 23 Oct 2026 → Sun 25 Oct 2026 (2 nights) · 👥 2 guests · 🐕 With dog
⭐ Saved from Suggest trips – score 77.5/100

## 🥾 Hikes (1)

1. **[Vikos Gorge](https://es.wikiloc.com/…)** – 12 km · +600 m · Moderate · TrailRank 70 · 🚗 2h 10m
   Near Monodendri · [📍 Map](https://www.google.com/maps/search/?api=1&query=39.9,20.7)

## 🏡 Stays (1)

- **[Stone house](https://www.airbnb.com/rooms/1)** – Airbnb · €190 total (€95/night) · ★ 4.8 · 🐕 Pets allowed
  [📍 Map](https://www.google.com/maps/search/?api=1&query=39.95,20.75)

## 📝 Notes

Bring the dog's water bowl.

---
Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved.
```

Rules: no dates → the 📅 part reads `📅 Dates not set`; `(N nights)` / per-night price only when
both dates are set and N > 0 (`1 night` singular); dog off → no 🐕 part; source manual → no ⭐
line; missing hike fields are skipped (no `?`); `🚗` shows `⛴️ ferry needed` when `ferry_needed`;
drive minutes formatted like the UI (`≈ ` prefix when estimated); badge text matches the UI
badges; empty hikes/stays → `_None yet._`; empty notes → section omitted. Names are escaped for
Markdown (`\ ` ` * _ [ ] < >` get a backslash); non-http(s) URLs render the name without a link.
Money: EUR or missing currency → `€`, otherwise `CUR ` prefix; rounded to whole units.

## UI (`static/trips.js`, loaded after `app.js`)

- Header button **🧳 My trips (N)** (`#trips-btn`), count refreshed after every change.
- **＋ Trip** button on every hike and stay list item (click does not select the item) and in their
  map popups. It opens a small menu: **➕ New trip** then saved trips (last used first, remembered
  in localStorage). New trip = POST with the item, name = item name, form dates/guests/dog, then a
  notice `Added to “X” – rename it in 🧳 My trips`. Existing trip = POST `/items`; notice
  `Added to “X”` (or `Already in “X”` when nothing changed).
- Each Trip pick gets **💾 Save** → POST `{source: "suggested", score, hikes: [hike], stays: [stay],
  name: "<hike> + <stay>" (cut to 120), form dates/guests/dog}`; button turns into `✓ Saved`.
- **My trips dialog** (`<dialog id="trips-dialog">`): list view (name, dates, `N hikes · M stays`,
  newest first, empty state text) → editor view: name, dates, guests, dog, notes; hikes and stays
  with ↑ ↓ ✕; buttons **Save changes** (PUT), **Show on map** (clears results, pins the trip's
  items, fits the map, closes the dialog), **⬇ Export .md** (download link), **Delete** (two-step:
  the button becomes **Confirm delete** for 4 s; no `window.confirm`), **← All trips**. Unsaved
  edits are kept only until the dialog is closed. Errors show in the dialog.
- All text via `textContent`; links only for http(s) URLs.

## MCP (`app/mcp_tools.py`)

- `list_saved_trips() -> str` JSON list of summaries.
- `get_saved_trip(trip_id: str) -> str` the trip's Markdown, or JSON `{"error": "Trip not found"}`.
- The store is built lazily from `Settings().trips_db`. `ask.py` already allows every
  `mcp__hicktoors` tool, so Ask Claude can read trips.

## Out of scope

Auth, per-user lists, day-by-day itineraries, refreshing saved prices, importing Markdown.

## Testing

Store (tmp DB: CRUD, ordering, dedupe, full, persistence across instances), models validation,
Markdown (exact expected text for a full trip, plus empty/no-dates/foreign-currency/escaping
cases), API (each route + 404/409/422, export headers), MCP tools, and the Playwright UI check
(save a pick, add from list, edit/reorder/save, export link, delete) against a server started with
a throwaway `TRIPS_DB`.
