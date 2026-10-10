# Nature sights — design

Date: 2026-10-10 · Status: approved in chat ("yes go on")

## Goal

Show natural sights (waterfalls, springs, lakes, rivers, peaks, gorges, caves, viewpoints, beaches,
protected areas…) on the map for whatever area the user is looking at, and let them add sights to saved
trips like hikes and stays.

## Decisions (from the brainstorm)

- **Source:** OpenStreetMap via the Overpass API (open data, works from cloud IPs). Endpoints in order:
  `https://overpass-api.de/api/interpreter`, then `https://maps.mail.ru/osm/tools/overpass/api/interpreter`.
  Probe (2026-10-10, box 37.55,22.10–37.75,22.35): main server 200 in 1.2 s → 7 springs, 6 peaks, 2 caves,
  1 viewpoint, 1 lake, 1 river. The main server sometimes answers 504 when busy; kumi/private.coffee mirrors
  answered 500 — not used.
- **Trigger:** a **🌿 Nature** header toggle; when on, sights load for the visible map (zoom ≥ 10) and refresh
  on pan/zoom. Own map layer, never cleared by Search hikes / Search all / Suggest trips / Show on map.
- **Categories (all four groups, each a chip):**

  | group key | kinds (emoji) | OSM tags |
  |---|---|---|
  | `water` | waterfall 💧, spring 🚰, hot_spring ♨️, lake 🏞️, river 🌊 | `waterway=waterfall`; `natural=spring`; `natural=hot_spring` or `amenity=public_bath`+`bath:type=hot_spring`; `natural=water`+`water=lake`/`reservoir`+`name`; `waterway=river`+`name` |
  | `landforms` | peak ⛰️, gorge 🏔️, cave 🕳️, arch 🪨, rock 🪨 | `natural=peak`; `natural=gorge` (also `natural=valley`+`valley=gorge`/`canyon`); `natural=cave_entrance`; `natural=arch`; `natural=rock`+`name` |
  | `views` | viewpoint 👁️, beach 🏖️ | `tourism=viewpoint`; `natural=beach` |
  | `protected` | protected_area 🌲, forest 🌳 | `boundary=protected_area` or `boundary=national_park` or `leisure=nature_reserve` (+`name`); `landuse=forest` or `natural=wood` with `name` |

- **Trips:** a trip gains `sights` (≤ 100); old trips load unchanged. Markdown gets a 🌿 Nature section.
- **Ask Claude / MCP:** read-only `nature_sights(lat, lon, radius_km, kinds)`.

## Model (`app/models.py`)

```
SightKind = Literal["waterfall","spring","hot_spring","lake","river","peak","gorge","cave","arch","rock",
                    "viewpoint","beach","protected_area","forest"]
SightGroup = Literal["water","landforms","views","protected"]
Sight
  id: str            # "osm:node/123", "osm:way/45", "osm:relation/6"
  name: str          # OSM name:en > name > kind label (e.g. "Spring") when unnamed
  kind: SightKind
  lat: float; lon: float           # node position, or centre of the element's bounds
  elevation_m: int | None = None   # peaks/springs/waterfalls with ele=*
  url: str           # https://www.openstreetmap.org/<type>/<id>
  wikipedia: str | None = None     # https://<lang>.wikipedia.org/wiki/<title> from wikipedia=lang:Title
  lines: list[list[list[float]]] = []   # rivers / area outlines: [[ [lat,lon], … ], …], clipped + simplified
NatureSearch { sights: list[Sight], warnings: list[str] }
```

`SIGHT_GROUPS: dict[SightGroup, list[SightKind]]` maps groups to kinds (table above).

## Source (`app/sources/nature.py`)

- **Tiles:** the requested bbox is snapped to a 0.25° grid; each tile (south-west corner key, e.g.
  `37.50_22.00`) is one Overpass query for **all** kinds; results are filtered by the requested groups
  afterwards. Max 16 tiles per request, else 422 `"Zoom in to see nature sights"`.
- **Query per tile:** nodes for point kinds; `way`/`relation` for lakes, rivers, gorges, beaches, protected
  areas and forests with `out tags center geom(<tile bbox>)` (geometry clipped to the tile); `[timeout:25]`;
  at most 600 elements per tile (`out … 600`).
- **Parsing:** element → `Sight` (kind decided by the tag table, first match wins in table order; unknown →
  skipped). Lines: ways give one line; relations give one line per member way with geometry. Each line is
  simplified (Douglas–Peucker, tolerance 0.0005°) and dropped if < 2 points; a sight keeps at most 40 lines
  and 2,000 points total. Names: `name:en` > `name` > kind label; names cut to 120 chars. `ele` parsed as int
  metres when numeric. Wikipedia only for `lang:Title` values (lang = 2–3 lowercase letters).
- **Fetch:** POST form `data=<query>` with a `User-Agent: hick-toors/1.0 (personal hiking planner)`, timeout
  40 s; try endpoint 1, on HTTP error/timeout/invalid JSON try endpoint 2; both fail → `NatureUnavailable`.
  Tiles are fetched with concurrency 2.
