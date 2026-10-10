import asyncio
import json
import sys
from datetime import date
from math import isfinite

from app.cache import DAY, HOUR, DiskCache
from app.config import Settings
from app.dog_rule import apply_dog_rule, assign_badge, badge_tier
from app.filters import (
    apply_hike_filters, hike_filters_active, skip_notice, stay_filters_active, stay_post_check,
    unsupported_filters,
)
from app.geo import GREECE_BBOX, bbox_around, clamp_to_greece, ensure_min_size, haversine_km
from app.models import (
    SIGHT_GROUPS, BBox, CircleArea, DriveLimit, Hike, HikeFilters, HikeQuery, HikeSearch, LatLon, NatureSearch,
    Sight, Stay, StayLookup, StayLookupResult, StayQuery, StaySearch, StaySearchQuery, TripQuery, TripSearch,
)
from app.ranking import rank_combos
from app.sources.nature import cap_lines, merge_named, tiles_for
from app.sources.stays.airbnb import NO_ANSWER as AIRBNB_NO_ANSWER
from app.sources.stays.airbnb import AirbnbParseError, ListingNotFound, room_id_from_url
from app.sources.stays.base import ProviderUnavailable, StayProvider
from app.sources.wikiloc import WikilocLoginExpired, WikilocResult, WikilocUnavailable, server_filter_params
from app.trip_window import max_drive_minutes, stay_window

STRAIGHT_LINE_KM_PER_MIN = 1.5  # generous: nothing within the drive limit falls outside the box
STAY_RADIUS_SLACK = 1.5  # providers search a square; drop results far outside the circle
MIN_AREA_RADIUS_KM = 5.0  # tiny drawn shapes are widened to at least this radius
MIN_STAY_AREA_RADIUS_KM = 1.0  # provider radius bounds for a drawn-area stay search
MAX_STAY_AREA_RADIUS_KM = 100.0  # = StaySearchQuery.radius_km upper bound
NO_HIKES_MATCH = "No hikes match your filters"
NO_HIKES_DEFAULT = "No hiking routes here – other activities can be enabled in Hike filters"
NO_HIKES_IN_AREA = "No hikes inside the drawn area – draw a bigger shape or change the hike filters"
NO_HIKES_IN_DRIVE = "No hikes within your drive limit – try more days or a bigger drive limit"
NO_STAYS_MATCH = "No stays match your filters"
NO_STAYS_IN_AREA = "No stays inside the drawn area – draw a bigger shape or relax the stay filters"
NO_SOURCE = "No source supports this combination of filters"
OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"
LOOP_NEEDS_LOGIN = "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"
LOGIN_EXPIRED = "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"
BAD_AIRBNB_LINK = "Paste an Airbnb room link (airbnb.com/rooms/…)"
LISTING_NOT_FOUND = "That Airbnb listing wasn't found (removed or private)"
PRICE_NOT_FOUND = "Price not found for these dates – you can type it in"
NO_DATES = "No dates set – price not looked up"
NATURE_UNAVAILABLE = "Nature sights unavailable for part of the map – try again in a minute"
ZOOM_IN_FOR_NATURE = "Zoom in to see nature sights"


def _provider_key(provider) -> str:
    """'Booking.com' -> 'booking', 'Airbnb' -> 'airbnb' (the StaySearchQuery.providers values)."""
    return provider.name.lower().split(".")[0]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def area_search_box(area) -> BBox | None:
    """Bounding box Wikiloc is searched in for a drawn shape (None when it misses Greece)."""
    if isinstance(area, CircleArea):
        box = bbox_around(LatLon(lat=area.lat, lon=area.lon), area.radius_m / 1000)
    else:
        box = BBox(south=area.south, west=area.west, north=area.north, east=area.east)
    box = clamp_to_greece(box)
    if box.south >= box.north or box.west >= box.east:
        return None
    return ensure_min_size(box, MIN_AREA_RADIUS_KM)


def in_area(h: Hike, area) -> bool:
    """Circle: within the (min-size widened) radius of its centre. Rectangle: the box already trims."""
    if not isinstance(area, CircleArea):
        return True
    radius_km = max(area.radius_m / 1000, MIN_AREA_RADIUS_KM)
    return haversine_km(LatLon(lat=area.lat, lon=area.lon), LatLon(lat=h.lat, lon=h.lon)) <= radius_km


