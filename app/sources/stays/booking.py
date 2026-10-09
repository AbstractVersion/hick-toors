import asyncio
import json
import re
import sys
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from app.models import Stay, StayQuery
from app.sources.stays.base import ProviderUnavailable

SEARCH_URL = "https://www.booking.com/searchresults.html"
PETS_FILTER = "hotelfacility=4"
PHOTO_BASE = "https://cf.bstatic.com"
JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)

FetchStatus = Callable[[str, dict], Awaitable[tuple[int, str]]]


class BookingBlocked(Exception):
    pass


def _walk_properties(node: Any) -> list[dict]:
    found = []
    if isinstance(node, dict):
        if "basicPropertyData" in node and "displayName" in node:
            return [node]
        for value in node.values():
            found.extend(_walk_properties(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_properties(value))
    return found


def parse_search(html: str) -> list[Stay]:
    props: list[dict] = []
    seen: set = set()
    for blob in JSON_SCRIPT_RE.findall(html):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for prop in _walk_properties(data):
            pid = (prop.get("basicPropertyData") or {}).get("id")
            if pid is not None and pid not in seen:
                seen.add(pid)
                props.append(prop)
    if not props and "searchQueries" not in html:
        raise BookingBlocked("no search results data on page")
    stays = []
    for prop in props:
        basic = prop["basicPropertyData"]
        loc = basic.get("location") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        score = (basic.get("reviews") or {}).get("totalScore")
        price = (((prop.get("priceDisplayInfoIrene") or {}).get("displayPrice") or {})
                 .get("amountPerStay") or {})
        amount = price.get("amountUnformatted")
        page = basic.get("pageName")
        photo = ((((basic.get("photos") or {}).get("main") or {}).get("highResUrl") or {})
                 .get("relativeUrl"))
        stays.append(Stay(
            id=f"booking:{basic['id']}",
            source="booking",
            name=(prop.get("displayName") or {}).get("text") or "Booking.com stay",
            url=(f"https://www.booking.com/hotel/{loc.get('countryCode', 'gr')}/{page}.html"
                 if page else "https://www.booking.com"),
            lat=loc["latitude"],
            lon=loc["longitude"],
            total_price=round(amount, 2) if amount else None,
            currency=price.get("currency") or None,
            rating=round(score / 2, 2) if score else None,
            photo=f"{PHOTO_BASE}{photo}" if photo else None,
        ))
    return stays


async def default_fetch(url: str, params: dict) -> tuple[int, str]:
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(url, params=params, timeout=30)
        return response.status_code, response.text


class BookingProvider:
    name = "Booking.com"

    def __init__(self, fetch: FetchStatus | None = None, retries: int = 3,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._fetch = fetch or default_fetch
        self._retries = retries
        self._sleep = sleep

    async def search(self, q: StayQuery) -> list[Stay]:
        params = {
            "latitude": q.lat, "longitude": q.lon, "radius": round(q.radius_km),
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "group_adults": q.guests, "no_rooms": 1, "group_children": 0,
            "lang": "en-us", "selected_currency": "EUR",
        }
        if q.dog:
            params["nflt"] = PETS_FILTER
        stays = None
        for attempt in range(self._retries):
            try:
                status, html = await self._fetch(SEARCH_URL, params)
                if status == 200:
                    stays = parse_search(html)
                    break
            except BookingBlocked:
                pass
            except Exception as exc:
                print(f"booking request failed: {exc}", file=sys.stderr)
            if attempt < self._retries - 1:
                await self._sleep(2 * (attempt + 1))
        if stays is None:
            raise ProviderUnavailable("Booking.com unavailable (blocked by the site) – showing other sources")
        link = urlencode({"checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
                          "group_adults": q.guests})
        for stay in stays:
            if q.dog:
                stay.pets_allowed = True
            stay.url = f"{stay.url}?{link}"
        return stays
