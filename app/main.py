import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from datetime import date
from math import isfinite

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.ask import AskRequest, AskResult, AskRunner
from app.cache import DAY
from app.config import BASE_DIR, Settings
from app.engine import StayLookupFailed, lookup_stay_link, nature_box, nature_groups
from app.filters import catalogue
from app.models import (MAX_TRIP_SIGHTS, TRIP_TOO_LARGE, HikeQuery, HikeSearch, LatLon, NatureSearch, NearbySightsIn,
                        NearbySightsResult, SavedTrip, SavedTripIn,
                        SavedTripSummary, SavedTripUpdate, StayLookup, StayLookupResult, StaySearch, StaySearchQuery,
                        TripItemAdd, TripQuery, TripSearch)
from app.nearby_limits import NearbyBusy, NearbyLimits
from app.trip_markdown import content_disposition, trip_markdown
from app.trip_print import CSP, trip_print_html
from app.trips_store import TooManyTrips, TripConflict, TripFull, TripStore, TripTooLarge
from app.wiring import build_engine

STATIC_DIR = BASE_DIR / "static"
# The page and its scripts must update together: browsers revalidate them (ETag) on every load.
REVALIDATE = {"Cache-Control": "no-cache"}
TRIP_SIGHTS_FULL = "Trip already holds 100 nature sights"


class RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(REVALIDATE)
        return response


def _json_safe(value):
    """Replace NaN/Infinity (not valid JSON) with None, recursively."""
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


async def sweep_cache_daily(cache, interval: float = DAY) -> None:
    """Sweep the disk cache now and then every `interval` seconds, in a thread; errors are only logged."""
    while True:
        try:
            removed = await asyncio.to_thread(cache.sweep)
            print(f"cache sweep removed {removed} files", file=sys.stderr)
        except Exception as exc:
            print(f"cache sweep failed: {exc!r}", file=sys.stderr)
        await asyncio.sleep(interval)


