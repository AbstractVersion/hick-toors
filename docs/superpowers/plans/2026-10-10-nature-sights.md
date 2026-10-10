# hick-toors — Nature Sights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A 🌿 Nature map layer (OpenStreetMap/Overpass) for the visible area, four category chips, and nature sights in saved trips + Markdown.

**Architecture:** `app/sources/nature.py` (tiles, Overpass query, parser, endpoint fallback) → `Engine.nature()` with a per-tile 7-day disk cache → `GET /api/nature` + MCP `nature_sights`; trips gain `sights`; `static/nature.js` owns the layer/toggle/chips; `static/trips.js` handles `sight` items.

**Tech Stack:** Python 3.10, FastAPI, pydantic v2, httpx, pytest; vanilla JS + Leaflet; Playwright UI check.

**Spec:** `docs/superpowers/specs/2026-10-10-nature-sights-design.md` — the binding authority for every exact value (tags, emojis, limits, messages, URLs). Each task brief must be read together with the spec section it names.

## Global Constraints

- Project folder `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors`, branch `feat/hick-toors`. Never read/print/commit `.env`. Sandbox refuses compound git commands (`cd x && git`, `git -C`): plain git commands only.
- `env -u VIRTUAL_ENV uv run pytest -q` stays green (419 passing at start). Tests never touch the network: Overpass is reached only through an injectable fetch callable.
- Python 3.10; no new dependencies.
- Messages verbatim from the spec: `"Zoom in to see nature sights"`, `"Nature sights unavailable for part of the map – try again in a minute"`, `"Give exactly one of hike, stay or sight"`, `"A trip holds at most 100 nature sights"`.
- Stored trips: only add optional fields (old documents must keep loading); `Sight.lines` never stored in trips.
- UI: strings via textContent/esc(); links only for http(s); no window.confirm/alert/prompt; the nature layer is never cleared by `clearAll()`/`clearHikes()`.
- Commits end with a blank line + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. Overpass busy (504) / mirror down / invalid JSON → second endpoint, then a warning; the UI keeps working and does not spam requests while panning (debounce + stale-response guard). (Task 1 tests; Task 3 UI check.)
2. Huge relations (Natura 2000, long rivers) → clipped to tile, simplified, capped (≤ 40 lines, ≤ 2,000 points per sight) so responses stay small. (Task 1 test.)
3. Unnamed springs/peaks → shown with the kind label, not blank. (Task 1 test; Task 2 Markdown test.)
4. Old saved trips (no `sights` key) load and export exactly as before. (Task 2 test.)
5. ＋ Trip on a sight popup adds it; a sight already in the trip says "Already in". (Task 3 UI check.)

---

### Task 1: Sight model, Overpass source, engine, API, MCP

**Files:** Modify `app/models.py`, `app/engine.py`, `app/main.py`, `app/mcp_tools.py`, `app/wiring.py`, `app/ask.py` (one sentence naming `nature_sights`). Create `app/sources/nature.py`, `tests/test_nature_source.py`, `tests/test_nature_api.py`.

**Interfaces — Produces:** `Sight`, `SightKind`, `SightGroup`, `SIGHT_GROUPS`, `NatureSearch` (app.models); `nature.tiles_for(bbox: BBox) -> list[tuple[float, float]]` (SW corners, 0.25° grid), `nature.tile_query(south, west) -> str`, `nature.parse_overpass(data: dict) -> list[Sight]`, `nature.simplify(points, tol) -> list`, `class NatureSource(fetch=None)` with `async fetch_tile(south, west) -> list[Sight]` (endpoint fallback; raises `NatureUnavailable`), `MAX_TILES = 16`; `Engine.nature(bbox: BBox, groups: list[SightGroup]) -> NatureSearch`; route `GET /api/nature`; MCP `nature_sights`.

