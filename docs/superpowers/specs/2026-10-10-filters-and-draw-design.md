# hick-toors v2 — Search filters + draw-area search — Design Spec

Date: 2026-10-10
Status: Approved for autonomous execution (user: "go autonomous… pick the best choices")
Builds on: `2026-10-09-hick-toors-design.md` (v1, implemented on branch feat/hick-toors)

## 1. Purpose
1. Let the user configure **hike** and **stay** searches in detail before running them, using
   the filters each provider really supports, via two buttons that open popups (never automatic).
   Existing settings (dates, 🐕 dog, drive limit, guests) stay and appear pre-filled in the popups.
2. Let the user **draw a rectangle or circle** on the map and search hikes inside it.

Success: a user can open "⚙️ Stay filters", ask for "≥ 4.5★, ≤ €120/night, entire place, free
parking, free cancellation", run a search, and every stay shown honours those choices; and can draw
a circle around Mount Parnassos and get only hikes inside it.

### Out of scope
- Backend persistence of filters (filters persist in browser localStorage behind two functions so a
  later backend store can replace them).
- Freehand polygons; drawn area limiting stays (stays still searched around the chosen hike).
- Wikiloc server-side filtering (login-gated, see §3.1).

## 2. UX (agreed with user)
Header gains: `[⚙️ Hike filters •N] [⚙️ Stay filters •N] [✏️ Draw area]`, and a fourth mode
**Drawn area** next to Near home / Place / Trip plan. `•N` = number of filters differing from defaults.

Popups (modal dialogs):
- Built from `GET /api/filters` (catalogue), so labels/options are not duplicated in JS.
- Pre-filled with current values; the shared settings (dates, dog, drive limit, guests) appear in the
  relevant popup and stay in sync with the header controls.
- Buttons: **Apply** (stores, closes, does not search), **Reset to defaults**, **Cancel**.
- Each field shows a source tag when not universal: "Airbnb only", "Booking only",
  "Airbnb + Booking".
- Validation in the popup: min > max highlights the field and disables Apply.
- Persisted in localStorage (`loadFilters()/saveFilters()`); corrupt/missing storage → defaults.

Drawing (Leaflet.draw 1.0.4 from unpkg, rectangle + circle tools only):
- Finishing a shape switches mode to **Drawn area**; shape stays visible, editable, deletable; a new
  shape replaces the old.
- Drawn area + **Trip plan**: if a shape exists while Trip plan is selected, combos come from hikes
  inside the shape; otherwise from the drive-time radius (no fifth mode).
- Drawn area mode with no shape → notice "Draw a rectangle or circle first", no request.

## 3. Provider capabilities (verified by live probing 2026-10-10)

### 3.1 Wikiloc (hikes)
- Server-side filter params (`a`, `lfr/lto`, `ufr/uto`, `sfr/sto`, `t`) return **0 results for
  anonymous callers** (login-gated). Only `sort` works: `sort=trailrank` (top-rated set) vs default
  (popular). Therefore all hike filters except sort are applied **in the engine after fetching**.
- When any hike filter differs from defaults, fetch more pages (`hike_pages_filtered = 10`, i.e. up
  to 250 routes) so filtering still leaves useful results.
- Search payload gives: picto (activity id), distance (km, "11,81"), slope (elevation gain m),
  skill (1–5), trailrank, lat/lon. Loop/circular is not in the payload → no loop filter.

### 3.2 Airbnb (verified params; all AND together)
price_min/price_max (EUR per night), min_bedrooms, min_beds, min_bathrooms,
`room_types[]=Entire home/apt`, `l2_property_type_ids[]` (1 House, 2 Guesthouse, 3 Apartment,
4 Hotel), `guest_favorite=true`, `ib=true` (instant book, weak), `flexible_cancellation=true` (weak),
`kg_and_tags[]=Tag:789` (beachfront), `amenities[]=<id>`: 4 wifi, 8 kitchen, 9 free parking, 7 pool,
25 hot tub, 27 indoor fireplace, 33 washer, 5 AC, 30 heating, 47 workspace, 97 EV charger, 99 BBQ.
No garden amenity id exists (garden detection stays on listing details, as in v1).

### 3.3 Booking.com (verified `nflt` tokens; same field repeated = OR, different fields = AND)
`price=EUR-<min>-<max>-1` per night, where an open end is the literal word `min` / `max`
(e.g. `EUR-min-100-1`, `EUR-80-max-1`), `review_score=60|70|80|90`, `ht_id` (204 Hotels,
201 Apartments, 213 Villas, 220 Vacation Homes, 216 Guesthouses, 208 B&B, 223 Country Houses),
`privacy_type=3` (entire place), `class=1..5` (stars, OR), `fc=2` (free cancellation),
`mealplan=1` (breakfast), `hotelfacility=` 2 parking, 107 free wifi, 433 pool, 63 hot tub,
182 EV charging, 146 beachfront, 4 pets; `roomfacility=` 11 AC, 999 kitchen, 34 washer;
`entire_place_bedroom_count=1..5`, `min_bathrooms=1..8`.
Access: use `/searchresults.en-gb.html` with a **fresh curl_cffi chrome session per attempt** and
retry on 202 (≈50% success observed) — replaces v1's `/searchresults.html`, which is always blocked.

## 4. Filter model

