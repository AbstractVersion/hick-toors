import sys
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ask import AskRequest, AskResult, AskRunner
from app.config import BASE_DIR, Settings
from app.filters import catalogue
from app.models import (HikeQuery, HikeSearch, SavedTrip, SavedTripIn, SavedTripSummary, StaySearch,
                        StaySearchQuery, TripItemAdd, TripQuery, TripSearch)
from app.trip_markdown import content_disposition, trip_markdown
from app.trips_store import TripFull, TripStore
from app.wiring import build_engine

STATIC_DIR = BASE_DIR / "static"


def create_app(engine=None, ask_runner=None, settings: Settings | None = None,
               trip_store: TripStore | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    app = FastAPI(title="hick-toors")
    app.state.engine = engine
    ask_runner = ask_runner or AskRunner(settings, engine.lookup)
    app.state.ask = ask_runner
    trip_store = trip_store or TripStore(settings.trips_db)
    app.state.trips = trip_store

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

    @app.post("/api/trips", response_model=TripSearch)
    async def trips(q: TripQuery):
        return await engine.plan_trip(q)

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
        return trip_store.create(body)

    @app.get("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def get_saved_trip(trip_id: str):
        return found(trip_store.get(trip_id))

    @app.put("/api/saved-trips/{trip_id}", response_model=SavedTrip)
    def replace_saved_trip(trip_id: str, body: SavedTripIn):
        return found(trip_store.replace(trip_id, body))

    @app.delete("/api/saved-trips/{trip_id}", status_code=204)
    def delete_saved_trip(trip_id: str):
        if not trip_store.delete(trip_id):
            raise HTTPException(status_code=404, detail="Trip not found")
        return Response(status_code=204)

    @app.post("/api/saved-trips/{trip_id}/items", response_model=SavedTrip)
    def add_saved_trip_item(trip_id: str, body: TripItemAdd):
        try:
            return found(trip_store.add_item(trip_id, body))
        except TripFull:
            raise HTTPException(status_code=409, detail="A trip holds at most 50 hikes and 50 stays")

    @app.get("/api/saved-trips/{trip_id}/export.md")
    def export_saved_trip(trip_id: str):
        trip = found(trip_store.get(trip_id))
        return Response(trip_markdown(trip, date.today()), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": content_disposition(trip.name)})

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
