from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, Settings
from app.models import HikeQuery, HikeSearch, StaySearch, StaySearchQuery, TripQuery, TripSearch
from app.wiring import build_engine

STATIC_DIR = BASE_DIR / "static"


def create_app(engine=None, ask_runner=None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or build_engine(settings)
    app = FastAPI(title="hick-toors")
    app.state.engine = engine
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

    @app.post("/api/hikes", response_model=HikeSearch)
    async def hikes(q: HikeQuery):
        return await engine.search_hikes(q)

    @app.post("/api/stays", response_model=StaySearch)
    async def stays(q: StaySearchQuery):
        return await engine.search_stays(q)

    @app.post("/api/trips", response_model=TripSearch)
    async def trips(q: TripQuery):
        return await engine.plan_trip(q)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
