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

### Run with Docker Compose

```bash
cd hick-toors
cp .env.example .env        # optional, read at runtime via env_file (never baked into the image)
docker compose up --build -d
# open http://localhost        (port 80; change with HICK_TOORS_PORT=8080 docker compose up -d)
docker compose logs -f      # follow logs
docker compose down         # stop (keeps both volumes: search cache `hick-cache` and saved trips `hick-data`)
```

The **Ask Claude** box is unavailable inside the container (it needs your local, logged-in
`claude` CLI) — use the non-Docker run above for that. Everything else works the same.

## How it works
- **Dates → reach:** 1 day ≈ 1.5 h drive, 2–3 days ≈ 4 h, 4+ days anywhere (islands marked ⛴️). The 🚗 slider overrides it.
- **Where vs what:** the mode radios say *where* – *Near home* (around Athens), *Place* (e.g. "Zagori") or *Drawn area*. Three buttons say *what*: **🔍 Search hikes** lists hikes there and leaves the stays already on the map (e.g. a trip you showed) where they are; **🔍 Search all** clears the map and lists hikes, and in a drawn area also loads the stays inside it; **⭐ Suggest trips** ranks hike + stay combos (score /100) for the same *where* (a drawn shape, the place text, or the drive limit) and shows them in the **Trip picks** section, which stays hidden until there is something to show. Search hikes / Search all hide it again. The MCP `plan_trip` tool takes optional `place` / `area` too.
- **📍 Go to place:** type a place in Greek or English in the header box and press Enter (or Go): the map flies there and drops a 📍 pin. It is navigation only – no hike or stay search runs and your results stay. The pin survives new searches (`GET /api/locate?q=`, Nominatim, cached 30 days).
- **🌿 Nature:** the header toggle shows natural sights on the map – waterfalls, springs, hot springs, lakes, rivers, peaks, gorges, caves, arches, rocks, viewpoints, beaches, protected areas and forests – for whatever area you are looking at, refreshed as you pan or zoom (after a short pause). Four chips on the map (💧 Water, ⛰️ Landforms, 👁️ Views & beaches, 🌲 Protected) pick the categories; the toggle and chips are remembered in this browser. Sights load once the view is about 1°×1° or smaller (at most 16 tiles of 0.25°; until then the map says *Zoom in to see nature sights*); sights already on the map stay put while you pan, so an open popup stays open. Rivers are drawn as blue lines, protected areas and forests as dashed green outlines. Each popup links to OpenStreetMap (and Wikipedia when tagged) and has **＋ Trip**; saved trips get a 🌿 Nature list (up to 100 sights, reorder/remove, included in the Markdown export, pinned by **Show on map**). Search hikes / Search all / Suggest trips never clear the nature layer. Data: © OpenStreetMap contributors (ODbL), through the Overpass API, cached 7 days per 0.25° tile (`GET /api/nature?south=&west=&north=&east=&groups=`, MCP `nature_sights`).
- **Loading spinners:** hikes, stays (Airbnb and Booking.com separately), Trip picks, Ask Claude and Go each show a small spinner while their request runs (a static "…" if your system prefers reduced motion).
- **🧳 Saved trips:** **＋ Trip** on any hike, stay or nature sight (list or map popup) adds it to a new or existing trip; **💾 Save** on a Trip pick saves that hike + stay as a trip. **🧳 My trips** lists them: rename, set dates/guests/dog, write notes, reorder or remove items, **Show on map**, **⬇ Export .md** (saves first, then downloads a Markdown file), **🖨️ PDF** (saves first, then opens a printable page in a new tab – pick “Save as PDF” in the print dialog) and **Delete** (click twice). Under 🏡 Stays, paste an Airbnb room link (or just its id) and **＋ Add** to add that listing – priced for the trip's dates/guests/dog (header dates if the trip has none; whatever the link says is ignored) and saved at once; if Airbnb shows no price for those dates, type the total in € next to the stay. Trips live in a SQLite file (`data/trips.db`, change with `TRIPS_DB`; in Docker the `hick-data` volume) and keep a copy of each hike/stay as seen when added. There is one shared list and no login – anyone who can open the site can change it. Limits: 500 trips, 50 hikes + 50 stays + 100 nature sights and 256 KB per trip; saving an edit that another tab or visitor overwrote in the meantime is refused (reopen the trip). Ask Claude can read them (`list_saved_trips`, `get_saved_trip`).
- **🐕 Dog:** stays must allow pets (🐕) or have a garden (⚠️ pets not confirmed, shown last).
- **⚙️ Filters:** *Hike filters* (activity, difficulty, distance, elevation gain, TrailRank, order) and *Stay filters* (price per night, rating, entire place, type, rooms, amenities, cancellation, …) open popups; **Apply** stores them in this browser, nothing searches until you press Search. `•N` shows how many differ from the defaults. Hike filters are applied after fetching from Wikiloc (more routes are fetched when filters are on). A stay source that can't apply a chosen filter is skipped with a notice (e.g. Booking.com can't filter by fireplace).
- **✏️ Draw area:** draw a rectangle (button) or a circle (map toolbar) to search hikes inside it (*Drawn area* mode). *Suggest trips* with *Drawn area* selected plans trips inside the shape. A *Drawn area* search also loads the stays inside the shape automatically (clicking a hike afterwards shows that hike's stays instead). The MCP `search_stays` tool takes the same shape as `area`.
- **Depth + Load more:** Wikiloc is read up to 20 pages (500 routes) per search, fetched 4 pages at a time (`HIKE_PAGES`, `HIKE_PAGES_FILTERED`, `MAX_HIKES`, `WIKILOC_CONCURRENCY`). The list header shows "Showing N of TOTAL routes in this area" (Wikiloc's count for the whole area; anonymously it includes every activity), and **Load more hikes** appends the next pages.
- **Wikiloc login (optional):** put your logged-in Wikiloc browser cookie in `.env` as `WIKILOC_COOKIE=...` (never commit it). Wikiloc then filters hikes server-side and *Circular routes only* works; without it hikes are filtered locally and that option is disabled. If the login expires the app falls back to local filtering and says so.
- **Booking.com reliability:** Booking sits behind AWS WAF's JavaScript challenge, which answers roughly 70% of our requests with a challenge page at random (the app does not try to solve or bypass it). So each page is retried up to 8 times in a fresh session with jittered exponential backoff (1, 2, 4, 8, 8… s), and in drawn-area searches page 2 is only fetched if page 1 already had a stay inside the shape. Stays now load as two parallel requests (`providers: ["airbnb"]` and `["booking"]`; also an optional `providers` argument for the API and the MCP `search_stays`): Airbnb results appear as soon as they arrive, a "Booking.com…" chip shows while Booking is still trying, and if it gives up you get a notice plus a **Retry Booking** link that re-requests just Booking.com and merges the results.
- **Sources:** Wikiloc (hikes), Airbnb and Booking.com (stays; Booking is often blocked and then skipped), OpenRouteService (driving times; estimated without a key), OpenStreetMap Nominatim (place search), OpenStreetMap Overpass (🌿 nature sights). Results are cached in `.cache/`.
- **Ask Claude:** uses your local Claude Code login (`claude -p`) with this app's search tools only. It counts against your Claude Code plan's usage; no API key needed. Run `claude` once to log in if the box says unavailable.

## Use the tools from Claude Code
Run this from the `hick-toors/` folder (it uses `$(pwd)`):

```bash
claude mcp add hicktoors -- uv run --directory "$(pwd)" python -m app.mcp_tools
```

## Tests
```bash
uv run pytest
node tests/ui/filters_logic.test.js
# headless Chrome check (needs Google Chrome; start the app on port 8765 with a throwaway trips DB first)
TRIPS_DB=$(mktemp -d)/trips.db uv run uvicorn app.main:app --port 8765 &
uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765
```
