from app.cache import DiskCache
from app.config import Settings
from app.engine import Engine
from app.geocode import Geocoder
from app.routing import Router
from app.sources.stays.airbnb import AirbnbProvider
from app.sources.stays.booking import BookingProvider
from app.sources.nature import DailyBudget, NatureSource
from app.sources.wikiloc import WikilocSource


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or Settings()
    cache = DiskCache(settings.cache_dir)
    return Engine(
        settings=settings,
        wikiloc=WikilocSource(pages=settings.hike_pages, cookie=settings.wikiloc_cookie,
                             concurrency=settings.wikiloc_concurrency),
        providers=[AirbnbProvider(detail_top_n=settings.airbnb_detail_top_n, area_pages=settings.airbnb_pages),
                   BookingProvider(area_pages=settings.booking_pages)],
        router=Router(cache, settings.ors_api_key),
        geocoder=Geocoder(cache, min_radius_km=settings.stay_radius_km),
        cache=cache,
        nature=NatureSource(budget=DailyBudget(cache)),
    )
