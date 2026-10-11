import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

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

# Hotel links: booking.com/hotel/<country>/<page>[.<lang>].html (probed 2026-10-11). The hotel page itself is
# always challenged, so a link is looked up through the search page: by hotel id when the link carries one
# (app links have app_hotel_id), else by the page name as search text.
HOTEL_HOST_RE = re.compile(r"(?:www\.|m\.)?booking\.com")
HOTEL_PATH_RE = re.compile(r"/hotel/([a-z]{2})/([a-z0-9][a-z0-9-]{0,149}?)(?:\.[a-z]{2}(?:-[a-z]{2,4})?)?\.html")
HOTEL_ID_RE = re.compile(r"[0-9]{1,12}")  # ASCII only
PETS_BADGE_RE = re.compile(r"allows pets", re.I)
NO_ANSWER = "Booking.com didn't answer – try again in a minute"

FetchStatus = Callable[[str, dict], Awaitable[tuple[int, str]]]


class BookingBlocked(Exception):
    pass


class HotelNotFound(Exception):
    """The search answered but the linked property was not among the results."""


@dataclass(frozen=True)
class HotelRef:
    """A Booking.com hotel link reduced to what the lookup needs (nothing else of the link is used)."""

    page: str  # e.g. "aenaon-guesthouse"
    hotel_id: str | None = None  # e.g. "5546346"

    @property
    def key(self) -> str:
        return self.hotel_id or self.page


def hotel_ref_from_url(url: str) -> HotelRef | None:
    """The page name (and hotel id, when the link has app_hotel_id / hotel_id) of a booking.com hotel link;
    None for anything else."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not HOTEL_HOST_RE.fullmatch(parts.hostname or ""):
        return None
    match = HOTEL_PATH_RE.fullmatch(parts.path.lower())
    if not match:
        return None
    query = parse_qs(parts.query)
    ids = [v for k in ("app_hotel_id", "hotel_id") for v in query.get(k, []) if HOTEL_ID_RE.fullmatch(v)]
    return HotelRef(page=match.group(2), hotel_id=ids[0] if ids else None)


def _page_name(url: str) -> str | None:
    match = HOTEL_PATH_RE.fullmatch(urlsplit(url).path.lower())
    return match.group(2) if match else None


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
            # "This property allows pets!" (shown with the pets filter); no badge means unknown, not "no"
            pets_allowed=True if any(PETS_BADGE_RE.search(str(((b or {}).get("caption") or {}).get("translation")))
                                     for b in prop.get("badges") or []) else None,
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

    def __init__(self, fetch: FetchStatus | None = None, retries: int = 8,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, area_pages: int = 2,
                 rand: Callable[[float, float], float] = random.uniform):
        self._fetch = fetch or default_fetch
        self._retries = retries
        self._sleep = sleep
        self._area_pages = area_pages
        self._rand = rand

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
                # AWS WAF challenges ~70% of requests at random: retry with jittered exponential backoff.
                await self._sleep(min(1.0 * 2 ** attempt, 8) * self._rand(0.6, 1.2))
        return None

    async def lookup(self, ref: HotelRef, check_in: date | None, check_out: date | None, guests: int) -> Stay:
        """One property from its link, priced for the dates when both are given.

        By hotel id: one search with the pets filter (its results carry the "allows pets" badge), then one
        without. By name only: Booking's text search lists the property without a price, so once it is found
        the lookup continues by its id. Raises HotelNotFound or ProviderUnavailable.
        """
        params: dict = {"group_adults": guests, "no_rooms": 1, "group_children": 0, "lang": "en-gb",
                        "selected_currency": "EUR"}
        if check_in and check_out:
            params.update(checkin=check_in.isoformat(), checkout=check_out.isoformat())
        answered = False
        by_name = None
        if not ref.hotel_id:
            for extra in ({"nflt": PETS_FILTER}, {}):
                found, by_name = await self._find(ref, {**params, "ss": ref.page.replace("-", " "), **extra})
                answered |= found
                if by_name is not None:
                    break
            if by_name is None:
                if not answered:
                    raise ProviderUnavailable(NO_ANSWER)
                raise HotelNotFound(ref.key)
            ref = HotelRef(page=ref.page, hotel_id=by_name.id.removeprefix("booking:"))
        match = None
        for extra in ({"nflt": PETS_FILTER}, {}):
            found, match = await self._find(ref, {**params, "dest_id": ref.hotel_id, "dest_type": "hotel", **extra})
            answered |= found
            if match is not None:
                break
        match = match or by_name
        if match is None:
            if not answered:
                raise ProviderUnavailable(NO_ANSWER)
            raise HotelNotFound(ref.key)
        if check_in and check_out:
            match.url += "?" + urlencode({"checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
                                          "group_adults": guests})
        return match

    async def _find(self, ref: HotelRef, params: dict) -> tuple[bool, Stay | None]:
        """(answered, the linked property among one search page's results)."""
        stays = await self._page(params)
        if stays is None:
            return False, None
        return True, next((s for s in stays if s.id == f"booking:{ref.hotel_id}" or _page_name(s.url) == ref.page),
                          None)

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
        # Page 2+ only pays off when page 1 already reached into the box.
        useful = q.bbox is not None and any(
            q.bbox.south <= s.lat <= q.bbox.north and q.bbox.west <= s.lon <= q.bbox.east for s in stays)
        for _ in range(1, self._area_pages if useful else 1):
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
