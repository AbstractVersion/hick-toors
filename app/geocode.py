import re
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel

from app.cache import DAY, DiskCache
from app.geo import ensure_min_size
from app.models import BBox

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "hick-toors/0.1 (personal local app)"

GREEK_RE = re.compile("[Ͱ-Ͽἀ-῿]")
FetchJson = Callable[[str, dict], Awaitable[Any]]


async def fetch_json(url: str, params: dict) -> Any:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params=params, timeout=20.0)
        response.raise_for_status()
        return response.json()


GEO_CLASSES = {"boundary", "place", "natural", "mountain_pass", "waterway"}
GEO_TYPES = {"region", "state", "municipality", "county", "city", "town", "village", "hamlet", "locality",
             "peak", "mountain_range", "island", "archipelago", "valley", "gorge", "lake", "beach",
             "administrative"}


def _area(c: dict) -> float:
    try:
        south, north, west, east = (float(v) for v in c["boundingbox"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return abs(north - south) * abs(east - west)


def _pick_best(candidates: list[dict]) -> dict:
    """Prefer real places over POIs; among them highest importance (0.02 near-ties go to the larger
    bbox), then a sensible type, then Nominatim order."""
    geo = [(i, c) for i, c in enumerate(candidates) if c.get("class") in GEO_CLASSES]
    if not geo:
        return candidates[0]
    top = max(float(c.get("importance") or 0) for _, c in geo)
    tied = [(i, c) for i, c in geo if top - float(c.get("importance") or 0) <= 0.02]

    def rank(item: tuple[int, dict]):
        i, c = item
        typed = c.get("addresstype") in GEO_TYPES or c.get("type") in GEO_TYPES
        return (_area(c), typed, -i)
    return max(tied, key=rank)[1]


class Place(BaseModel):
    name: str
    lat: float
    lon: float
    bbox: BBox


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

    async def locate(self, text: str) -> Place | None:
        """Navigation lookup (Greek or English): the place itself with its exact, unwidened box."""
        lang = "el" if GREEK_RE.search(text) else "en"  # labels come back in the language you typed
        key = f"locate3:{lang}:{text.strip().casefold()}"
        cached = self._cache.get(key)
        if cached:
            return Place(**cached)
        data = await self._fetch(NOMINATIM_URL, {
            "q": text.strip(), "format": "json", "limit": 10, "countrycodes": "gr", "addressdetails": 0,
            "accept-language": lang})
        if not data:
            return None
        hit = _pick_best(data)
        south, north, west, east = (float(v) for v in hit["boundingbox"])
        label = ", ".join(p.strip() for p in str(hit.get("display_name", text)).split(",")[:2])
        place = Place(name=label, lat=float(hit["lat"]), lon=float(hit["lon"]),
                      bbox=BBox(south=south, west=west, north=north, east=east))
        self._cache.set(key, place.model_dump(), 30 * DAY)
        return place
