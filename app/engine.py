import asyncio
import sys
from datetime import date

from app.cache import DAY, HOUR, DiskCache
from app.config import Settings
from app.dog_rule import apply_dog_rule, badge_tier
from app.geo import GREECE_BBOX, bbox_around, clamp_to_greece, haversine_km
from app.models import (
    DriveLimit, Hike, HikeQuery, HikeSearch, LatLon, Stay, StayQuery, StaySearch,
    StaySearchQuery, TripQuery, TripSearch,
)
from app.ranking import rank_combos
from app.sources.stays.base import ProviderUnavailable, StayProvider
from app.sources.wikiloc import WikilocUnavailable
from app.trip_window import max_drive_minutes, stay_window

STRAIGHT_LINE_KM_PER_MIN = 1.5  # generous: nothing within the drive limit falls outside the box
STAY_RADIUS_SLACK = 1.5  # providers search a square; drop results far outside the circle


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


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

    async def _wikiloc_cached(self, box) -> list[Hike]:
        key = f"hikes:{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Hike(**h) for h in cached]
        hikes = await self.wikiloc.search(box)
        self.cache.set(key, [h.model_dump(mode="json") for h in hikes], 7 * DAY)
        return hikes

    async def search_hikes(self, q: HikeQuery) -> HikeSearch:
        warnings: list[str] = []
        if q.mode == "place":
            try:
                box = await self.geocoder.geocode(q.place)
            except Exception as exc:
                print(f"geocoding failed: {exc}", file=sys.stderr)
                return HikeSearch(warnings=["Place search unavailable – try again later"])
            if box is None:
                return HikeSearch(warnings=[f"Couldn't find '{q.place}' in Greece"])
            limit = None
        else:
            limit = self.drive_limit_minutes(q.start, q.end, q.drive_limit)
            box = GREECE_BBOX if limit is None else clamp_to_greece(
                bbox_around(self.home, limit * STRAIGHT_LINE_KM_PER_MIN))
        try:
            hikes = await self._wikiloc_cached(box)
        except WikilocUnavailable as exc:
            return HikeSearch(warnings=[str(exc)], drive_limit_minutes=limit)
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
                       check_out=check_out, guests=q.guests, dog=q.dog)
        results = await asyncio.gather(*(self._provider_search(p, sq) for p in self.providers))
        warnings = [w for _, w in results if w]
        stays = []
        for provider_stays, _ in results:
            for s in provider_stays:
                km = haversine_km(center, LatLon(lat=s.lat, lon=s.lon))
                if km <= radius * STAY_RADIUS_SLACK:
                    stays.append(s.model_copy(update={"distance_km": round(km, 1)}))
        stays = apply_dog_rule(stays, q.dog)
        stays.sort(key=lambda s: (badge_tier(s) if q.dog else 0, s.distance_km))
        self._register(stays)
        return StaySearch(stays=stays, warnings=_dedupe(warnings), center=center)

    async def plan_trip(self, q: TripQuery) -> TripSearch:
        hikes = await self.search_hikes(HikeQuery(start=q.start, end=q.end, drive_limit=q.drive_limit))
        warnings = list(hikes.warnings)
        semaphore = asyncio.Semaphore(2)

        async def stays_for(h: Hike):
            async with semaphore:
                return h, await self.search_stays(StaySearchQuery(
                    start=q.start, end=q.end, hike_id=h.id, guests=q.guests, dog=q.dog))

        results = await asyncio.gather(*(stays_for(h) for h in hikes.hikes[: self.settings.trip_top_hikes]))
        pairs = []
        for h, found in results:
            warnings.extend(found.warnings)
            pairs.extend((h, s) for s in found.stays[: self.settings.trip_stays_per_hike])
        combos = rank_combos(pairs, self.settings.weights, self.settings.stay_radius_km, q.dog)
        return TripSearch(combos=combos[: self.settings.trip_max_combos], warnings=_dedupe(warnings))
