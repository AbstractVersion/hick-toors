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
from app.geo import (GREECE_BBOX, bbox_around, clamp_to_greece, ensure_min_size, haversine_km, point_in_rings,
                     segment_distance_km)
from app.models import (
    SIGHT_GROUPS, SIGHT_LABELS, BBox, CircleArea, DriveLimit, Hike, HikeFilters, HikeQuery, HikeSearch, LatLon,
    NatureSearch, Sight, Stay, StayLookup, StayLookupResult, StayQuery, StaySearch, StaySearchQuery, TripQuery,
    TripSearch,
)
from app.ranking import rank_combos
from app.sources.nature import (MAX_TILES, NatureLimitReached, Tile, cap_lines, default_meta, lines_centre,
                                merge_named, tiles_for)
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
NATURE_TRUNCATED = "Some nature sights were left out here (too many in this area)"
NATURE_LIMIT = "Nature sights limit reached for today – showing cached areas only"
NATURE_GREECE_ONLY = "Nature sights are available for Greece only"
NATURE_GREECE_PAD_DEG = 0.5  # border areas (islands, Prespa) still count
NATURE_TILE_DEADLINE_S = 60.0  # per tile, both Overpass endpoints included
NATURE_MAX_INFLIGHT = 32  # tile fetches queued or running; new uncached tiles beyond this are skipped
NEARBY_MAX_TILES = 48  # 🌿 Find nearby sights: distinct tiles per trip
NEARBY_SPREAD = "Trip is spread out – searched near the first places only"
NEARBY_DEADLINE_S = 120.0  # whole tile search of one Find nearby sights request
NEARBY_TIMEOUT = "Search timed out – showing what was found; try again to continue"
AREA_KINDS = {"lake", "protected_area", "forest"}  # outlines: a point inside counts as 0 km away
_LIMITED = object()  # a tile not fetched because the daily Overpass budget is used up


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
    g, pad = GREECE_BBOX, NATURE_GREECE_PAD_DEG
    if not (south < g.north + pad and north > g.south - pad and west < g.east + pad and east > g.west - pad):
        raise ValueError(NATURE_GREECE_ONLY)
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


def _merge_tiles(tiles: list[Tile], groups: list[str], keep) -> list[Sight]:
    """Sights of `groups` from all tiles for which keep(sight) holds, one per feature (pure: run in a thread)."""
    kinds = {k for g in groups for k in SIGHT_GROUPS[g]}
    merged: dict[str, Sight] = {}
    meta: dict[str, dict] = {}
    for tile in tiles:
        for s in tile.sights:
            if s.kind not in kinds:
                continue
            m = tile.meta.get(s.id) or default_meta(s)
            if s.id in merged:  # same element clipped into several tiles: join its lines
                first, lines = merged[s.id], cap_lines(merged[s.id].lines + s.lines)
                centre = lines_centre(lines)
                update: dict = {"lines": lines}
                if centre:
                    update.update(lat=centre[0], lon=centre[1])
                merged[s.id] = first.model_copy(update=update)
                meta[s.id] = {"wd": meta[s.id]["wd"] or m["wd"], "name": meta[s.id]["name"],
                              "en": meta[s.id]["en"] or m["en"]}
            else:
                merged[s.id], meta[s.id] = s, m
    # A river split over tiles comes back as several sights of one feature: join them like within a tile.
    joined, _ = merge_named(list(merged.values()), meta)
    return [s for s in joined if keep(s)]