### 4.1 HikeFilters (all optional; defaults reproduce v1 behaviour)
| field | type | default | applied |
|---|---|---|---|
| activities | list[int] | [1] (Hiking) | engine (picto ∈ list) — options: 1 Hiking, 43 Walking, 48 Trail running, 57 Nordic walking, 66 Canicross (with dog), 60 Via ferrata |
| difficulty_min / difficulty_max | int 1–5 | 1 / 5 | engine (skill) |
| distance_min_km / distance_max_km | float ≥ 0 | None | engine |
| elevation_min_m / elevation_max_m | int ≥ 0 | None | engine |
| min_trailrank | int 0–100 | None | engine |
| sort | "popular" \| "top_rated" | "popular" | Wikiloc (`sort=trailrank` for top_rated) |

Hikes with a missing value for a filtered field are **kept** only if that field is unfiltered
(e.g. distance None is dropped when a distance range is set — the user asked for a range).

### 4.2 StayFilters
| field | type | Airbnb | Booking | post-check |
|---|---|---|---|---|
| price_min / price_max | int EUR/night | price_min/max | price token | yes (total_price / nights) |
| min_rating | float 0–5 (options 3.5, 4, 4.5, 4.8) | — | review_score = 60/70/80/90 (rating×20 floored to step) | yes (rating) |
| entire_place | bool | room_types[] | privacy_type=3 | no |
| property_types | list[house, apartment, guesthouse, hotel, villa] | l2 ids (villa→house) | ht_id (house→220+223, guesthouse→216+208) | no |
| min_bedrooms | int | min_bedrooms | entire_place_bedroom_count (cap 5) | no |
| min_beds | int | min_beds | — | no |
| min_bathrooms | int | min_bathrooms | min_bathrooms | no |
| amenities | list[key] | ids above | facility tokens where mapped | no |
| free_cancellation | bool | flexible_cancellation | fc=2 | no |
| instant_book | bool | ib | — | no |
| guest_favorite | bool | guest_favorite | — | no |
| breakfast | bool | — | mealplan=1 | no |
| min_stars | int 1–5 | — | class=N..5 | no |
| beachfront | bool | kg_and_tags Tag:789 | hotelfacility=146 | no |

Amenity keys and support: wifi (A+B), kitchen (A+B), parking (A+B), pool (A+B), hot_tub (A+B),
ac (A+B), washer (A+B), ev_charger (A+B), fireplace (A), heating (A), workspace (A), bbq (A).

**Provider skipping rule:** when an active filter is not supported by a provider, that provider is
**skipped** for this search and a notice says why (e.g. "Booking.com skipped – it can't filter by
Fireplace"). Rationale: showing Booking stays for a "fireplace" search would silently break the
user's filter. Post-checks (price, rating) run for every provider and never drop a stay over
missing data.

Dog rule (v1) is unchanged and applies on top. Guests stay a top-level search setting
(shown in the Stay filters popup).

### 4.3 DrawnArea
`{shape: "rectangle", south, west, north, east}` or `{shape: "circle", lat, lon, radius_m}`.
Rectangle → bbox; circle → bbox around centre, then hikes farther than radius dropped.
Clamped to Greece; expanded to ≥ 5 km radius if tiny. Validation: south<north, west<east,
radius 200 m–300 km.

## 5. Architecture changes
- `app/filters.py` (new; the HikeFilters/StayFilters/DrawnArea models themselves live in
  `models.py`): holds the **catalogue** (fields, options, labels, provider support),
  `hike_filters_active()`, `apply_hike_filters(hikes, f)`, `stay_post_check(stays, f, nights)`,
  and `unsupported_filters(provider_name, f) -> list[label]`.
- `models.py`: add HikeFilters, StayFilters, DrawnArea; `HikeQuery.mode` gains "drawn" +
  `area: DrawnArea | None`; `HikeQuery.filters`, `StaySearchQuery.filters`, `TripQuery.filters`
  (hike + stay) and `TripQuery.area`; `StayQuery.filters`.
- Wikiloc: `search(bbox, query="", sort="popular", pages=None)`; activity filter moves out of
  `parse_search` (it keeps all activities, still Greece-only) into the engine.
- Airbnb / Booking: translate `StayQuery.filters` to params; Booking endpoint/session change.
- Engine: filter pass-through, page count, post-checks, provider skipping, drawn-area bbox +
  circle trim, cache keys include filters/sort/area.
- API: `GET /api/filters`; existing endpoints accept the new fields; 422 on invalid ranges.
- MCP tools: optional `hike_filters` / `stay_filters` dict args (validated into the models).
- UI: buttons, two modal popups generated from the catalogue, localStorage, Leaflet.draw,
  Drawn area mode, filter badges.

## 6. Error handling
- Invalid ranges / area → 422 with readable message; popup prevents most of them.
- Provider skipped → notice per skipped provider; all providers skipped → "No source supports
  this combination of filters".
- Filters leave nothing → "No stays match your filters" / "No hikes match your filters" notice.
- Wikiloc / Booking / Airbnb failures unchanged (soft warnings).

## 7. Testing
- Provider unit tests: filter → params mapping (Airbnb params, Booking nflt string and endpoint),
  Wikiloc sort param + activity no longer filtered in parse.
- filters.py unit tests: catalogue shape, apply_hike_filters each field + missing values,
  stay_post_check price per night + rating, unsupported_filters.
- Engine tests: filtered page count, provider skipping + notice, drawn rectangle/circle, Trip plan
  with area, cache key separation.
- API tests: /api/filters, invalid range 422, filters reach engine.
- MCP tool tests: filters dict passed through, invalid → error JSON.
- UI: `node --check`; headless Chrome (installed Chrome via Playwright `channel="chrome"`) script:
  open both popups, change a filter, apply, see badge; draw rectangle via Leaflet.draw API; search.