- [ ] Step 1 — Write failing tests (`tests/test_nature_source.py`), with a canned Overpass JSON fixture built in the test file covering: a waterfall node with `name:en` and `name` (name:en wins) and `ele=120`; an unnamed spring node (name "Spring"); a peak with `ele="1981 m"` → 1981 and `wikipedia="el:Μαίναλο"` → `https://el.wikipedia.org/wiki/Μαίναλο`; a `natural=water`+`water=lake` way with `center` + `geometry` (lines: one); a river relation with two member ways with geometry (two lines); a protected-area relation with 60 member ways (capped to 40 lines); a way with 5,000 collinear points (simplified to 2 points); `amenity=public_bath`+`bath:type=hot_spring` → hot_spring; `natural=valley`+`valley=canyon` → gorge; `natural=rock` without name → skipped; unknown tags → skipped; ids `osm:node/1` etc.; urls `https://www.openstreetmap.org/node/1`. Tiling: bbox 37.55,22.10–37.75,22.35 → tiles (37.5,22.0),(37.5,22.25) ; negative coords snap down (floor); a bbox needing 17+ tiles → `ValueError`. Fallback: fake fetch raising on endpoint 1 and returning JSON on endpoint 2 → sights; both failing / invalid JSON → `NatureUnavailable`. Name cut to 120 chars.
- [ ] Step 2 — Run, see them fail (ImportError).
- [ ] Step 3 — Implement `app/sources/nature.py` per spec §Source (query text must include `[out:json][timeout:25]`, the tile bbox, nodes for point kinds, ways/relations for area/line kinds, `out tags center geom(<s>,<w>,<n>,<e>) 600;`). Douglas–Peucker iterative or recursive with depth safety (Python recursion limit: use an explicit stack).
- [ ] Step 4 — Engine (`tests/test_nature_api.py` first): `Engine.nature` with per-tile cache key `nature:v1:<s>_<w>` (2 decimals), TTL `7 * DAY`; failures not cached; group filter via `SIGHT_GROUPS`; merge by id (lines concatenated then re-capped); partial failure warning once; concurrency 2 (asyncio.Semaphore). Wire `NatureSource()` into `build_engine` as `nature=` (Engine gets an optional `nature=None` parameter so existing test engines keep working).
- [ ] Step 5 — API: `GET /api/nature` with query params `south, west, north, east: float`, `groups: str | None` (comma list). Validation → 422 with readable `detail` (`"Zoom in to see nature sights"` for > MAX_TILES; `"Unknown group: x"`; `"south must be < north and west < east"`). Tests with a fake engine and with the real Engine + fake NatureSource (cache hit: second request doesn't call fetch).
- [ ] Step 6 — MCP `nature_sights(lat, lon, radius_km=10, groups=None)` (radius 1..30 else `{"error": …}`; bbox from `app.geo.bbox_around`). Test via monkeypatched engine. One sentence in the Ask system prompt: use `nature_sights` for natural sights (waterfalls, springs, gorges, viewpoints…).
- [ ] Step 7 — Optional real check: at most 2 real Overpass requests total through `NatureSource` for tile (37.5, 22.0) to validate the parser; report counts per kind. Never in tests.
- [ ] Step 8 — Full suite green; commit `feat(hick-toors): nature sights from OpenStreetMap (source, cache, API, MCP)`.

### Task 2: Sights in saved trips + Markdown

**Files:** Modify `app/models.py`, `app/trips_store.py`, `app/main.py` (409 mapping), `app/trip_markdown.py`; tests in `tests/test_models.py`, `tests/test_trips_store.py`, `tests/test_api_saved_trips.py`, `tests/test_trip_markdown.py`.

**Interfaces — Consumes:** `Sight` (Task 1). **Produces:** `SavedTripIn.sights`, `TripItemAdd.sight`, `TripFull` carries which limit (`TripFull(kind)`), store `MAX_SIGHTS = 100`, Markdown section.

- [ ] Step 1 — Failing tests: `SavedTripIn(sights=[…])` dedupes by id, rejects 101, rejects NaN lat, **drops `lines`** (stored sight has `lines == []`); an old JSON doc without `sights` validates as `SavedTrip` with `sights == []`; `TripItemAdd()` / two items → `"Give exactly one of hike, stay or sight"`; `add_item` with a sight appends/dedupes; 101st sight → `TripFull` → API 409 `"A trip holds at most 100 nature sights"` (hikes/stays keep `"A trip holds at most 50 hikes and 50 stays"`); the 256 KiB size check still applies; Markdown: section omitted when empty (the existing exact `FULL`/`EMPTY` expectations stay byte-identical), and with a waterfall (`ele` 120, wikipedia) + unnamed spring renders exactly:
  ```
  ## 🌿 Nature (2)

  - 💧 **[Kalavrita falls](https://www.openstreetmap.org/node/1)** – Waterfall · 120 m · [Wikipedia](https://en.wikipedia.org/wiki/Kalavrita_falls) · [📍 Map](https://www.google.com/maps/search/?api=1&query=38.0,22.1)
  - 🚰 **[Spring](https://www.openstreetmap.org/node/2)** – Spring · [📍 Map](https://www.google.com/maps/search/?api=1&query=38.1,22.2)
  ```
  placed after the Stays section and before Notes (blank line before and after, like other sections). Kind labels: Waterfall, Spring, Hot spring, Lake, River, Peak, Gorge, Cave, Arch, Rock, Viewpoint, Beach, Protected area, Forest. Emojis from the spec table.
- [ ] Step 2 — Implement (reuse `_check_item_numbers` for sights — extend it to accept `Sight`; field validator that strips `lines`; `_link`/`md_escape` for names/urls).
- [ ] Step 3 — Full suite; commit `feat(hick-toors): nature sights in saved trips and Markdown export`.

### Task 3: UI — 🌿 Nature layer, chips, ＋ Trip, editor; UI check; README

**Files:** Create `static/nature.js` (loaded after `app.js`, before `trips.js`). Modify `static/index.html`, `static/style.css`, `static/trips.js`, `static/app.js` (only if a hook is needed, e.g. `itemOf` lookup for sights lives in trips.js), `tests/ui/check_ui.py`, `README.md`.

**Interfaces — Consumes:** `GET /api/nature` (Task 1), trip `sights` + `TripItemAdd.sight` (Task 2), app.js globals (`map`, `pin`, `esc`, `safeUrl`, `api`, `notices`, `startLoading/stopLoading`), trips.js `tripButtonHtml`. **Produces:** `state.sights` Map; `showTripSights(sights)` used by trips.js Show on map.

- [ ] Step 1 — Markup + CSS per spec §UI: `#nature-btn` in the header, `#nature-chips` (four `<button aria-pressed>` chips) and `#nature-hint` inside `#map-wrap`, a load chip `data-spin="nature"`.
- [ ] Step 2 — `static/nature.js`: toggle + chips persisted (try/catch localStorage); debounced (600 ms) loads on moveend/zoomend/toggle/chip; zoom < 10 → hint, no request, layer cleared; request id guard; padded bounds (10 %); groups param from active chips (no chips → clear, no request); render points (emoji pins, class `sight`) and `lines` (rivers blue solid, protected/forest dashed green, others thin blue); popup per spec with `tripButtonHtml("sight", id)`; `state.sights` updated with every rendered sight; warnings → `notices()` only when they differ from the last shown nature warning (no spam); trip sights group for Show on map.
- [ ] Step 3 — `static/trips.js`: kind `"sight"` everywhere items are handled (`itemOf`, `newTripWith`, `addToTrip` counts via `sight_count`? — use `trip.sights.length` from the response and the summary's counts: add `sight_count` to `SavedTripSummary` in Task 3 only if needed; prefer reading lengths from the full trip response), editor `🌿 Nature` list with ↑ ↓ ✕ (same renderer as hikes), list rows show `· N sights` when > 0, Show on map calls `showTripSights`.
- [ ] Step 4 — `tests/ui/check_ui.py`: route `**/api/nature**` to canned JSON (a waterfall point, a river with `lines`); assert: toggle on at low zoom shows the hint and makes no request; zoom ≥ 10 → one request with the expected groups and bbox, pins + polyline rendered; chip off → request without that group; panning quickly makes one request (debounce); a stale response is ignored; ＋ Trip on the waterfall popup → menu → saved trip → notice; editor lists it under 🌿 Nature; export contains `## 🌿 Nature (1)`; Search hikes / Search all keep the nature pins. Run per docstring with a throwaway TRIPS_DB on port 8765 (never touch port 8000).
- [ ] Step 5 — README bullet for 🌿 Nature (source OpenStreetMap © contributors, ODbL; cache 7 days; zoom ≥ 10). Add `© OpenStreetMap contributors` attribution note in the popup footer or the layer attribution (`L.layerGroup` has none — add attribution via `map.attributionControl.addAttribution` when the toggle is on, remove when off).
- [ ] Step 6 — pytest + node --check + UI check green; commit `feat(hick-toors): 🌿 Nature layer with category chips, sights in trips`.