def sight_distance_km(s: Sight, points: list[LatLon], radius_km: float) -> float:
    """Distance from the sight (its position or the nearest point of its lines) to the nearest point.

    An area (lake, protected area, forest) counts as 0 km from a point inside its outline. Points whose radius
    box can't reach the sight's extent are skipped; inf when none is within reach.
    """
    if s.kind in AREA_KINDS and s.lines and any(point_in_rings(p, s.lines) for p in points):
        return 0.0
    lats = [s.lat] + [q[0] for line in s.lines for q in line]
    lons = [s.lon] + [q[1] for line in s.lines for q in line]
    s0, s1, w0, w1 = min(lats), max(lats), min(lons), max(lons)
    best = float("inf")
    for p in points:
        box = bbox_around(p, radius_km)
        if box.south > s1 or box.north < s0 or box.west > w1 or box.east < w0:
            continue
        best = min(best, haversine_km(p, LatLon(lat=s.lat, lon=s.lon)))
        for line in s.lines:
            if len(line) == 1:
                best = min(best, haversine_km(p, LatLon(lat=line[0][0], lon=line[0][1])))
            for a, b in zip(line, line[1:]):
                best = min(best, segment_distance_km(p, LatLon(lat=a[0], lon=a[1]), LatLon(lat=b[0], lon=b[1])))
    return best


