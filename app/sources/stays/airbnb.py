import asyncio
import base64
import binascii
import json
import math
import re
import sys
from datetime import date
from html import unescape
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlsplit

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

LD_JSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
OG_RE = re.compile(r'<meta[^>]*property="og:(title|image)"[^>]*content="([^"]*)"', re.I)
ROOM_PATH_RE = re.compile(r"/rooms/(?:plus/)?([0-9]{1,25})/?")
ROOM_HOST_RE = re.compile(r"(?:www\.|m\.)?airbnb\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2})")
ROOM_ID_RE = re.compile(r"[0-9]{1,25}")  # ASCII only: \d also matches e.g. Arabic-Indic digits
LOOKUP_BOX_KM = 1.5  # price lookup: search a small box around the listing and pick it out by id
MAX_NAME = 200
MAX_PHOTO_URL = 2000
NO_ANSWER = "Airbnb didn't answer – try again in a minute"

FetchText = Callable[[str, dict], Awaitable[str]]


class AirbnbParseError(Exception):
    pass


class ListingNotFound(Exception):
    """The room page is gone (404/410): removed or private listing."""


def room_id_from_url(url: str) -> str | None:
    """The listing id of an Airbnb room link (any airbnb.<tld> host, /rooms/<id> or /rooms/plus/<id>),
    or of a bare id; None for anything else."""
    text = (url or "").strip()
    if ROOM_ID_RE.fullmatch(text):
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not ROOM_HOST_RE.fullmatch(parts.hostname or ""):
        return None
    match = ROOM_PATH_RE.fullmatch(parts.path)
    return match.group(1) if match else None


def _ld_objects(node: Any) -> list[dict]:
    """Every dict in a JSON-LD payload (top-level lists and @graph included)."""
    if isinstance(node, list):
        return [o for item in node for o in _ld_objects(item)]
    if isinstance(node, dict):
        return [node] + _ld_objects(node.get("@graph") or [])
    return []


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_room(html: str, room_id: str) -> Stay:
    """A Stay from a room page's schema.org JSON-LD (og: meta as fallback for name and photo).
    The page carries no price; raises AirbnbParseError without coordinates."""
    objects = []
    for block in LD_JSON_RE.findall(html):
        try:
            objects += _ld_objects(json.loads(block))
        except (ValueError, RecursionError):  # bad or absurdly nested JSON: skip that block
            continue
    place = next((o for o in objects if _float(o.get("latitude")) is not None
                  and _float(o.get("longitude")) is not None), None)
    if place is None:
        raise AirbnbParseError("room page has no coordinates")
    og = {key.lower(): unescape(value) for key, value in OG_RE.findall(html)}
    ordered = [place] + [o for o in objects if o is not place]
    names = [unescape(o["name"]).strip() for o in ordered if isinstance(o.get("name"), str)]
    name = next((n for n in names if n), None) or og.get("title", "").strip() or "Airbnb stay"
    images = [o["image"][0] if isinstance(o.get("image"), list) and o["image"] else o.get("image") for o in ordered]
    photos = [i for i in images + [og.get("image")] if isinstance(i, str) and 0 < len(i) <= MAX_PHOTO_URL]
    ratings = [_float(o["aggregateRating"].get("ratingValue")) for o in ordered
               if isinstance(o.get("aggregateRating"), dict)]
    rating = next((r for r in ratings if r is not None), None)
    return Stay(
        id=f"airbnb:{room_id}",
        source="airbnb",
        name=name[:MAX_NAME],
        url=ROOM_URL.format(id=room_id),
        lat=_float(place["latitude"]),
        lon=_float(place["longitude"]),
        rating=rating if rating is not None and 0 <= rating <= 5 else None,
        photo=photos[0] if photos else None,  # over-long URLs are dropped, not cut
    )


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


