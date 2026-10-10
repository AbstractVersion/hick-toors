import json
from datetime import date

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from app.models import HikeQuery, StayLookup, StaySearchQuery, TripQuery
from app.trip_markdown import trip_markdown

mcp = FastMCP("hicktoors")
_engine = None


def engine():
    global _engine
    if _engine is None:
        from app.wiring import build_engine

        _engine = build_engine()
    return _engine


_trip_store = None


def trip_store():
    global _trip_store
    if _trip_store is None:
        from app.config import Settings
        from app.trips_store import TripStore

        _trip_store = TripStore(Settings().trips_db)
    return _trip_store


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
                       radius_km: float | None = None, stay_filters: dict | None = None,
                       area: dict | None = None, providers: list[str] | None = None) -> str:
    """Find stays (Airbnb, Booking.com) near a hike (hike_id from search_hikes), a lat/lon, or inside a
    drawn area (used only when neither hike_id nor lat/lon is given): area is
    {"shape": "circle", "lat", "lon", "radius_m" (200-300000)} or
    {"shape": "rectangle", "south", "west", "north", "east"}; only stays inside the shape are returned.
    providers (optional): ["airbnb"], ["booking"] or both; default all sources.

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
                            guests=guests, radius_km=radius_km, area=area, providers=providers,
                            filters=stay_filters or {})
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_stays(q)).model_dump_json()


@mcp.tool()
async def plan_trip(start: str, end: str, dog: bool = True, guests: int = 2,
                    max_drive_minutes: int | None = None, unlimited: bool = False,
                    hike_filters: dict | None = None, stay_filters: dict | None = None,
                    place: str | None = None, area: dict | None = None) -> str:
    """Rank hike + stay combos (score /100 with breakdown). By default hikes come from around home
    (Athens); `place` (e.g. "Zagori") uses that area instead, `area` (same shape dict as search_stays:
    circle or rectangle) uses a drawn area; `area` wins when both are given.

    hike_filters / stay_filters take the same keys as in search_hikes / search_stays.
    Returns JSON {combos: [{hike, stay, score, breakdown}], warnings}.
    """
    try:
        q = TripQuery(start=start, end=end, dog=dog, guests=guests,
                      mode="drawn" if area else ("place" if place else "near_home"),
                      place=None if area else place, area=area,
                      drive_limit=_limit(max_drive_minutes, unlimited),
                      hike_filters=hike_filters or {}, stay_filters=stay_filters or {})
    except ValidationError as exc:
        return _error(exc)
    return (await engine().plan_trip(q)).model_dump_json()


@mcp.tool()
async def lookup_airbnb(url: str, start: str | None = None, end: str | None = None, guests: int = 2,
                        dog: bool = True) -> str:
    """Look up one known Airbnb listing from its room link (airbnb.<tld>/rooms/<id>, or just the id).
    Read-only. Dates/guests/dog in the link are ignored: pass start/end (YYYY-MM-DD) to get the total
    price for those dates; without both dates no price is looked up.

    Returns JSON {stay: {id, source, name, url, lat, lon, total_price, currency, rating, photo,
    pets_allowed, has_garden, badge}, warnings}, or {"error": "..."} for a bad link, a removed
    listing or when Airbnb doesn't answer.
    """
    from app.engine import StayLookupFailed, lookup_airbnb_link

    try:
        q = StayLookup(url=url, start=start, end=end, guests=guests, dog=dog)
    except ValidationError as exc:
        return _error(exc)
    try:
        return (await lookup_airbnb_link(engine(), q)).model_dump_json()
    except StayLookupFailed as exc:
        return json.dumps({"error": exc.message})


@mcp.tool()
async def nature_sights(lat: float, lon: float, radius_km: float = 10, groups: list[str] | None = None) -> str:
    """Natural sights from OpenStreetMap around a point (read-only): waterfalls, springs, hot springs,
    lakes, rivers, peaks, gorges, caves, arches, rocks, viewpoints, beaches, protected areas, forests.

    radius_km 1-30 (a square box around lat/lon). groups (optional, default all): any of "water"
    (waterfall, spring, hot_spring, lake, river), "landforms" (peak, gorge, cave, arch, rock),
    "views" (viewpoint, beach), "protected" (protected_area, forest).
    Returns JSON {sights: [{id, name, kind, lat, lon, elevation_m, url, wikipedia, lines}], warnings},
    or {"error": "..."}.
    """
    from math import isfinite

    from app.engine import ZOOM_IN_FOR_NATURE, nature_box, nature_groups
    from app.geo import bbox_around
    from app.models import LatLon

    if not (isfinite(radius_km) and 1 <= radius_km <= 30):
        return json.dumps({"error": "radius_km must be between 1 and 30"})
    if not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return json.dumps({"error": "lat must be within -90..90 and lon within -180..180"})
    try:
        chosen = nature_groups(groups)
        b = bbox_around(LatLon(lat=lat, lon=lon), radius_km)
        box = nature_box(b.south, b.west, b.north, b.east)
    except ValueError as exc:
        message = str(exc)
        if message == ZOOM_IN_FOR_NATURE:  # the box spans too many tiles (narrow longitudes up north)
            message = "radius_km too large for this latitude"
        return json.dumps({"error": message})
    return (await engine().nature(box, chosen)).model_dump_json()


@mcp.tool()
async def list_saved_trips() -> str:
    """List the user's saved trips (read-only), newest first.

    Returns JSON [{id, name, start, end, source ("manual" | "suggested"), hike_count, stay_count,
    updated_at}]. Use get_saved_trip(id) for the full trip.
    """
    return json.dumps([s.model_dump(mode="json") for s in trip_store().list_trips()])


@mcp.tool()
async def get_saved_trip(trip_id: str) -> str:
    """Return one saved trip (id from list_saved_trips) as Markdown: dates, guests, dog, hikes with
    Wikiloc links and stats, stays with links/prices/pet badges, and notes. Read-only.
    Unknown id -> JSON {"error": "Trip not found"}.
    """
    trip = trip_store().get(trip_id)
    if trip is None:
        return json.dumps({"error": "Trip not found"})
    return trip_markdown(trip, date.today())


if __name__ == "__main__":
    mcp.run(transport="stdio")