def create_app(engine=None, ask_runner=None, settings: Settings | None = None,
               trip_store: TripStore | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    cache = getattr(engine, "cache", None)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(sweep_cache_daily(cache)) if hasattr(cache, "sweep") else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="hick-toors", lifespan=lifespan)
    app.state.engine = engine
    ask_runner = ask_runner or AskRunner(settings, engine.lookup)
    app.state.ask = ask_runner
    trip_store = trip_store or TripStore(settings.trips_db)
    app.state.trips = trip_store
    nearby_limits = NearbyLimits()
    app.state.nearby = nearby_limits

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError):
        # Like FastAPI's default, but a NaN/Infinity echoed back in "input" must not turn the 422 into a 500.
        return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})

    @app.get("/api/config")
    async def config():
        return {
            "home": {"name": settings.home_name, "lat": settings.home_lat, "lon": settings.home_lon},
            "drive_bands": {"day_trip": settings.day_trip_max_minutes, "weekend": settings.weekend_max_minutes},
            "stay_radius_km": settings.stay_radius_km,
            "default_guests": settings.default_guests,
            "ask_available": bool(ask_runner and ask_runner.available()),
        }

    @app.get("/api/filters")
    async def filters():
        return catalogue(wikiloc_login=bool(settings.wikiloc_cookie))

    @app.get("/api/locate")
    async def locate(q: str = Query(min_length=1, max_length=200)):
        q = q.strip()
        if not q:
            raise HTTPException(status_code=422, detail="q must not be blank")
        try:
            place = await engine.geocoder.locate(q)
        except Exception as exc:
            print(f"locate failed: {exc}", file=sys.stderr)
            return {"place": None, "warnings": ["Place search unavailable – try again later"]}
        if place is None:
            return {"place": None, "warnings": [f"Couldn't find '{q}' in Greece"]}
        return {"place": place.model_dump(), "warnings": []}

    @app.post("/api/hikes", response_model=HikeSearch)
    async def hikes(q: HikeQuery):
        return await engine.search_hikes(q)

    @app.post("/api/stays", response_model=StaySearch)
    async def stays(q: StaySearchQuery):
        return await engine.search_stays(q)

    @app.post("/api/stays/lookup", response_model=StayLookupResult)
    async def stay_lookup(q: StayLookup):
        try:
            return await lookup_stay_link(engine, q)
        except StayLookupFailed as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message)

    @app.post("/api/trips", response_model=TripSearch)
    async def trips(q: TripQuery):
        return await engine.plan_trip(q)

    @app.get("/api/nature", response_model=NatureSearch)
    async def nature(south: float, west: float, north: float, east: float, groups: str | None = None):
        try:
            box = nature_box(south, west, north, east)
            chosen = nature_groups(groups.split(",") if groups else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return await engine.nature(box, chosen)

    @app.post("/api/ask", response_model=AskResult)
    async def ask(req: AskRequest):
        return await ask_runner.ask(req)

    def found(trip):
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        return trip

    # Saved trips: sync handlers, so sqlite runs in FastAPI's threadpool.
    @app.get("/api/saved-trips", response_model=list[SavedTripSummary])
    def list_saved_trips():
        return trip_store.list_trips()

    @app.post("/api/saved-trips", response_model=SavedTrip, status_code=201)
    def create_saved_trip(body: SavedTripIn):
        try:
            return trip_store.create(body)
        except TooManyTrips:
            raise HTTPException(status_code=409, detail="At most 500 saved trips – delete some first")

    @app.get("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def get_saved_trip(trip_id: str):
        return found(trip_store.get(trip_id))

    @app.put("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def replace_saved_trip(trip_id: str, body: SavedTripUpdate):
        try:
            return found(trip_store.replace(trip_id, body, expected_updated_at=body.expected_updated_at))
        except TripConflict:
            raise HTTPException(status_code=409,
                                detail="Trip changed elsewhere – reopen it to see the latest version")

    @app.delete("/api/saved-trips/{trip_id}", status_code=204)
    def delete_saved_trip(trip_id: str):
        if not trip_store.delete(trip_id):
            raise HTTPException(status_code=404, detail="Trip not found")
        return Response(status_code=204)

    @app.post("/api/saved-trips/{trip_id}/items", response_model=SavedTrip)
    def add_saved_trip_item(trip_id: str, body: TripItemAdd):
        try:
            return found(trip_store.add_item(trip_id, body))
        except TripFull as full:
            raise HTTPException(status_code=409, detail="A trip holds at most 100 nature sights"
                                if full.kind == "sights" else "A trip holds at most 50 hikes and 50 stays")
        except TripTooLarge:
            raise HTTPException(status_code=413, detail=TRIP_TOO_LARGE)

    @app.post("/api/saved-trips/{trip_id}/nearby-sights", response_model=NearbySightsResult)
    async def add_nearby_sights(trip_id: str, body: NearbySightsIn | None = None):
        radius = (body or NearbySightsIn()).radius_km
        trip = found(await asyncio.to_thread(trip_store.get, trip_id))
        points = [LatLon(lat=item.lat, lon=item.lon) for item in [*trip.hikes, *trip.stays]]
        if not points:
            raise HTTPException(status_code=422, detail="Add a hike or stay first")
        if len(trip.sights) >= MAX_TRIP_SIGHTS:  # nothing could be added: don't spend Overpass calls
            return NearbySightsResult(trip=trip, added=0, found=0, warnings=[TRIP_SIGHTS_FULL])
        try:
            with nearby_limits.slot(trip_id):
                nearby = await engine.nearby_sights(points, radius)
        except NearbyBusy as busy:
            raise HTTPException(status_code=429, detail=str(busy))
        result = found(await asyncio.to_thread(trip_store.add_sights, trip_id, nearby.sights))
        saved, added, stopped = result
        warnings = list(nearby.warnings)
        if stopped == "sights":
            warnings.append(TRIP_SIGHTS_FULL)
        elif stopped == "size":
            warnings.append(TRIP_TOO_LARGE)
        return NearbySightsResult(trip=saved, added=added, found=len(nearby.sights), warnings=warnings)

    @app.get("/api/saved-trips/{trip_id}/export.md")
    def export_saved_trip(trip_id: str):
        trip = found(trip_store.get(trip_id))
        return Response(trip_markdown(trip, date.today()), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": content_disposition(trip.name)})

    @app.get("/api/saved-trips/{trip_id}/print", response_class=HTMLResponse)
    def print_saved_trip(trip_id: str):
        trip = found(trip_store.get(trip_id))
        return HTMLResponse(trip_print_html(trip, date.today()),
                            headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers=REVALIDATE)

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
