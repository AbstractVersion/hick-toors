# hick-toors — Design Spec

Date: 2026-10-09
Status: Draft for review

## 1. Purpose

A local website (runs only on the owner's machine) for planning the next hike in
**Greece** together with a place to stay, travelling with a dog.

Success looks like: pick the days you have, toggle 🐕, and see on a map the hikes
that fit your travel time plus the stays near them that accept a dog — or ask
Claude in plain language and get the same kind of results on the map.

### Out of scope (v1)
- Public deployment, user accounts, booking/payment.
- Dog-based filtering of hikes (all hikes are considered dog-OK).
- Ferry schedules / ferry pet policies (islands are only marked "ferry needed").
- Follow-up conversation in the Ask box (each question is independent).
- Calling the Anthropic API directly (owner has a Claude Code subscription, no API credits).

## 2. User-facing behaviour

### 2.1 Search modes (one page, one form)
| Mode | Input | Result |
|---|---|---|
| Place | place name (geocoded to a bounding box) | hikes in that area; stays near a selected hike |
| Near home | home (default Athens) + trip window | hikes within the drive-time limit; stays near a selected hike |
| Trip plan | home + trip window | ranked hike+stay combos |

### 2.2 Trip window → travel radius
The user picks dates; the number of days sets the default max driving time from home:

| Days | Max drive time |
|---|---|
| 1 | 1.5 h |
| 2–3 | 4 h |
| 4+ | unlimited (islands/Crete included, flagged "ferry needed") |

A slider overrides the value. Bands live in config.

### 2.3 Dog toggle
- **ON**: a stay is shown only if it allows pets **or** has a garden.
  - Pets allowed → badge 🐕 "Pets allowed".
  - Has garden → badge 🌳 "Garden".
  - Garden but pets not confirmed → badge ⚠️ "Garden – pets not confirmed", ranked
    below pet-friendly stays.
  - Neither → hidden.
- **OFF**: normal search; badges still shown where known.
- Hikes are never filtered by the dog toggle.

### 2.4 Map (Leaflet)
- Tile layers (switcher): OpenTopoMap (default), OpenStreetMap, Esri World Imagery. No API keys.
- 🥾 Hike pins at trailheads; popup: name, distance, elevation gain, difficulty,
  TrailRank, nearest village, drive time, Wikiloc link.
- Selected hike: route line drawn if a track is obtainable without login (verified in
  Task 1); otherwise pin + link only.
- 🏡 Stay pins coloured by source (Airbnb / Booking), price on pin, dog badge.
- 🏠 Home marker + shaded approximate reach area.
- Marker clustering when zoomed out (Leaflet.markercluster).
- List ⇄ map highlighting both ways. Selecting a hike triggers a stay search around its
  trailhead and fits both in view.
- Trip-plan mode: dashed line between hike and stay of each combo, labelled with score.
- Ask Claude results render on the same map.

### 2.5 Ask Claude box
Free-text question → short written plan + hikes/stays shown on the map.
Spinner with elapsed time; 120 s timeout; one request at a time. If `claude` CLI is
missing or not logged in, the box shows "Ask Claude unavailable – run `claude` once to
log in" and the rest of the site works normally. Usage counts against the owner's
Claude Code plan.

## 3. Architecture

Python 3.10+, own `uv` project inside `hick-toors/`. FastAPI backend, static
HTML/JS/CSS frontend (no build step). The existing `mcp-server.py` in the repo root is
left untouched; the Wikiloc logic is copied/adapted into `hick-toors`.

```
hick-toors/
  pyproject.toml
  .env.example
  mcp.json                 MCP config used by the Ask box
  app/
    main.py                FastAPI: static page + JSON API
    config.py              settings from .env (home, bands, weights, radius, ORS key)
    models.py              Hike, Stay, Combo, SearchQuery, SearchResult
    trip_window.py         dates → days → max drive minutes
    geocode.py             place name → bbox (Nominatim, cached)
    cache.py               disk cache with TTL
    sources/
      wikiloc.py           bbox search → [Hike]; optional track fetch
      stays/
        base.py            StayProvider protocol
        airbnb.py
        booking.py
    dog_rule.py            dog filter + badge + ordering
    routing.py             drive times home → trailheads
    ranking.py             combo scoring
    engine.py              search_hikes / search_stays / plan_trip (single entry point)
    mcp_tools.py           FastMCP server exposing engine functions
    ask.py                 runs `claude -p`, resolves ids
  static/
    index.html  app.js  style.css
  tests/
    fixtures/              saved sample responses (Wikiloc JSON, Airbnb/Booking HTML)
```

### 3.1 Unit responsibilities
- **models.py** — pydantic models. Every Hike/Stay has a stable `id` (`"wikiloc:<id>"`,
  `"airbnb:<id>"`, `"booking:<id>"`).
- **trip_window.py** — pure function `max_drive_minutes(start, end, override=None) -> int | None`
  (`None` = unlimited).
- **sources/wikiloc.py** — `search(bbox, query="", limit) -> list[Hike]` using
  `find.do?event=map`; retries with backoff on 403. Parsing separated from HTTP so it is
  testable with fixtures.
- **StayProvider** — `async search(center, radius_km, check_in, check_out, guests, dog) -> list[Stay]`.
  Raises a `ProviderUnavailable` error on block/parse failure.
  - **airbnb.py** — dog ON: search with `pets=1` → `pets_allowed=True`. For top N results,
    fetch details and match amenities (Garden, Backyard, Private yard, Fenced) → `has_garden`.
  - **booking.py** — dog ON: "Pets allowed" filter → `pets_allowed=True`; Garden facility →
    `has_garden`. Expected to be fragile; failures are soft.
- **dog_rule.py** — pure: `apply(stays, dog) -> list[Stay]` with badge assignment and
  ordering (pets-allowed first, garden-only ⚠️ after, others removed when dog ON).
  Note: garden-only stays come from Booking (pets unknown) or Airbnb listings with garden
  whose pet status is unknown; Airbnb `pets=1` results are always pets-allowed.
- **routing.py** — OpenRouteService matrix API (home → trailheads), cached 7 days.
  Without key or on failure: haversine distance ÷ 60 km/h, marked `estimated=True`.
- **ranking.py** — combo score /100: hike TrailRank 40, stay rating 25, trailhead–stay
  proximity 20, price 15 (cheaper higher). Normalised within the result set. Weights in config.
  Returns breakdown per combo.
- **engine.py** — orchestrates sources, routing, dog rule, ranking; always returns
  `SearchResult{results, warnings[]}`. Registers every returned object in an in-memory
  id→object registry (used by the Ask box).
- **mcp_tools.py** — FastMCP server `hicktoors` with tools `search_hikes`, `search_stays`,
  `plan_trip`, calling the engine; returns JSON including ids.
- **ask.py** — builds and runs:
  ```
  claude -p "<question + form context>" \
    --mcp-config hick-toors/mcp.json --strict-mcp-config \
    --allowedTools "mcp__hicktoors__*" \
    --append-system-prompt "<trip planner instructions>" \
    --output-format json
  ```
  Claude is instructed to finish with JSON `{summary, hike_ids, stay_ids, combos}`.
  Because the MCP server runs in a separate process, it persists returned objects to the
  disk cache keyed by id; `ask.py` resolves ids from there. Unknown ids are dropped with a
  warning. Timeout 120 s; an asyncio lock allows one run at a time.

### 3.2 API
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the page |
| GET | `/api/config` | home, bands, defaults, whether Ask is available |
| POST | `/api/hikes` | `{mode, place?, home?, start, end, max_drive_minutes?}` → hikes |
| POST | `/api/stays` | `{hike_id or center, radius_km?, start, end, guests, dog}` → stays |
| POST | `/api/trips` | `{home?, start, end, max_drive_minutes?, guests, dog}` → combos |
| POST | `/api/ask` | `{question, context}` → `{summary, hikes, stays, combos, warnings}` |

All responses: `{results..., warnings: [string]}`.

### 3.3 Data flow (Near home / Trip plan)
dates → `trip_window` → bbox around home sized to the limit → Wikiloc search →
routing drops trailheads over the limit → (select hike | each top hike) → stay providers
in parallel around trailhead → `dog_rule` → (`ranking` for trip plan) → JSON → map.

## 4. Error handling
- Each source fails independently; failures become `warnings`, never 500s.
- Wikiloc: retry with backoff (4 attempts); then warning "Hikes temporarily unavailable".
- Stay provider blocked/changed: empty list + "<Provider> unavailable".
- Routing failure/no key: haversine estimate flagged "≈ estimated".
- Disk cache TTLs: hikes 7 d, drive times 7 d, geocoding 30 d, stays 1 h, Ask objects 1 d.
- Ask: missing CLI / not logged in / timeout / unparsable output → clear message, HTTP 200
  with warning.

## 5. Config (`.env`, gitignored; `.env.example` committed)
`ORS_API_KEY` (optional), `HOME_NAME=Athens`, `HOME_LAT=37.9838`, `HOME_LON=23.7275`,
drive-time bands, ranking weights, `STAY_RADIUS_KM=15`, `DEFAULT_GUESTS=2`,
`AIRBNB_DETAIL_TOP_N=10`.

## 6. Testing
- TDD per task.
- Sources: parsing tests against saved fixtures; no network in tests.
- Pure logic: trip_window, dog_rule (incl. ⚠️ garden-only case), ranking, routing fallback.
- API: FastAPI TestClient with fake providers.
- Ask: fake `claude` stub script on PATH — verifies flags, id resolution, timeout,
  not-installed case.
- Manual smoke test: real weekend search from Athens with 🐕 ON, verify map.

## 7. Running
```
cd hick-toors
uv sync
uv run uvicorn app.main:app --reload
# open http://localhost:8000
```

## 8. Build order (for subagent-driven development)
1. **Spike** — verify live access to Wikiloc search (fields incl. lat/lon), Wikiloc track
   geometry without login, Airbnb search/details, Booking search; save fixtures. Report
   which parts are feasible; adjust later tasks.
2. Project scaffold, config, models, trip_window, cache
3. Wikiloc source
4. Airbnb provider
5. Booking provider
6. Geocoding + routing
7. Dog rule + ranking + engine
8. FastAPI API
9. Map UI
10. MCP tools + Ask box
11. Smoke test + README

## 9. Known risks
- Wikiloc, Airbnb and Booking are scraped; they may block or change markup. Mitigated by
  soft failures, caching and fixture-based tests. Booking is the most likely to fail.
- Track geometry may require Wikiloc login → fall back to pin + link.
- Ask box latency (10–60 s) and plan usage limits.