def _find_cursors(node: Any) -> list[str]:
    if isinstance(node, dict):
        if isinstance(node.get("pageCursors"), list):
            return [c for c in node["pageCursors"] if isinstance(c, str)]
        for value in node.values():
            found = _find_cursors(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_cursors(value)
            if found:
                return found
    return []


def parse_cursors(html: str) -> list[str]:
    """Page cursors of a search page (first = page 1); pass one as the `cursor` param for that page."""
    match = STATE_RE.search(html)
    if not match:
        return []
    try:
        return _find_cursors(json.loads(match.group(1)))
    except ValueError:
        return []


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

    def __init__(self, fetch: FetchText | None = None, detail_top_n: int = 10, concurrency: int = 3,
                 area_pages: int = 3):
        self._fetch = fetch or fetch_text
        self._top_n = detail_top_n
        self._concurrency = concurrency
        self._area_pages = area_pages

    def _search_params(self, q: StayQuery) -> dict:
        box = q.bbox or bbox_around(LatLon(lat=q.lat, lon=q.lon), q.radius_km)
        return {
            "ne_lat": box.north, "ne_lng": box.east, "sw_lat": box.south, "sw_lng": box.west,
            "search_by_map": "true", "search_type": "user_map_move",
            "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
            "adults": q.guests, "currency": "EUR",
            **filter_params(q.filters),
        }

    async def _page(self, params: dict) -> tuple[list[Stay], list[str]]:
        try:
            html = await self._fetch(SEARCH_URL, params)
            return parse_search(html), parse_cursors(html)
        except (httpx.HTTPError, AirbnbParseError, ValueError) as exc:
            raise ProviderUnavailable("Airbnb unavailable – showing other sources") from exc

    async def _search(self, params: dict, pages: int = 1) -> list[Stay]:
        """Page 1 must succeed; later pages (the response's own cursors) are best-effort."""
        stays, cursors = await self._page(params)
        seen = {s.id for s in stays}
        for cursor in cursors[1:pages]:
            try:
                more, _ = await self._page({**params, "cursor": cursor})
            except ProviderUnavailable as exc:
                print(f"airbnb: later page failed, keeping earlier pages: {exc}", file=sys.stderr)
                break
            fresh = [s for s in more if s.id not in seen]
            seen.update(s.id for s in fresh)
            stays += fresh
        return stays

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
        pages = self._area_pages if q.bbox is not None else 1
        if not q.dog:
            stays = await self._search(base, pages)
        else:
            pet_stays = await self._search({**base, "pets": 1}, pages)
            for stay in pet_stays:
                stay.pets_allowed = True
            pet_ids = {s.id for s in pet_stays}
            try:
                others = [s for s in await self._search(base, pages) if s.id not in pet_ids]
            except ProviderUnavailable as exc:
                print(f"airbnb: no-pets search failed, keeping pet results: {exc}", file=sys.stderr)
                others = []
            await self._enrich(pet_stays[: self._top_n] + others[: self._top_n])
            stays = pet_stays + others
        for stay in stays:
            stay.url = _room_link(stay.url, q)
        return stays

    async def _find(self, params: dict, stay_id: str) -> Stay | None:
        """The listing among one page of search results, or None (also when the search fails)."""
        try:
            stays = await self._search(params)
        except ProviderUnavailable as exc:
            print(f"airbnb: price lookup search failed: {exc}", file=sys.stderr)
            return None
        return next((s for s in stays if s.id == stay_id), None)

    async def lookup(self, room_id: str, check_in: date | None, check_out: date | None, guests: int,
                     dog: bool) -> Stay:
        """One listing by id: details from its room page; with dates, the total price from a search
        over a small box around it (pets search first when the dog comes along)."""
        try:
            html = await self._fetch(ROOM_URL.format(id=room_id), {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                raise ListingNotFound(room_id) from exc
            raise ProviderUnavailable(NO_ANSWER) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(NO_ANSWER) from exc
        stay = parse_room(html, room_id)
        stay.pets_allowed, stay.has_garden = parse_details(html)
        if check_in is None or check_out is None:
            return stay
        q = StayQuery(lat=stay.lat, lon=stay.lon, radius_km=LOOKUP_BOX_KM, check_in=check_in,
                      check_out=check_out, guests=guests, dog=dog,
                      bbox=bbox_around(LatLon(lat=stay.lat, lon=stay.lon), LOOKUP_BOX_KM))
        base = self._search_params(q)
        found = await self._find({**base, "pets": 1}, stay.id) if dog else None
        if found is not None:
            stay.pets_allowed = True
        else:
            found = await self._find(base, stay.id)
        if found is not None:
            stay.total_price, stay.currency = found.total_price, found.currency
        stay.url = _room_link(stay.url, q)
        return stay


def _room_link(url: str, q: StayQuery) -> str:
    """Room URL with the trip's dates/guests (and pets) so Airbnb opens it priced for the trip."""
    link = {"check_in": q.check_in.isoformat(), "check_out": q.check_out.isoformat(), "adults": q.guests}
    if q.dog:
        link["pets"] = 1
    return f"{url}?{urlencode(link)}"