def _nearby(tiles: list[Tile], points: list[LatLon], radius_km: float) -> list[Sight]:
    """Named sights of the tiles within radius_km of a point, nearest first (pure: run in a thread)."""
    merged = _merge_tiles(tiles, list(SIGHT_GROUPS), lambda s: True)
    # Named only: the nature source names an unnamed feature after its kind ("Waterfall", "Peak"), so a name
    # equal to the kind label means "no name in OSM". (A feature really named just "Peak" is dropped too.)
    named = [s for s in merged if s.name != SIGHT_LABELS.get(s.kind)]
    scored = [(sight_distance_km(s, points, radius_km), s) for s in named]
    return [s for d, s in sorted((ds for ds in scored if ds[0] <= radius_km), key=lambda ds: ds[0])]


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
        self._nature_sem: tuple | None = None  # (loop, Semaphore) shared by all nature requests
        self._nature_inflight: dict = {}  # tile cache key -> task fetching it

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

    def _nature_semaphore(self) -> asyncio.Semaphore:
        """One semaphore (2 Overpass queries at a time) shared by all requests on the running loop."""
        loop = asyncio.get_running_loop()
        if self._nature_sem is None or self._nature_sem[0] is not loop:
            self._nature_sem = (loop, asyncio.Semaphore(2))
            self._nature_inflight = {}
        return self._nature_sem[1]

    async def _fetch_nature_tile(self, key: str, south: float, west: float):
        async with self._nature_semaphore():
            try:  # one deadline for the whole tile, both endpoints included
                tile = await asyncio.wait_for(self.nature_source.fetch_tile(south, west), NATURE_TILE_DEADLINE_S)
            except NatureLimitReached:
                return _LIMITED
            except Exception as exc:  # NatureUnavailable, the deadline, anything unexpected
                print(f"nature tile {key} failed: {exc!r}", file=sys.stderr)
                return None
        # A truncated tile is cached like any other: the tile grid is fixed, so zooming in or refetching
        # asks Overpass the same query and hits the same cap.
        try:
            await asyncio.to_thread(lambda: self.cache.set(key, tile.to_cache(), 7 * DAY))
        except OSError as exc:
            print(f"nature tile {key} not cached: {exc!r}", file=sys.stderr)
        return tile

    async def _nature_tile(self, south: float, west: float):
        """The tile (cached or fetched), None when unavailable, or _LIMITED (daily budget used up)."""
        key = f"nature:v1:{south:.2f}_{west:.2f}"
        # old-format / corrupt entries count as a miss
        cached = await asyncio.to_thread(lambda: Tile.from_cache(self.cache.get(key)))
        if cached is not None:
            return cached
        if self.nature_source is None:
            return None
        self._nature_semaphore()  # (re)binds the shared state to this loop
        budget = getattr(self.nature_source, "budget", None)
        if key not in self._nature_inflight and budget is not None and await asyncio.to_thread(budget.exhausted):
            return _LIMITED  # cache only for the rest of the UTC day
        # No await from here until the task is registered, so the queue cap holds across requests.
        task = self._nature_inflight.get(key)
        if task is None:  # concurrent requests for the same tile share one Overpass query
            if len(self._nature_inflight) >= NATURE_MAX_INFLIGHT:
                return None  # queue full: don't pile up more Overpass queries
            task = asyncio.ensure_future(self._fetch_nature_tile(key, south, west))
            self._nature_inflight[key] = task
            task.add_done_callback(
                lambda done: self._nature_inflight.pop(key) if self._nature_inflight.get(key) is done else None)
        return await asyncio.shield(task)  # one cancelled request must not cancel the others' query

    async def nature(self, bbox: BBox, groups: list[str]) -> NatureSearch:
        """Natural sights in bbox (any of `groups`), one cached Overpass query per 0.25° tile.

        Raises ValueError when the box needs more than nature.MAX_TILES tiles (the API checks first).
        A failed tile is not cached; it adds one warning and the other tiles still count.
        """
        tiles, warnings = await self._nature_tiles(tiles_for(bbox))
        # big rivers: keep the loop free
        sights = await asyncio.to_thread(_merge_tiles, tiles, groups, lambda s: _sight_in_box(s, bbox))
        return NatureSearch(sights=sights, warnings=warnings)

    async def _nature_tiles(self, corners: list[tuple[float, float]],
                            timeout: float | None = None) -> tuple[list[Tile], list[str]]:
        """The tiles at these south-west corners plus their warnings, MAX_TILES at a time (the queue cap).

        With a timeout, tiles not finished by then are left out (NEARBY_TIMEOUT warning); their Overpass
        queries are shielded, so they still finish and get cached for the next try.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        results, timed_out = [], False
        for i in range(0, len(corners), MAX_TILES):
            tasks = [asyncio.ensure_future(self._nature_tile(s, w)) for s, w in corners[i:i + MAX_TILES]]
            left = None if deadline is None else max(0.0, deadline - loop.time())
            _, pending = await asyncio.wait(tasks, timeout=left)
            results += [t.result() for t in tasks if t not in pending]
            if pending:
                for t in pending:
                    t.cancel()
                timed_out = True
                break
        tiles = [t for t in results if isinstance(t, Tile)]
        warnings = []
        if any(t is None for t in results):
            warnings.append(NATURE_UNAVAILABLE)
        if any(t is _LIMITED for t in results):
            warnings.append(NATURE_LIMIT)
        if any(t.truncated for t in tiles):
            warnings.append(NATURE_TRUNCATED)
        if timed_out:
            warnings.append(NEARBY_TIMEOUT)
        return tiles, warnings

    async def nearby_sights(self, points: list[LatLon], radius_km: float) -> NatureSearch:
        """Named sights (all groups) within radius_km of any of `points`, nearest first.

        points[0] is the anchor: when the points' boxes need more than NEARBY_MAX_TILES tiles, the points
        closest to it are searched until the cap (NEARBY_SPREAD warning). Points outside Greece are skipped.
        """
        g, pad = GREECE_BBOX, NATURE_GREECE_PAD_DEG
        boxes = [(p, bbox_around(p, radius_km)) for p in points]
        boxes = [(p, b) for p, b in boxes if b.south < g.north + pad and b.north > g.south - pad
                 and b.west < g.east + pad and b.east > g.west - pad]
        if not boxes:
            return NatureSearch(warnings=[NATURE_GREECE_ONLY])
        anchor = points[0]
        boxes.sort(key=lambda pb: haversine_km(anchor, pb[0]))
        corners: list[tuple[float, float]] = []
        searched: list[LatLon] = []
        spread = False
        for p, box in boxes:
            new = [t for t in tiles_for(box) if t not in corners]
            if len(corners) + len(new) > NEARBY_MAX_TILES:
                spread = True
                break
            corners += new
            searched.append(p)
        tiles, warnings = await self._nature_tiles(corners, timeout=NEARBY_DEADLINE_S)
        sights = await asyncio.to_thread(_nearby, tiles, searched, radius_km)
        return NatureSearch(sights=sights, warnings=warnings + ([NEARBY_SPREAD] if spread else []))

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
