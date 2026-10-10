import asyncio
import json
import sys
from datetime import date

from app.cache import DAY, HOUR, DiskCache
from app.config import Settings
from app.dog_rule import apply_dog_rule, badge_tier
from app.filters import (
    apply_hike_filters, hike_filters_active, skip_notice, stay_filters_active, stay_post_check,
    unsupported_filters,
)
from app.geo import GREECE_BBOX, bbox_around, clamp_to_greece, ensure_min_size, haversine_km
from app.models import (
    BBox, CircleArea, DriveLimit, Hike, HikeFilters, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
    StaySearchQuery, TripQuery, TripSearch,
)
from app.ranking import rank_combos
from app.sources.stays.base import ProviderUnavailable, StayProvider
from app.sources.wikiloc import WikilocLoginExpired, WikilocUnavailable, server_filter_params
from app.trip_window import max_drive_minutes, stay_window

STRAIGHT_LINE_KM_PER_MIN = 1.5  # generous: nothing within the drive limit falls outside the box
STAY_RADIUS_SLACK = 1.5  # providers search a square; drop results far outside the circle
MIN_AREA_RADIUS_KM = 5.0  # tiny drawn shapes are widened to at least this radius
NO_HIKES_MATCH = "No hikes match your filters"
NO_STAYS_MATCH = "No stays match your filters"
NO_SOURCE = "No source supports this combination of filters"
OUTSIDE_GREECE = "The drawn area is outside Greece – draw it over Greece"
LOOP_NEEDS_LOGIN = "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"
LOGIN_EXPIRED = "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"


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


class Engine:
    def __init__(self, settings: Settings, wikiloc, providers: list[StayProvider], router, geocoder,
                 cache: DiskCache):
        self.settings = settings
        self.wikiloc = wikiloc
        self.providers = providers
        self.router = router
        self.geocoder = geocoder
        self.cache = cache

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

    async def _wikiloc_cached(self, box, f: HikeFilters, pages: int, login: bool) -> list[Hike]:
        # v2 key: entries now hold every activity (v1 entries were hiking-only, without `activity`).
        # Anonymous fetches ignore post-fetch filters, so they share one raw fetch per sort + page
        # count; logged-in fetches are server-filtered, so their key carries the server params.
        mode = "login:" + json.dumps(server_filter_params(f), sort_keys=True) if login else "anon"
        key = f"hikes:v2:{mode}:{f.sort}:{pages}:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box, sort=f.sort, pages=pages, filters=f if login else None,
                                          use_login=login)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes

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
                fetched = await self._wikiloc_cached(box, q.filters, self.settings.hike_pages if login else anon_pages,
                                                     login)
            except WikilocLoginExpired:
                fetched = await self._wikiloc_cached(box, q.filters, anon_pages, False)
                if apply_hike_filters(fetched, q.filters):
                    warnings.append(LOGIN_EXPIRED)
                if q.filters.loop_only and LOOP_NEEDS_LOGIN not in warnings:
                    warnings.append(LOOP_NEEDS_LOGIN)  # the anonymous retry cannot filter by loop
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=warnings + [str(exc)], drive_limit_minutes=limit)
        hikes = apply_hike_filters(fetched, q.filters)
        if fetched and not hikes:
            return HikeSearch(warnings=warnings + [NO_HIKES_MATCH], drive_limit_minutes=limit)
        if q.mode == "drawn":
            hikes = [h for h in hikes if in_area(h, q.area)]
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
        kept.sort(key=lambda h: h.trailrank or 0, reverse=True)
        kept = kept[: self.settings.max_hikes]
        self._register(kept)
        return HikeSearch(hikes=kept, warnings=warnings, drive_limit_minutes=limit)

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
        else:
            center = LatLon(lat=q.lat, lon=q.lon)
        radius = q.radius_km or self.settings.stay_radius_km
        check_in, check_out = stay_window(q.start, q.end)
        sq = StayQuery(lat=center.lat, lon=center.lon, radius_km=radius, check_in=check_in,
                       check_out=check_out, guests=q.guests, dog=q.dog, filters=q.filters)
        warnings: list[str] = []
        runnable = []
        for p in self.providers:
            missing = unsupported_filters(p.name, q.filters, dog=q.dog)
            if missing:
                warnings.append(skip_notice(p.name, missing))
            else:
                runnable.append(p)
        if self.providers and not runnable:
            return StaySearch(warnings=warnings + [NO_SOURCE], center=center)
        results = await asyncio.gather(*(self._provider_search(p, sq) for p in runnable))
        warnings += [w for _, w in results if w]
        stays = []
        for provider_stays, _ in results:
            for s in provider_stays:
                km = haversine_km(center, LatLon(lat=s.lat, lon=s.lon))
                if km <= radius * STAY_RADIUS_SLACK:
                    stays.append(s.model_copy(update={"distance_km": round(km, 1)}))
        stays = stay_post_check(stays, q.filters, (check_out - check_in).days)
        stays = apply_dog_rule(stays, q.dog)
        stays.sort(key=lambda s: (badge_tier(s) if q.dog else 0, s.distance_km))
        if not stays and stay_filters_active(q.filters) and any(w is None for _, w in results):
            warnings.append(NO_STAYS_MATCH)
        self._register(stays)
        return StaySearch(stays=stays, warnings=_dedupe(warnings), center=center)

    async def plan_trip(self, q: TripQuery) -> TripSearch:
        hikes = await self.search_hikes(HikeQuery(
            start=q.start, end=q.end, drive_limit=q.drive_limit, mode="drawn" if q.area else "near_home",
            area=q.area, filters=q.hike_filters))
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
