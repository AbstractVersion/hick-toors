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
- **⚙️ Filters:** *Hike filters* (activity, difficulty, distance, elevation gain, TrailRank, order) and *Stay filters* (price per night, rating, entire place, type, rooms, amenities, cancellation, …) open popups; **Apply** stores them in this browser, nothing searches until you press Search. `•N` shows how many differ from the defaults. Hike filters are applied after fetching from Wikiloc (more routes are fetched when filters are on). A stay source that can't apply a chosen filter is skipped with a notice (e.g. Booking.com can't filter by fireplace).
- **✏️ Draw area:** draw a rectangle (button) or a circle (map toolbar) to search hikes inside it (*Drawn area* mode). With *Trip plan* selected, a drawn shape replaces the drive radius.
- **Wikiloc login (optional):** put your logged-in Wikiloc browser cookie in `.env` as `WIKILOC_COOKIE=...` (never commit it). Wikiloc then filters hikes server-side and *Circular routes only* works; without it hikes are filtered locally and that option is disabled. If the login expires the app falls back to local filtering and says so.
- **Sources:** Wikiloc (hikes), Airbnb and Booking.com (stays; Booking is often blocked and then skipped), OpenRouteService (driving times; estimated without a key), OpenStreetMap Nominatim (place search). Results are cached in `.cache/`.
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
# headless Chrome check (needs Google Chrome; start the app on port 8765 first)
uv run uvicorn app.main:app --port 8765 &
uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765
```
