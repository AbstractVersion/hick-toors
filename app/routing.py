import sys
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel

from app.cache import DAY, DiskCache
from app.geo import haversine_km
from app.models import LatLon

ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
FALLBACK_KMH = 60
FALLBACK_WARNING = "Driving times unavailable – showing ≈ estimated times"

PostJson = Callable[[str, dict, dict], Awaitable[Any]]


class RouteTime(BaseModel):
    minutes: int | None
    estimated: bool
    reachable: bool = True


async def post_json(url: str, body: dict, headers: dict) -> Any:
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=body, headers=headers, timeout=30.0)
        response.raise_for_status()
        return response.json()


def _key(origin: LatLon, dest: LatLon) -> str:
    return f"drive:{origin.lat:.4f},{origin.lon:.4f}->{dest.lat:.4f},{dest.lon:.4f}"


class Router:
    def __init__(self, cache: DiskCache, api_key: str | None, post: PostJson | None = None):
        self._cache = cache
        self._api_key = api_key
        self._post = post or post_json

    async def drive_times(self, origin: LatLon, dests: list[LatLon]) -> tuple[list[RouteTime], str | None]:
        results: list[RouteTime | None] = []
        for dest in dests:
            cached = self._cache.get(_key(origin, dest))
            results.append(RouteTime(**cached) if cached else None)
        missing = [i for i, r in enumerate(results) if r is None]
        warning = None
        if missing and self._api_key:
            body = {
                "locations": [[origin.lon, origin.lat]] + [[dests[i].lon, dests[i].lat] for i in missing],
                "sources": [0],
                "destinations": list(range(1, len(missing) + 1)),
                "metrics": ["duration"],
            }
            try:
                data = await self._post(ORS_MATRIX_URL, body, {"Authorization": self._api_key})
                durations = data["durations"][0]
                for i, seconds in zip(missing, durations):
                    if seconds is None:
                        rt = RouteTime(minutes=None, estimated=False, reachable=False)
                    else:
                        rt = RouteTime(minutes=round(seconds / 60), estimated=False)
                    results[i] = rt
                    self._cache.set(_key(origin, dests[i]), rt.model_dump(), 7 * DAY)
            except Exception as exc:
                print(f"routing failed: {exc}", file=sys.stderr)
                warning = FALLBACK_WARNING
        final = []
        for dest, rt in zip(dests, results):
            if rt is None:
                km = haversine_km(origin, dest)
                rt = RouteTime(minutes=round(km / FALLBACK_KMH * 60), estimated=True)
            final.append(rt)
        return final, warning
