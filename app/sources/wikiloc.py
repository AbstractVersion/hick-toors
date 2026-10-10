import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from app.models import BBox, Hike, HikeFilters

SEARCH_URL = "https://es.wikiloc.com/wikiloc/find.do"
BASE_URL = "https://es.wikiloc.com"
USER_AGENT = "wikiloc-app/1.0"
PAGE_SIZE = 25
GREECE_MARKERS = ("Greece", "Ελλάδα", "Grecia")
SKILL = {1: "Easy", 2: "Moderate", 3: "Hard", 4: "Very Hard", 5: "Experts Only"}

Fetch = Callable[..., Awaitable[Any]]  # fetch(url, params) or fetch(url, params, headers)


class WikilocUnavailable(Exception):
    pass


class WikilocLoginExpired(Exception):
    """A logged-in, server-filtered search came back empty or asked to log in again."""


def server_filter_params(f: HikeFilters) -> dict:
    """HikeFilters -> find.do filter params. Wikiloc honours them only for logged-in callers
    (probed 2026-10-10); anonymously they return count 0, so they are sent only with a cookie."""
    params: dict = {"a": ",".join(str(a) for a in f.activities)}
    if f.distance_min_km is not None:
        params["lfr"] = f"{f.distance_min_km:g}"
    if f.distance_max_km is not None:
        params["lto"] = f"{f.distance_max_km:g}"
    if f.elevation_min_m is not None:
        params["ufr"] = f.elevation_min_m
    if f.elevation_max_m is not None:
        params["uto"] = f.elevation_max_m
    if (f.difficulty_min, f.difficulty_max) != (1, 5):
        params["sfr"] = min(f.difficulty_min, 4)  # the filter scale is 1-4, results are 1-5
        if f.difficulty_max < 5:
            params["sto"] = min(f.difficulty_max, 4)
    if f.loop_only:
        params["t"] = 1
    return params


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


@dataclass
class WikilocResult:
    hikes: list[Hike] = field(default_factory=list)
    # Wikiloc's `count` from the first page of the call: total routes in the whole bbox for that
    # query. Anonymously it covers every activity (filters are not applied server-side), so it can
    # exceed what the local filters leave. None when Wikiloc did not report it.
    total: int | None = None
    pages_fetched: int = 0
    has_more: bool = False  # the last batch was full, so more pages probably exist


def _count(data: dict) -> int | None:
    value = data.get("count")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


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

    async def fetch(url: str, params: dict, headers: dict | None = None) -> Any:
        # headers may carry the Wikiloc session cookie: never log them.
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, params=params, headers=headers, timeout=30.0)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    # With extra headers (the session cookie) the message may echo them: name only.
                    detail = type(exc).__name__ if headers else exc
                    print(f"wikiloc request failed: {detail}", file=sys.stderr)
                    if isinstance(exc, httpx.LocalProtocolError):
                        return None  # deterministic (e.g. malformed header): retrying cannot help
                    if attempt < retries:
                        await sleep(1.5 * (attempt + 1))
        return None

    return fetch


class WikilocSource:
    def __init__(self, fetch: Fetch | None = None, pages: int = 2, cookie: str | None = None,
                 concurrency: int = 4):
        self._fetch = fetch or make_fetch()
        self._pages = pages
        self._concurrency = max(1, concurrency)
        self._cookie = cookie or None  # Wikiloc session cookie (WIKILOC_COOKIE); never logged

    @property
    def logged_in(self) -> bool:
        return self._cookie is not None

    def __repr__(self) -> str:  # keep the cookie out of any debug output
        return f"WikilocSource(pages={self._pages}, logged_in={self.logged_in})"

    async def search(self, bbox: BBox, query: str = "", sort: str = "popular",
                     pages: int | None = None, filters: HikeFilters | None = None,
                     use_login: bool = True, start_page: int = 0) -> WikilocResult:
        """Routes in bbox. sort="top_rated" asks Wikiloc for its TrailRank order; pages overrides
        the configured page count; start_page skips that many pages (Load more). Pages are fetched
        concurrently in batches of `concurrency`; after a batch in which any page was short (< 25)
        the data has ended. With a cookie (and use_login) the cookie is sent, plus `filters` as
        server params; raises WikilocLoginExpired when the first page of a fresh search (start_page
        0) comes back empty or Wikiloc asks to log in, so the caller can retry anonymously."""
        login = use_login and self._cookie is not None
        headers = {"Cookie": self._cookie} if login else None
        server = server_filter_params(filters) if login and filters is not None else {}
        wanted = pages or self._pages
        hikes: list[Hike] = []
        seen: set[str] = set()
        total: int | None = None
        fetched = 0
        has_more = False

        def params_for(page: int) -> dict:
            # Wikiloc pages with from/to offsets (max 25 per request); it ignores a `page` param.
            offset = (start_page + page) * PAGE_SIZE
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
            params.update(server)
            return params

        async def get(params: dict):
            return await (self._fetch(SEARCH_URL, params, headers) if login else self._fetch(SEARCH_URL, params))

        for batch_start in range(0, wanted, self._concurrency):
            batch = range(batch_start, min(batch_start + self._concurrency, wanted))
            batch_params = [params_for(page) for page in batch]
            responses = await asyncio.gather(*(get(p) for p in batch_params))
            full = True
            for page, params, data in zip(batch, batch_params, responses):
                first = page == 0
                if not isinstance(data, dict):
                    if first and start_page == 0 and login:
                        raise WikilocLoginExpired()  # engine retries anonymously instead of blaming rate limits
                    if first:
                        raise WikilocUnavailable(
                            "Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute"
                        )
                    # A later page failed: keep the pages before it (offsets stay contiguous) and
                    # let Load more resume from here.
                    return WikilocResult(hikes, total, fetched, full)
                if first and login:
                    # Also on Load more: an expired session must not look like end-of-data.
                    restricted = bool(data.get("forwardToLogin") or data.get("restricted"))
                    if restricted:
                        raise WikilocLoginExpired()
                if first and start_page == 0 and login:
                    if server and not data.get("spas"):
                        if "t" in server:
                            # No circular routes, or an expired login? Ask again without the loop filter.
                            retry = await self._fetch(SEARCH_URL, {k: v for k, v in params.items() if k != "t"},
                                                      headers)
                            if (isinstance(retry, dict) and retry.get("spas")
                                    and not (retry.get("forwardToLogin") or retry.get("restricted"))):
                                return WikilocResult([], _count(retry), 1, False)  # login works: no circular routes
                        raise WikilocLoginExpired()
                if first:
                    total = _count(data)
                fetched += 1
                for hike in parse_search(data):
                    if hike.id not in seen:
                        seen.add(hike.id)
                        hikes.append(hike)
                if len(data.get("spas") or []) < PAGE_SIZE:
                    full = False
            if not full:
                return WikilocResult(hikes, total, fetched, False)
            has_more = True
        return WikilocResult(hikes, total, fetched, has_more)
