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
Run this from the `hick-toors/` folder (it uses `$(pwd)`):

```bash
claude mcp add hicktoors -- uv run --directory "$(pwd)" python -m app.mcp_tools
```

## Tests
```bash
uv run pytest
```
