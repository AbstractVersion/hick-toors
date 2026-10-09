from typing import Any, Awaitable, Callable

import httpx

from app.cache import DAY, DiskCache
from app.geo import ensure_min_size
from app.models import BBox

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "hick-toors/0.1 (personal local app)"

FetchJson = Callable[[str, dict], Awaitable[Any]]


async def fetch_json(url: str, params: dict) -> Any:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()


class Geocoder:
    def __init__(self, cache: DiskCache, fetch: FetchJson | None = None, min_radius_km: float = 15):
        self._cache = cache
        self._fetch = fetch or fetch_json
        self._min_radius_km = min_radius_km

    async def geocode(self, place: str) -> BBox | None:
        key = f"geocode:{place.strip().lower()}"
        cached = self._cache.get(key)
        if cached:
            return BBox(**cached)
        data = await self._fetch(
            NOMINATIM_URL, {"q": place.strip(), "format": "json", "limit": 1, "countrycodes": "gr"}
        )
        if not data:
            return None
        south, north, west, east = (float(v) for v in data[0]["boundingbox"])
        box = ensure_min_size(BBox(south=south, west=west, north=north, east=east), self._min_radius_km)
        self._cache.set(key, box.model_dump(), 30 * DAY)
        return box
