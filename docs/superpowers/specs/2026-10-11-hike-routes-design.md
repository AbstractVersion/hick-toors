# Hike route lines on the map — design

Date: 2026-10-11 · Status: requested in chat ("when we click on view on map, can we highlight the routes the same
way Wikiloc does?"); approach decided by the controller.

## Facts (probed 2026-10-11)

- Wikiloc search (`find.do`) returns only a start point per route — no geometry.
- Each trail page embeds `var mapData = {"mapData":[{..., "geom": "<base64>", ...}]}`; `geom` is **TWKB**
  (Tiny Well-Known Binary, an open spec) — Wikiloc's own map decodes it with `twkb.toGeoJSON(base64ToUint8Array(geom))`.
  Coordinates may carry extra dimensions (elevation, time): keep lon/lat only.
- The VM (Azure IP) gets 403 from Wikiloc pages; the user's Mac gets 200.

## Decisions

- **Model:** `Hike.route: str | None = None` — a Google-encoded polyline (precision 5) of the simplified track
  (Douglas–Peucker, tolerance ≈ 0.00005°, at most 400 points → typically ≤ 1–2 KB). Optional field: old trips
  load unchanged; the trip size cap still applies (a route that would push a trip over 256 KiB is dropped, the
  hike stays).
- **Source:** `app/sources/wikiloc_route.py`: `parse_route(html) -> list[(lat, lon)] | None` (extract `mapData`
  JSON, base64 → TWKB decode → coordinates of the first feature; LineString and MultiLineString), a minimal
  pure-Python TWKB decoder (varint/zigzag, precision from the header, optional size/bbox/idlist flags, Z/M
  dims skipped), `simplify`, `encode_polyline` / `decode_polyline`. Fetch with the existing Wikiloc HTTP
  settings (User-Agent; anonymous; never the cookie), retries like `make_fetch`; disk cache `route:v1:<id>` for
  30 days (failures not cached).
- **When fetched:** when a hike is added to a saved trip (`POST /api/saved-trips/{id}/items` and trip create /
  replace for hikes without a route) the server tries to fetch the route (deadline 15 s per hike, at most 4 at
  a time); failure → saved without a route (no error to the user). `GET /api/hikes/{wikiloc_id}/route` →
  `{route: "<polyline>" | null}` (cached) so the UI can also fill missing routes lazily.
- **Ops for the VM:** CLI `python -m app.routes fetch --from <trips API base URL or local DB path> --out
  routes.json` (run where Wikiloc is reachable: collects every hike id in the trips, fetches routes) and
  `python -m app.routes apply routes.json [--db PATH]` (run on the VM: merges `route` into matching hikes in
  every trip document under BEGIN IMMEDIATE, never touching other fields or tables; prints counts).
- **Map (UI):** `showTripOnMap` (used by My trips → Show on map and the guide's See on map) draws each hike's
  route: polyline weight 5 in a per-hike colour from a fixed palette of distinct colours, with a white casing
  line underneath (weight 8), start marker ▶ (small circle in the hike colour) and end 🏁 when start ≠ end
  (> 50 m apart); tapping/clicking the line opens the hike's popup; the map fits all routes + stays + sights.
  Hikes without a route keep today's pin. Routes live in the trip layer and are cleared with it. Hike list
  rows in the trip editor show a small colour dot matching their line.
- Phones: lines are tappable (Leaflet `interactive`, tolerance via the canvas renderer `tolerance: 10`).

## Testing

pytest: TWKB decoder on a real `geom` sample (fixture extracted from a saved trail page — store only the
geom string) and hand-built TWKB bytes (2D/3D, multi-line, negative coords); `parse_route` on minimal HTML;
simplify/encode/decode round trip; add-item stores a route (fake fetch), failure stores none, size cap drops
the route; route endpoint + cache; CLI fetch (fake fetch) and apply (merges into a copy of a trips DB without
touching users/rounds). UI check: a trip with a routed hike → Show on map draws a polyline with the casing and
end flag, clicking the line opens the popup, the map bounds contain the route; a hike without a route keeps
the pin; guide See on map shows the same lines; phone viewport.