def area_center_radius(area) -> tuple[LatLon, float]:
    """Centre and provider search radius (km) for stays in a drawn shape: the circle's radius or the
    rectangle's half-diagonal, clamped to what a stay search accepts."""
    if isinstance(area, CircleArea):
        center, radius = LatLon(lat=area.lat, lon=area.lon), area.radius_m / 1000
    else:
        center = LatLon(lat=(area.south + area.north) / 2, lon=(area.west + area.east) / 2)
        radius = haversine_km(center, LatLon(lat=area.north, lon=area.east))
    return center, min(max(radius, MIN_STAY_AREA_RADIUS_KM), MAX_STAY_AREA_RADIUS_KM)


def area_bbox(area) -> BBox:
    """The exact box providers should search for a drawn shape (a circle's bounding box)."""
    if isinstance(area, CircleArea):
        return bbox_around(LatLon(lat=area.lat, lon=area.lon), area.radius_m / 1000)
    return BBox(south=area.south, west=area.west, north=area.north, east=area.east)


def stay_in_area(s: Stay, area, center: LatLon, radius_km: float) -> bool:
    """Rectangle: inside the drawn box. Circle: within the (clamped) radius of the centre."""
    if isinstance(area, CircleArea):
        return haversine_km(center, LatLon(lat=s.lat, lon=s.lon)) <= radius_km
    return area.south <= s.lat <= area.north and area.west <= s.lon <= area.east


def nature_groups(names: list[str] | None) -> list[str]:
    """Validated, de-duplicated group keys (all four when none given); ValueError "Unknown group: x"."""
    groups = _dedupe([n.strip() for n in names or [] if n and n.strip()])
    for g in groups:
        if g not in SIGHT_GROUPS:
            raise ValueError(f"Unknown group: {g}")
    return groups or list(SIGHT_GROUPS)


BAD_COORDS = "Latitude must be within -90..90 and longitude within -180..180"


