import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from datetime import date
from math import isfinite

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.accounts import AccountStore, User
from app.ask import AskRequest, AskResult, AskRunner
from app.auth_routes import auth_router, require_user
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
from app.rounds_routes import rounds_router
from app.routes import fill_routes, missing_route_ids, route_router
from app.sources.wikiloc_route import RouteFetcher
from app.trips_store import TooManyTrips, TripConflict, TripFull, TripStore, TripTooLarge
from app.votes_store import RoundStore
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


async def sweep_cache_daily(cache, interval: float = DAY, accounts=None) -> None:
    """Sweep the disk cache (if any) and expired sessions now and then every `interval` seconds, in a thread;
    errors are only logged."""
    while True:
        if cache is not None:
            try:
                removed = await asyncio.to_thread(cache.sweep)
                print(f"cache sweep removed {removed} files", file=sys.stderr)
            except Exception as exc:
                print(f"cache sweep failed: {exc!r}", file=sys.stderr)
        if accounts is not None:
            try:
                removed = await asyncio.to_thread(accounts.sweep_sessions)
                print(f"session sweep removed {removed} sessions", file=sys.stderr)
            except Exception as exc:
                print(f"session sweep failed: {exc!r}", file=sys.stderr)
        await asyncio.sleep(interval)


def create_app(engine=None, ask_runner=None, settings: Settings | None = None,
               trip_store: TripStore | None = None, accounts: AccountStore | None = None,
               rounds: RoundStore | None = None, route_fetcher: RouteFetcher | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    cache = getattr(engine, "cache", None)
    cache = cache if hasattr(cache, "sweep") else None
    accounts = accounts or AccountStore(settings.trips_db, signups_per_ip_hour=settings.signup_per_ip_hour)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(sweep_cache_daily(cache, accounts=accounts))
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
    app.state.accounts = accounts
    rounds = rounds or RoundStore(settings.trips_db, trip_store, accounts)
    app.state.rounds = rounds
    app.include_router(auth_router(accounts))
    app.include_router(rounds_router(rounds))
    # Hike tracks: build_engine's fetcher; an injected engine without one (tests) gets an offline, cache-less one.
    routes = route_fetcher or getattr(engine, "routes", None) or RouteFetcher(online=False)
    app.state.routes = routes
    app.include_router(route_router(routes))
    nearby_limits = NearbyLimits()
    app.state.nearby = nearby_limits

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError):
        # Like FastAPI's default, but a NaN/Infinity echoed back in "input" must not turn the 422 into a 500.
        errors = exc.errors()
        if request.url.path.startswith("/api/auth/"):  # `input` / `ctx` could echo the password
            errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in errors]
        return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(errors))})

    @app.get("/api/users")
    def users():
        return [{"username": name, "emoji": emoji} for name, emoji in accounts.users()]

    @app.get("/api/config")
    async def config():
        return {
            "home": {"name": settings.home_name, "lat": settings.home_lat, "lon": settings.home_lon},
            "drive_bands": {"day_trip": settings.day_trip_max_minutes, "weekend": settings.weekend_max_minutes},
            "stay_radius_km": settings.stay_radius_km,
            "default_guests": settings.default_guests,
            "ask_available": bool(ask_runner and ask_runner.available()),
            "route_fetch": routes.online,
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

    # Create / replace / add item are async: hikes without a route get one fetched (app/routes.py) without
    # blocking the loop, while sqlite still runs in a thread.
    @app.post("/api/saved-trips", response_model=SavedTrip, status_code=201)
    async def create_saved_trip(body: SavedTripIn, user: User = Depends(require_user)):
        body = await fill_routes(routes, body)
        try:
            return await asyncio.to_thread(trip_store.create, body, created_by=user.username)
        except TooManyTrips:
            raise HTTPException(status_code=409, detail="At most 500 saved trips – delete some first")

    @app.get("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def get_saved_trip(trip_id: str):
        return found(trip_store.get(trip_id))

    @app.put("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    async def replace_saved_trip(trip_id: str, body: SavedTripUpdate, user: User = Depends(require_user)):
        old = found(await asyncio.to_thread(trip_store.get, trip_id))
        # Stored routes are kept by the store (even if the editor sent the hike without one): fetch only the rest.
        body = await fill_routes(routes, body, have={h.id for h in old.hikes if h.route})
        try:
            return found(await asyncio.to_thread(trip_store.replace, trip_id, body,
                                                 expected_updated_at=body.expected_updated_at))
        except TripConflict:
            raise HTTPException(status_code=409,
                                detail="Trip changed elsewhere – reopen it to see the latest version")

    @app.delete("/api/saved-trips/{trip_id}", status_code=204)
    def delete_saved_trip(trip_id: str, user: User = Depends(require_user)):
        if not trip_store.delete(trip_id):
            raise HTTPException(status_code=404, detail="Trip not found")
        return Response(status_code=204)

    @app.post("/api/saved-trips/{trip_id}/items", response_model=SavedTrip)
    async def add_saved_trip_item(trip_id: str, body: TripItemAdd, user: User = Depends(require_user)):
        if body.hike is not None and missing_route_ids([body.hike]):
            trip = found(await asyncio.to_thread(trip_store.get, trip_id))
            if all(h.id != body.hike.id for h in trip.hikes):  # already in the trip: nothing to fetch
                route = await routes.route(body.hike.id)
                if route:
                    body = body.model_copy(update={"hike": body.hike.model_copy(update={"route": route})})
        try:
            return found(await asyncio.to_thread(trip_store.add_item, trip_id, body))
        except TripFull as full:
            raise HTTPException(status_code=409, detail="A trip holds at most 100 nature sights"
                                if full.kind == "sights" else "A trip holds at most 50 hikes and 50 stays")
        except TripTooLarge:
            raise HTTPException(status_code=413, detail=TRIP_TOO_LARGE)

    @app.post("/api/saved-trips/{trip_id}/nearby-sights", response_model=NearbySightsResult)
    async def add_nearby_sights(trip_id: str, body: NearbySightsIn | None = None,
                                user: User = Depends(require_user)):
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
