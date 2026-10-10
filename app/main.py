import sys

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ask import AskRequest, AskResult, AskRunner
from app.config import BASE_DIR, Settings
from app.filters import catalogue
from app.models import HikeQuery, HikeSearch, StaySearch, StaySearchQuery, TripQuery, TripSearch
from app.wiring import build_engine

STATIC_DIR = BASE_DIR / "static"


def create_app(engine=None, ask_runner=None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    app = FastAPI(title="hick-toors")
    app.state.engine = engine
    ask_runner = ask_runner or AskRunner(settings, engine.lookup)
    app.state.ask = ask_runner

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

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