def nature_box(south: float, west: float, north: float, east: float) -> BBox:
    """The /api/nature box, or ValueError with the message the user sees."""
    if not (all(isfinite(v) for v in (south, west, north, east))
            and -90 <= south <= 90 and -90 <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError(BAD_COORDS)
    if south >= north or west >= east:
        raise ValueError("south must be < north and west < east")
    box = BBox(south=south, west=west, north=north, east=east)
    try:
        tiles_for(box)
    except ValueError:
        raise ValueError(ZOOM_IN_FOR_NATURE)
    return box


def _sight_in_box(s: Sight, box: BBox) -> bool:
    def inside(lat, lon):
        return box.south <= lat <= box.north and box.west <= lon <= box.east

    return inside(s.lat, s.lon) or any(inside(p[0], p[1]) for line in s.lines for p in line)


class StayLookupFailed(Exception):
    """A pasted-link lookup that failed: HTTP status for the API and a message for the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def lookup_airbnb_link(engine: "Engine", q: StayLookup) -> StayLookupResult:
    """POST /api/stays/lookup and the lookup_airbnb MCP tool: link -> listing, errors -> StayLookupFailed."""
    room_id = room_id_from_url(q.url)
    if room_id is None:
        raise StayLookupFailed(422, BAD_AIRBNB_LINK)
    try:
        return await engine.lookup_airbnb(room_id, q.start, q.end, q.guests, q.dog)
    except ListingNotFound:
        raise StayLookupFailed(404, LISTING_NOT_FOUND)
    except (ProviderUnavailable, AirbnbParseError) as exc:
        print(f"airbnb lookup of {room_id} failed: {exc!r}", file=sys.stderr)
        raise StayLookupFailed(502, AIRBNB_NO_ANSWER)


class Engine:
    def __init__(self, settings: Settings, wikiloc, providers: list[StayProvider], router, geocoder,
                 cache: DiskCache, nature=None):
        self.settings = settings
        self.wikiloc = wikiloc
        self.providers = providers
        self.router = router
        self.geocoder = geocoder
        self.cache = cache
        self.nature_source = nature  # NatureSource (None: nature sights unavailable)

    @property
    def home(self) -> LatLon:
        return LatLon(lat=self.settings.home_lat, lon=self.settings.home_lon)

    def drive_limit_minutes(self, start: date, end: date, limit: DriveLimit) -> int | None:
        return max_drive_minutes(start, end, limit, self.settings.day_trip_max_minutes,
                                 self.settings.weekend_max_minutes)

    def _register(self, objs: list) -> None:
        for obj in objs:
            kind = "hike" if isinstance(obj, Hike) else "stay"
            self.cache.set(f"obj:{obj.id}", {"kind": kind, "data": obj.model_dump(mode="json")}, DAY)

    def lookup(self, obj_id: str) -> Hike | Stay | None:
        entry = self.cache.get(f"obj:{obj_id}")
        if not entry:
            return None
        return (Hike if entry["kind"] == "hike" else Stay)(**entry["data"])

    @property
    def wikiloc_login(self) -> bool:
        return bool(getattr(self.wikiloc, "logged_in", False))

    async def _wikiloc_cached(self, box, f: HikeFilters, pages: int, login: bool,
                              start_page: int = 0) -> WikilocResult:
        # v3 key: entries hold every activity plus the Wikiloc total / paging state (v2 entries were
        # bare lists, v1 hiking-only). Anonymous fetches ignore post-fetch filters, so they share one
        # raw fetch per sort + page count + start page; logged-in fetches are server-filtered, so
        # their key carries the server params.
        mode = "login:" + json.dumps(server_filter_params(f), sort_keys=True) if login else "anon"
        key = (f"hikes:v3:{mode}:{f.sort}:{pages}:{start_page}:"
               f"{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}")
        cached = self.cache.get(key)
        if cached is not None:
            return WikilocResult([Hike(**h) for h in cached["hikes"]], cached["total"], cached["pages_fetched"],
                                 cached["has_more"])
        result = await self.wikiloc.search(box, sort=f.sort, pages=pages, filters=f if login else None,
                                           use_login=login, start_page=start_page)
        if start_page > 0 and not result.hikes:
            return result  # Load more past the end (or a hiccup): never pin an empty page in the cache
        self.cache.set(key, {"hikes": [h.model_dump(mode="json") for h in result.hikes], "total": result.total,
                             "pages_fetched": result.pages_fetched, "has_more": result.has_more}, 7 * DAY)
        return result

    async def search_hikes(self, q: HikeQuery) -> HikeSearch:
        warnings: list[str] = []
        limit = None
        if q.mode == "place":
            try:
                box = await self.geocoder.geocode(q.place)
            except Exception as exc:
                print(f"geocoding failed: {exc}", file=sys.stderr)
                return HikeSearch(warnings=["Place search unavailable – try again later"])
            if box is None:
                return HikeSearch(warnings=[f"Couldn't find '{q.place}' in Greece"])
        elif q.mode == "drawn":
            box = area_search_box(q.area)
            if box is None:
                return HikeSearch(warnings=[OUTSIDE_GREECE])
        else:
            limit = self.drive_limit_minutes(q.start, q.end, q.drive_limit)
            box = GREECE_BBOX if limit is None else clamp_to_greece(
                bbox_around(self.home, limit * STRAIGHT_LINE_KM_PER_MIN))
        # Anonymous: Wikiloc can't filter, so fetch more and filter here. Logged in: the server
        # filters (the local filter below stays as an idempotent safety net).
        anon_pages = self.settings.hike_pages_filtered if hike_filters_active(q.filters) else self.settings.hike_pages
        login = self.wikiloc_login
        if q.filters.loop_only and not login:
            warnings.append(LOOP_NEEDS_LOGIN)
        try:
            try:
                result = await self._wikiloc_cached(box, q.filters, self.settings.hike_pages if login else anon_pages,
                                                    login, q.page_offset)
            except WikilocLoginExpired:
                result = await self._wikiloc_cached(box, q.filters, anon_pages, False, q.page_offset)
                if apply_hike_filters(result.hikes, q.filters):
                    warnings.append(LOGIN_EXPIRED)
                if q.filters.loop_only and LOOP_NEEDS_LOGIN not in warnings:
                    warnings.append(LOOP_NEEDS_LOGIN)  # the anonymous retry cannot filter by loop
        except WikilocUnavailable as exc:
            # A failed Load more keeps its button: the same page offset can be retried.
            return HikeSearch(warnings=warnings + [str(exc)], drive_limit_minutes=limit,
                              next_page_offset=q.page_offset or None)
        fetched = result.hikes
        total = result.total
        next_offset = q.page_offset + result.pages_fetched if result.has_more else None
        hikes = apply_hike_filters(fetched, q.filters)
        if fetched and not hikes:
            no_match = NO_HIKES_MATCH if hike_filters_active(q.filters) else NO_HIKES_DEFAULT
            return HikeSearch(warnings=warnings + [no_match], drive_limit_minutes=limit,
                              total_available=total, next_page_offset=next_offset)
        if q.mode == "drawn":
            before = len(hikes)
            hikes = [h for h in hikes if in_area(h, q.area)]
            if before and not hikes:
                warnings.append(NO_HIKES_IN_AREA)
        times, warning = await self.router.drive_times(self.home, [LatLon(lat=h.lat, lon=h.lon) for h in hikes])
        if warning:
            warnings.append(warning)
        kept = []
        for h, t in zip(hikes, times):
            h = h.model_copy(update={"drive_minutes": t.minutes, "drive_estimated": t.estimated,
                                     "ferry_needed": not t.reachable})
            if q.mode == "near_home" and limit is not None:
                if not t.reachable or t.minutes is None or t.minutes > limit:
                    continue
            kept.append(h)
        if hikes and not kept and q.mode == "near_home" and limit is not None:
            warnings.append(NO_HIKES_IN_DRIVE)
        kept.sort(key=lambda h: h.trailrank or 0, reverse=True)
        kept = kept[: self.settings.max_hikes]
        self._register(kept)
        return HikeSearch(hikes=kept, warnings=warnings, drive_limit_minutes=limit, total_available=total,
                          next_page_offset=next_offset)

    async def _provider_search(self, provider: StayProvider, sq: StayQuery) -> tuple[list[Stay], str | None]:
        key = f"stays:{provider.name}:{sq.model_dump_json()}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Stay(**s) for s in cached], None
        try:
            stays = await provider.search(sq)
        except ProviderUnavailable as exc:
            return [], str(exc)
        except Exception as exc:
            print(f"{provider.name} failed: {exc}", file=sys.stderr)
            return [], f"{provider.name} unavailable – showing other sources"
        self.cache.set(key, [s.model_dump(mode="json") for s in stays], HOUR)
        return stays, None

    async def search_stays(self, q: StaySearchQuery) -> StaySearch:
        if q.hike_id:
            found = self.lookup(q.hike_id)
            if not isinstance(found, Hike):
                return StaySearch(warnings=["Unknown hike – please search hikes again"])
            center = LatLon(lat=found.lat, lon=found.lon)
        elif q.lat is not None and q.lon is not None:
            center = LatLon(lat=q.lat, lon=q.lon)
        else:
            center = None
        area = q.area if center is None else None  # precedence: hike_id > lat/lon > area
        if area is not None:
            center, radius = area_center_radius(area)
        else:
            radius = q.radius_km or self.settings.stay_radius_km
        check_in, check_out = stay_window(q.start, q.end)
        sq = StayQuery(lat=center.lat, lon=center.lon, radius_km=radius, check_in=check_in,
                       check_out=check_out, guests=q.guests, dog=q.dog, filters=q.filters,
                       bbox=area_bbox(area) if area is not None else None)
        warnings: list[str] = []
        runnable = []
        selected = [p for p in self.providers if q.providers is None or _provider_key(p) in q.providers]
        for p in selected:
            missing = unsupported_filters(p.name, q.filters, dog=q.dog)
            if missing:
                warnings.append(skip_notice(p.name, missing))
            else:
                runnable.append(p)
        if selected and not runnable and q.providers is None:
            return StaySearch(warnings=warnings + [NO_SOURCE], center=center)
        results = await asyncio.gather(*(self._provider_search(p, sq) for p in runnable))
        warnings += [w for _, w in results if w]
        stays = []
        for provider_stays, _ in results:
            for s in provider_stays:
                km = haversine_km(center, LatLon(lat=s.lat, lon=s.lon))
                # Area searches keep what lies inside the shape; otherwise drop results far outside the circle.
                if (stay_in_area(s, area, center, radius) if area is not None else km <= radius * STAY_RADIUS_SLACK):
                    stays.append(s.model_copy(update={"distance_km": round(km, 1)}))
        if area is not None and not stays and any(provider_stays for provider_stays, _ in results):
            warnings.append(NO_STAYS_IN_AREA)
        stays = stay_post_check(stays, q.filters, (check_out - check_in).days)
        stays = apply_dog_rule(stays, q.dog)
        stays.sort(key=lambda s: (badge_tier(s) if q.dog else 0, s.distance_km))
        if not stays and stay_filters_active(q.filters) and any(w is None for _, w in results):
            warnings.append(NO_STAYS_MATCH)
        self._register(stays)
        return StaySearch(stays=stays, warnings=_dedupe(warnings), center=center)

    async def lookup_airbnb(self, room_id: str, start: date | None, end: date | None, guests: int,
                            dog: bool) -> StayLookupResult:
        """One Airbnb listing by id, priced for the trip's dates when both are given.

        Raises ListingNotFound, ProviderUnavailable or AirbnbParseError (callers map them to messages).
        Unlike a search, the dog rule only badges the listing: the user picked it, so it is never dropped.
        """
        provider = next((p for p in self.providers if _provider_key(p) == "airbnb"), None)
        if provider is None:
            raise ProviderUnavailable(AIRBNB_NO_ANSWER)
        check_in, check_out = stay_window(start, end) if start and end else (None, None)
        key = f"airbnb-room:v1:{room_id}:{check_in}:{check_out}:{guests}:{int(dog)}"
        cached = self.cache.get(key)
        if cached is not None:
            stay = Stay(**cached)
        else:
            stay = await provider.lookup(room_id, check_in, check_out, guests, dog)
            stay.badge = assign_badge(stay, dog)
            # A missing price may be a search hiccup: retry it sooner than a full day later.
            ttl = HOUR if check_in and stay.total_price is None else DAY
            self.cache.set(key, stay.model_dump(mode="json"), ttl)
        self._register([stay])
        warnings = []
        if check_in is None:
            warnings.append(NO_DATES)
        elif stay.total_price is None:
            warnings.append(PRICE_NOT_FOUND)
        return StayLookupResult(stay=stay, warnings=warnings)

    async def nature(self, bbox: BBox, groups: list[str]) -> NatureSearch:
        """Natural sights in bbox (any of `groups`), one cached Overpass query per 0.25° tile.

        Raises ValueError when the box needs more than nature.MAX_TILES tiles (the API checks first).
        A failed tile is not cached; it adds one warning and the other tiles still count.
        """
        semaphore = asyncio.Semaphore(2)

        async def tile(south: float, west: float) -> list[Sight] | None:
            key = f"nature:v1:{south:.2f}_{west:.2f}"
            cached = self.cache.get(key)
            if cached is not None:
                return [Sight(**s) for s in cached]
            if self.nature_source is None:
                return None
            async with semaphore:
                try:
                    sights = await self.nature_source.fetch_tile(south, west)
                except Exception as exc:
                    print(f"nature tile {key} failed: {exc!r}", file=sys.stderr)
                    return None
            self.cache.set(key, [s.model_dump(mode="json") for s in sights], 7 * DAY)
            return sights

        results = await asyncio.gather(*(tile(s, w) for s, w in tiles_for(bbox)))
        kinds = {k for g in groups for k in SIGHT_GROUPS[g]}
        merged: dict[str, Sight] = {}
        for sights in results:
            for s in sights or []:
                if s.kind not in kinds:
                    continue
                if s.id in merged:  # same element clipped into several tiles: join its lines
                    first = merged[s.id]
                    merged[s.id] = first.model_copy(update={"lines": cap_lines(first.lines + s.lines)})
                else:
                    merged[s.id] = s
        # A river split over tiles comes back as several same-named sights: join them like within a tile.
        sights = [s for s in merge_named(list(merged.values())) if _sight_in_box(s, bbox)]
        warnings = [NATURE_UNAVAILABLE] if any(r is None for r in results) else []
        return NatureSearch(sights=sights, warnings=warnings)

    async def plan_trip(self, q: TripQuery) -> TripSearch:
        hikes = await self.search_hikes(HikeQuery(
            start=q.start, end=q.end, drive_limit=q.drive_limit, mode=q.mode, place=q.place,
            area=q.area if q.mode == "drawn" else None, filters=q.hike_filters))
        warnings = list(hikes.warnings)
        semaphore = asyncio.Semaphore(2)

        async def stays_for(h: Hike):
            async with semaphore:
                return h, await self.search_stays(StaySearchQuery(
                    start=q.start, end=q.end, hike_id=h.id, guests=q.guests, dog=q.dog, filters=q.stay_filters))

        results = await asyncio.gather(*(stays_for(h) for h in hikes.hikes[: self.settings.trip_top_hikes]))
        pairs = []
        for h, found in results:
            warnings.extend(found.warnings)
            pairs.extend((h, s) for s in found.stays[: self.settings.trip_stays_per_hike])
        combos = rank_combos(pairs, self.settings.weights, self.settings.stay_radius_km, q.dog)
        return TripSearch(combos=combos[: self.settings.trip_max_combos], warnings=_dedupe(warnings))
