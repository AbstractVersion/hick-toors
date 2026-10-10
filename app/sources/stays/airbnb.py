import asyncio
import base64
import binascii
import json
import re
import sys
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from app.geo import bbox_around
from app.models import LatLon, Stay, StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable

SEARCH_URL = "https://www.airbnb.com/s/homes"
ROOM_URL = "https://www.airbnb.com/rooms/{id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
STATE_RE = re.compile(r'<script id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S)
TITLE_RE = re.compile(r'"title":"([^"]*)"')
GARDEN_RE = re.compile(r"garden|backyard|\byard\b", re.I)
PRICE_RE = re.compile(r"([€$£])\s?([\d.,]+)")
RATING_RE = re.compile(r"^\s*([\d.]+) out of 5")
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}
# Verified search params (probed 2026-10-10); villa has no own type on Airbnb, so it maps to House.
PROPERTY_TYPE_IDS = {"house": 1, "guesthouse": 2, "apartment": 3, "hotel": 4, "villa": 1}
AMENITY_IDS = {"wifi": 4, "ac": 5, "pool": 7, "kitchen": 8, "parking": 9, "hot_tub": 25, "fireplace": 27,
               "heating": 30, "washer": 33, "workspace": 47, "ev_charger": 97, "bbq": 99}
BEACHFRONT_TAG = "Tag:789"

FetchText = Callable[[str, dict], Awaitable[str]]


class AirbnbParseError(Exception):
    pass