- **Cache:** per tile `nature:v1:<tile>` → list of Sight dicts, TTL 7 days (failures not cached).
- **Merge:** sights from several tiles are merged by id: lines concatenated (cap applies), first position kept.

## Engine + API

- `Engine.nature(bbox, groups) -> NatureSearch`: tiles in parallel; a failed tile adds the warning
  `"Nature sights unavailable for part of the map – try again in a minute"` (once) and the other tiles still
  return; all tiles failed → same warning, empty list.
- `GET /api/nature?south=&west=&north=&east=&groups=water,landforms` (groups default: all four; unknown group
  → 422; south<north, west<east, within lat −90..90 / lon −180..180 else 422) → `NatureSearch`.
  Too many tiles → 422 `"Zoom in to see nature sights"`.
- MCP `nature_sights(lat, lon, radius_km=10, groups=None)` → JSON `NatureSearch` (radius 1..30 km; bbox
  around the point) or `{"error": …}`.

## Trips

- `SavedTripIn.sights: list[Sight] = []` (≤ 100, deduped by id, numbers finite like hikes/stays; `lines` are
  **not** stored in trips: dropped on save to keep documents small).
- `TripItemAdd` accepts exactly one of `hike` / `stay` / `sight` (message `"Give exactly one of hike, stay or
  sight"`). Store `add_item` limit for sights 100 → 409 `"A trip holds at most 100 nature sights"`.
- Markdown: after Stays, `## 🌿 Nature (N)` with `- <emoji> **[<name>](<osm url>)** – <Kind label>[ · <ele> m][
  · [Wikipedia](…)] · [📍 Map](google maps)`; `_None yet._` is **not** printed for an empty Nature section
  (section omitted when no sights, so old exports are unchanged).

## UI

- Header button `🌿 Nature` (`#nature-btn`, toggles `.active`, persisted in localStorage `hick-toors:nature`).
- On the map (top-right, under the layer control) a chip bar `#nature-chips` with the four groups (💧 Water,
  ⛰️ Landforms, 👁️ Views & beaches, 🌲 Protected), all on by default, persisted; visible only when the toggle
  is on. A hint `Zoom in to see nature sights` (reuse the `#draw-hint` style but separate element
  `#nature-hint`) when zoom < 10.
- On `moveend`/`zoomend` (debounced 600 ms) and on toggle/chip change: GET `/api/nature` for the map bounds
  (padded 10 %); responses older than the latest request are ignored; spinner chip while loading.
- Layer: own `L.layerGroup` (not clustered): point sights as small emoji pins (`pin()` style, class
  `sight`); `lines` drawn as polylines (rivers blue `#3b7dd8` weight 2; protected/forest dashed green
  `#2f6f4e` weight 1.5, no fill). Popup: name, kind label (+ elevation), links `↗ OpenStreetMap`,
  `↗ Wikipedia` (only http(s)), and `＋ Trip` (`tripButtonHtml("sight", id)`); sights stored in
  `state.sights` (Map) for the ＋ Trip lookup.
- Trip editor: `🌿 Nature` list with ↑ ↓ ✕ (same as hikes); **Show on map** also pins the trip's sights (into
  the nature layer's separate "trip sights" group, cleared by the next Show on map / clearAll).
- All text via textContent/esc(); links only for http(s).

## Limits (added after the final review — public VM, Overpass fair use)

- Greece only: boxes that don't touch the Greece bbox padded by 0.5° → 422 "Nature sights are available for
  Greece only" (API and MCP).
- At most 32 tile fetches in flight; further uncached tiles return the "unavailable for part of the map" warning.
- Both endpoints failing → no fetching for 60 s, doubling per consecutive failure up to 10 min; the endpoint
  that last answered is tried first.
- At most 5,000 Overpass HTTP calls per UTC day (persisted in the cache); then cache only + "Nature sights limit
  reached for today – showing cached areas only".
- Truncated tiles: warning "Some nature sights were left out here (too many in this area)", cached 7 days.
- Disk cache sweep at startup and daily: expired entries, files older than 31 days, `.tmp` files older than 1 h.

## PDF export (added mid-run by the user)

`GET /api/saved-trips/{id}/print` → printable HTML (same facts as the Markdown export, hikes numbered), strict
CSP with a sha256-hashed inline print script; the 🖨️ PDF button saves first, opens the tab inside the click
(Safari), then navigates it; the user picks "Save as PDF" in the print dialog.

## Out of scope

Full-country overview at low zoom, routing to sights, photos, editing OSM.

## Testing

Parser on canned Overpass JSON (every kind, unnamed fallback, ele, wikipedia, relation lines, clipping caps,
Douglas–Peucker), tiling math, cache hit, endpoint fallback, partial-failure warning, API validation, MCP,
trip sights (model, store, add_item 100 limit, lines dropped, old docs load), Markdown section, Playwright
check with `/api/nature` canned (toggle, chips, hint at low zoom, ＋ Trip on a sight, editor list, export).
