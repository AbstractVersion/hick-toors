import asyncio
import sys
from typing import Any, Awaitable, Callable

import httpx

from app.models import BBox, Hike

SEARCH_URL = "https://es.wikiloc.com/wikiloc/find.do"
BASE_URL = "https://es.wikiloc.com"
USER_AGENT = "wikiloc-app/1.0"
PAGE_SIZE = 25
GREECE_MARKERS = ("Greece", "Ελλάδα", "Grecia")
SKILL = {1: "Easy", 2: "Moderate", 3: "Hard", 4: "Very Hard", 5: "Experts Only"}

Fetch = Callable[[str, dict], Awaitable[Any]]


class WikilocUnavailable(Exception):
    pass


def _num(value) -> float | None:
    """Parse Wikiloc's Spanish-locale numbers: '11,81' -> 11.81, '1.136' -> 1136."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _in_greece(near: str | None) -> bool:
    return not near or any(marker in near for marker in GREECE_MARKERS)


def parse_search(data: dict) -> list[Hike]:
    """Every Greek route of any activity; the engine filters activities (HikeFilters.activities)."""
    hikes = []
    for spa in data.get("spas") or []:
        if not _in_greece(spa.get("near")):
            continue
        if spa.get("lat") is None or spa.get("lon") is None:
            continue
        slope = _num(spa.get("slope"))
        thumbs = spa.get("thumbs") or []
        skill = spa.get("skill")
        hikes.append(
            Hike(
                id=f"wikiloc:{spa['id']}",
                name=spa.get("name") or "Unnamed route",
                url=f"{BASE_URL}{spa.get('prettyURL', '')}",
                lat=spa["lat"],
                lon=spa["lon"],
                distance_km=_num(spa.get("distance")),
                elevation_gain_m=int(slope) if slope is not None else None,
                difficulty=SKILL.get(skill),
                trailrank=spa.get("trailrank"),
                near=spa.get("near"),
                thumbnail=thumbs[0].get("url") if thumbs else None,
                activity=spa.get("picto") if isinstance(spa.get("picto"), int) else None,
                skill=skill if skill in SKILL else None,
            )
        )
    return hikes


def make_fetch(
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retries: int = 4,
) -> Fetch:
    """Build a JSON fetcher that retries Wikiloc's intermittent 403s with backoff."""

    async def fetch(url: str, params: dict) -> Any:
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, params=params, timeout=30.0)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    print(f"wikiloc request failed: {exc}", file=sys.stderr)
                    if attempt < retries:
                        await sleep(1.5 * (attempt + 1))
        return None

    return fetch


class WikilocSource:
    def __init__(self, fetch: Fetch | None = None, pages: int = 2):
        self._fetch = fetch or make_fetch()
        self._pages = pages

    async def search(self, bbox: BBox, query: str = "", sort: str = "popular",
                     pages: int | None = None) -> list[Hike]:
        """Routes in bbox. sort="top_rated" asks Wikiloc for its TrailRank order (the only
        server-side filter that works anonymously); pages overrides the configured page count."""
        hikes: list[Hike] = []
        seen: set[str] = set()
        for page in range(pages or self._pages):
            # Wikiloc pages with from/to offsets (max 25 per request); it ignores a `page` param.
            offset = page * PAGE_SIZE
            params = {
                "event": "map",
                "from": offset,
                "to": offset + PAGE_SIZE,
                "sw": f"{bbox.south},{bbox.west}",
                "ne": f"{bbox.north},{bbox.east}",
                "q": query,
            }
            if sort == "top_rated":
                params["sort"] = "trailrank"
            data = await self._fetch(SEARCH_URL, params)
            if not isinstance(data, dict):
                if page == 0:
                    raise WikilocUnavailable(
                        "Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute"
                    )
                break
            for hike in parse_search(data):
                if hike.id not in seen:
                    seen.add(hike.id)
                    hikes.append(hike)
            if len(data.get("spas") or []) < PAGE_SIZE:
                break
        return hikes
