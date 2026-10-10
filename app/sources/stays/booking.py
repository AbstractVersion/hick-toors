import asyncio
import json
import re
import sys
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from app.models import Stay, StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable

# The plain /searchresults.html is always challenged; the en-gb page passes about half the time.
SEARCH_URL = "https://www.booking.com/searchresults.en-gb.html"
PETS_FILTER = "hotelfacility=4"
# Verified nflt tokens (probed 2026-10-10). Repeating a field ORs its values; fields AND together.
PROPERTY_TYPE_IDS = {"house": [220, 223], "apartment": [201], "guesthouse": [216, 208], "hotel": [204],
                     "villa": [213]}
FACILITY_TOKENS = {"parking": "hotelfacility=2", "wifi": "hotelfacility=107", "pool": "hotelfacility=433",
                   "hot_tub": "hotelfacility=63", "ev_charger": "hotelfacility=182",
                   "ac": "roomfacility=11", "kitchen": "roomfacility=999", "washer": "roomfacility=34"}
BEACHFRONT = "hotelfacility=146"
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


def review_score(min_rating: float) -> int | None:
    """0-5 rating -> Booking review_score step (60/70/80/90), floored; below 3.0 no token."""
    score = int(round(min_rating * 20, 6)) // 10 * 10
    return min(score, 90) if score >= 60 else None


def nflt_tokens(f: StayFilters, dog: bool) -> list[str]:
    """StayFilters (+ dog) -> Booking nflt tokens. min_beds, instant_book, guest_favorite and the
    Airbnb-only amenities have no token; the engine skips Booking when they are active."""
    tokens = [PETS_FILTER] if dog else []
    if f.price_min is not None or f.price_max is not None:
        low = f.price_min if f.price_min is not None else "min"
        high = f.price_max if f.price_max is not None else "max"
        tokens.append(f"price=EUR-{low}-{high}-1")
    if f.min_rating is not None and review_score(f.min_rating) is not None:
        tokens.append(f"review_score={review_score(f.min_rating)}")
    if f.entire_place:
        tokens.append("privacy_type=3")
    ht_ids = sorted({i for t in f.property_types for i in PROPERTY_TYPE_IDS[t]})
    tokens += [f"ht_id={i}" for i in ht_ids]
    if f.min_bedrooms is not None:
        tokens.append(f"entire_place_bedroom_count={min(f.min_bedrooms, 5)}")
    if f.min_bathrooms is not None:
        tokens.append(f"min_bathrooms={f.min_bathrooms}")
    tokens += [FACILITY_TOKENS[a] for a in f.amenities if a in FACILITY_TOKENS]
    if f.free_cancellation:
        tokens.append("fc=2")
    if f.breakfast:
        tokens.append("mealplan=1")
    if f.min_stars is not None:
        tokens += [f"class={n}" for n in range(f.min_stars, 6)]
    if f.beachfront:
        tokens.append(BEACHFRONT)
    return tokens


async def default_fetch(url: str, params: dict) -> tuple[int, str]:
    """One request in a brand-new impersonated session: each retry gets a fresh fingerprint."""
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(url, params=params, timeout=30)
        return response.status_code, response.text


class BookingProvider:
    name = "Booking.com"

    def __init__(self, fetch: FetchStatus | None = None, retries: int = 4,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, area_pages: int = 2):
        self._fetch = fetch or default_fetch
        self._retries = retries
        self._sleep = sleep
        self._area_pages = area_pages

    async def _page(self, params: dict) -> list[Stay] | None:
        """One page with retries; None when every attempt was blocked or failed."""
        for attempt in range(self._retries):
            try:
                status, html = await self._fetch(SEARCH_URL, params)
                if status == 200:
                    return parse_search(html)
            except BookingBlocked:
                pass
            except Exception as exc:
                print(f"booking request failed: {exc}", file=sys.stderr)
            if attempt < self._retries - 1:
                await self._sleep(2 * (attempt + 1))
        return None

    async def search(self, q: StayQuery) -> list[Stay]:
        params = {
            "latitude": q.lat, "longitude": q.lon, "radius": round(q.radius_km),
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "group_adults": q.guests, "no_rooms": 1, "group_children": 0,
            "lang": "en-gb", "selected_currency": "EUR",
        }
        tokens = nflt_tokens(q.filters, q.dog)
        if tokens:
            params["nflt"] = ";".join(tokens)
        stays = await self._page(params)
        if stays is None:
            raise ProviderUnavailable("Booking.com unavailable (blocked by the site) – showing other sources")
        # Drawn areas: further pages via `offset` (Booking has no verified bbox param; it ignores radius).
        seen = {s.id for s in stays}
        received = len(stays)
        for _ in range(1, self._area_pages if q.bbox is not None else 1):
            more = await self._page({**params, "offset": received})
            if not more:
                break
            received += len(more)
            fresh = [s for s in more if s.id not in seen]
            seen.update(s.id for s in fresh)
            stays += fresh
        link = urlencode({"checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
                          "group_adults": q.guests})
        for stay in stays:
            if q.dog:
                stay.pets_allowed = True
            stay.url = f"{stay.url}?{link}"
        return stays