def _walk_results(node: Any) -> list[dict]:
    """Find every search result dict (has 'demandStayListing') wherever Airbnb nests it."""
    found = []
    if isinstance(node, dict):
        if "demandStayListing" in node:
            return [node]
        for value in node.values():
            found.extend(_walk_results(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_results(value))
    return found


def _listing_id(encoded: str) -> str:
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.b64decode(padded).decode().split(":")[-1]
    except (binascii.Error, UnicodeDecodeError):
        return encoded


def _price(result: dict) -> tuple[float | None, str | None]:
    line = ((result.get("structuredDisplayPrice") or {}).get("primaryLine") or {})
    text = line.get("accessibilityLabel") or line.get("price") or ""
    matches = PRICE_RE.findall(text)
    if not matches:
        return None, None
    symbol, number = matches[-1]
    try:
        return float(number.replace(",", "")), CURRENCIES.get(symbol)
    except ValueError:
        return None, None


def _rating(label: str | None) -> float | None:
    match = RATING_RE.match(label or "")
    return float(match.group(1)) if match else None


def parse_search(html: str) -> list[Stay]:
    match = STATE_RE.search(html)
    if not match:
        raise AirbnbParseError("no data-deferred-state script")
    try:
        state = json.loads(match.group(1))
    except ValueError as exc:
        raise AirbnbParseError("state script is not JSON") from exc
    stays = []
    for result in _walk_results(state):
        listing = result["demandStayListing"]
        coord = ((listing.get("location") or {}).get("coordinate") or {})
        if coord.get("latitude") is None or coord.get("longitude") is None or not listing.get("id"):
            continue
        listing_id = _listing_id(listing["id"])
        name = ((result.get("nameLocalized") or {}).get("localizedStringWithTranslationPreference")
                or result.get("subtitle") or result.get("title") or "Airbnb stay")
        price, currency = _price(result)
        pictures = result.get("contextualPictures") or []
        stays.append(Stay(
            id=f"airbnb:{listing_id}",
            source="airbnb",
            name=name,
            url=ROOM_URL.format(id=listing_id),
            lat=coord["latitude"],
            lon=coord["longitude"],
            total_price=price,
            currency=currency,
            rating=_rating(result.get("avgRatingA11yLabel")),
            photo=pictures[0].get("picture") if pictures else None,
        ))
    return stays


def filter_params(f: StayFilters) -> dict:
    """StayFilters -> Airbnb search params (all AND together). List values become repeated keys.

    min_rating, breakfast and min_stars have no Airbnb param: rating is post-checked by the
    engine, and the other two make the engine skip Airbnb.
    """
    params: dict = {}
    if f.price_min is not None:
        params["price_min"] = f.price_min
    if f.price_max is not None:
        params["price_max"] = f.price_max
    if f.entire_place:
        params["room_types[]"] = ["Entire home/apt"]
    if f.property_types:
        params["l2_property_type_ids[]"] = sorted({PROPERTY_TYPE_IDS[t] for t in f.property_types})
    for key in ("min_bedrooms", "min_beds", "min_bathrooms"):
        if getattr(f, key) is not None:
            params[key] = getattr(f, key)
    if f.amenities:
        params["amenities[]"] = sorted(AMENITY_IDS[a] for a in f.amenities)
    if f.guest_favorite:
        params["guest_favorite"] = "true"
    if f.instant_book:
        params["ib"] = "true"
    if f.free_cancellation:
        params["flexible_cancellation"] = "true"
    if f.beachfront:
        params["kg_and_tags[]"] = [BEACHFRONT_TAG]
    return params


def parse_details(html: str) -> tuple[bool | None, bool]:
    """(pets_allowed, has_garden) from a listing page's amenity/house-rule titles."""
    titles = [t.strip() for t in TITLE_RE.findall(html)]
    has_garden = any(GARDEN_RE.search(t) for t in titles)
    lowered = {t.lower() for t in titles}
    if "pets allowed" in lowered:
        pets: bool | None = True
    elif lowered & {"no pets", "pets not allowed"}:
        pets = False
    else:
        pets = None
    return pets, has_garden


async def fetch_text(url: str, params: dict) -> str:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        return response.text


class AirbnbProvider:
    name = "Airbnb"

    def __init__(self, fetch: FetchText | None = None, detail_top_n: int = 10, concurrency: int = 3):
        self._fetch = fetch or fetch_text
        self._top_n = detail_top_n
        self._concurrency = concurrency

    def _search_params(self, q: StayQuery) -> dict:
        box = bbox_around(LatLon(lat=q.lat, lon=q.lon), q.radius_km)
        return {
            "ne_lat": box.north, "ne_lng": box.east, "sw_lat": box.south, "sw_lng": box.west,
            "search_by_map": "true", "search_type": "user_map_move",
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "adults": q.guests, "currency": "EUR",
            **filter_params(q.filters),
        }

    async def _search(self, params: dict) -> list[Stay]:
        try:
            return parse_search(await self._fetch(SEARCH_URL, params))
        except (httpx.HTTPError, AirbnbParseError, ValueError) as exc:
            raise ProviderUnavailable("Airbnb unavailable – showing other sources") from exc

    async def _enrich(self, stays: list[Stay]) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(stay: Stay) -> None:
            async with semaphore:
                try:
                    html = await self._fetch(stay.url, {})
                except Exception as exc:
                    print(f"airbnb detail fetch failed for {stay.id}: {exc}", file=sys.stderr)
                    return
            pets, garden = parse_details(html)
            stay.has_garden = garden
            if stay.pets_allowed is None:
                stay.pets_allowed = pets

        await asyncio.gather(*(one(s) for s in stays))

    async def search(self, q: StayQuery) -> list[Stay]:
        base = self._search_params(q)
        if not q.dog:
            stays = await self._search(base)
        else:
            pet_stays = await self._search({**base, "pets": 1})
            for stay in pet_stays:
                stay.pets_allowed = True
            pet_ids = {s.id for s in pet_stays}
            try:
                others = [s for s in await self._search(base) if s.id not in pet_ids]
            except ProviderUnavailable as exc:
                print(f"airbnb: no-pets search failed, keeping pet results: {exc}", file=sys.stderr)
                others = []
            await self._enrich(pet_stays[: self._top_n] + others[: self._top_n])
            stays = pet_stays + others
        link = {"check_in": q.check_in.isoformat(), "check_out": q.check_out.isoformat(), "adults": q.guests}
        if q.dog:
            link["pets"] = 1
        for stay in stays:
            stay.url = f"{stay.url}?{urlencode(link)}"
        return stays
