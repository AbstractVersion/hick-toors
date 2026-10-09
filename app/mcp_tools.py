import json

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from app.models import HikeQuery, StaySearchQuery, TripQuery

mcp = FastMCP("hicktoors")
_engine = None


def engine():
    global _engine
    if _engine is None:
        from app.wiring import build_engine

        _engine = build_engine()
    return _engine


def _limit(max_drive_minutes: int | None, unlimited: bool):
    if unlimited:
        return "unlimited"
    return max_drive_minutes if max_drive_minutes else "auto"


def _error(exc: ValidationError) -> str:
    return json.dumps({"error": "; ".join(e["msg"] for e in exc.errors())})


@mcp.tool()
async def search_hikes(start: str, end: str, place: str | None = None,
                       max_drive_minutes: int | None = None, unlimited: bool = False) -> str:
    """Find hikes in Greece (Wikiloc) for a trip from start to end (YYYY-MM-DD).

    Without `place`, searches around home (Athens) within a drive limit that defaults from the
    number of days (1 day: 90 min, 2-3 days: 4 h, 4+ days: unlimited). Set max_drive_minutes to
    override, or unlimited=True to include islands (hikes flagged ferry_needed).
    With `place` (e.g. "Pelion", "Zagori"), searches that area instead.
    Returns JSON {hikes: [{id, name, url, lat, lon, distance_km, elevation_gain_m, difficulty,
    trailrank, near, drive_minutes, ferry_needed}], warnings}.
    """
    try:
        q = HikeQuery(start=start, end=end, mode="place" if place else "near_home", place=place,
                      drive_limit=_limit(max_drive_minutes, unlimited))
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_hikes(q)).model_dump_json()


@mcp.tool()
async def search_stays(start: str, end: str, hike_id: str | None = None, lat: float | None = None,
                       lon: float | None = None, dog: bool = True, guests: int = 2,
                       radius_km: float | None = None) -> str:
    """Find stays (Airbnb, Booking.com) near a hike (hike_id from search_hikes) or a lat/lon.

    With dog=True only stays that allow pets, or have a garden (badge garden_unconfirmed =
    pets not confirmed), are returned. Returns JSON {stays: [{id, source, name, url, lat, lon,
    total_price, currency, rating, badge, distance_km}], warnings}.
    """
    try:
        q = StaySearchQuery(start=start, end=end, hike_id=hike_id, lat=lat, lon=lon, dog=dog,
                            guests=guests, radius_km=radius_km)
    except ValidationError as exc:
        return _error(exc)
    return (await engine().search_stays(q)).model_dump_json()


@mcp.tool()
async def plan_trip(start: str, end: str, dog: bool = True, guests: int = 2,
                    max_drive_minutes: int | None = None, unlimited: bool = False) -> str:
    """Rank hike + stay combos for a trip from home (score /100 with breakdown).

    Returns JSON {combos: [{hike, stay, score, breakdown}], warnings}.
    """
    try:
        q = TripQuery(start=start, end=end, dog=dog, guests=guests,
                      drive_limit=_limit(max_drive_minutes, unlimited))
    except ValidationError as exc:
        return _error(exc)
    return (await engine().plan_trip(q)).model_dump_json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
