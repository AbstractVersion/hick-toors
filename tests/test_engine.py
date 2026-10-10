from datetime import date

import pytest

from app.cache import DiskCache
from app.config import Settings
from app.engine import Engine
from app.models import Hike, HikeQuery, LatLon, Stay, StaySearchQuery, TripQuery
from app.routing import RouteTime
from app.sources.stays.base import ProviderUnavailable
from app.sources.wikiloc import WikilocUnavailable

FRI, SUN = date(2026, 10, 23), date(2026, 10, 25)


def hike(id, trailrank, lat=39.4, lon=23.0, activity=1, distance_km=None):
    # activity=1 (Hiking): the engine now applies the default activity filter itself.
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=lat, lon=lon, trailrank=trailrank,
                activity=activity, distance_km=distance_km)


def stay(id, source="airbnb", pets=True, garden=None, lat=39.41, lon=23.01, rating=4.5, price=200.0):
    return Stay(id=f"{source}:{id}", source=source, name=str(id), url="u", lat=lat, lon=lon,
                pets_allowed=pets, has_garden=garden, rating=rating, total_price=price)


class FakeWikiloc:
    def __init__(self, hikes=None, fail=False):
        self.hikes, self.fail, self.boxes = hikes or [], fail, []

    async def search(self, bbox, query=""):
        self.boxes.append(bbox)
        if self.fail:
            raise WikilocUnavailable("Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute")
        return list(self.hikes)


class FakeRouter:
    def __init__(self, minutes_by_lat):
        self.minutes_by_lat = minutes_by_lat

    async def drive_times(self, origin, dests):
        out = []
        for d in dests:
            m = self.minutes_by_lat[d.lat]
            out.append(RouteTime(minutes=None, estimated=False, reachable=False) if m is None
                       else RouteTime(minutes=m, estimated=False))
        return out, None


class FakeGeocoder:
    def __init__(self, box=None, fail=False):
        self.box, self.fail = box, fail

    async def geocode(self, place):
        if self.fail:
            raise ConnectionError("down")
        return self.box


class FakeProvider:
    def __init__(self, name, stays=None, fail=False):
        self.name, self.stays, self.fail, self.queries = name, stays or [], fail, []

    async def search(self, q):
        self.queries.append(q)
        if self.fail:
            raise ProviderUnavailable(f"{self.name} unavailable – showing other sources")
        return [s.model_copy() for s in self.stays]


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, cache_dir=tmp_path)


def make_engine(settings, wikiloc=None, providers=None, router=None, geocoder=None):
    return Engine(settings, wikiloc or FakeWikiloc(), providers or [], router or FakeRouter({}),
                  geocoder or FakeGeocoder(), DiskCache(settings.cache_dir))


async def test_near_home_weekend_filters_by_drive_limit_and_sorts(settings):
    hikes = [hike(1, 50, lat=38.1), hike(2, 90, lat=39.4), hike(3, 99, lat=40.1), hike(4, 70, lat=35.3)]
    router = FakeRouter({38.1: 60, 39.4: 200, 40.1: 300, 35.3: None})
    engine = make_engine(settings, wikiloc=FakeWikiloc(hikes), router=router)
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.drive_limit_minutes == 240
    assert [h.id for h in result.hikes] == ["wikiloc:2", "wikiloc:1"]
    assert result.hikes[0].drive_minutes == 200


async def test_unlimited_keeps_island_hikes_flagged_ferry(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(4, 70, lat=35.3)]),
                         router=FakeRouter({35.3: None}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=date(2026, 10, 28)))
    assert result.drive_limit_minutes is None
    assert result.hikes[0].ferry_needed is True and result.hikes[0].drive_minutes is None


async def test_drive_limit_override(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, drive_limit=45))
    assert result.hikes == [] and result.drive_limit_minutes == 45


async def test_place_mode_uses_geocoded_box_and_does_not_filter_by_drive(settings):
    from app.models import BBox
    box = BBox(south=39.3, west=22.9, north=39.5, east=23.2)
    wikiloc = FakeWikiloc([hike(2, 90, lat=39.4)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({39.4: 999}), geocoder=FakeGeocoder(box))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=FRI, mode="place", place="Pelion"))
    assert wikiloc.boxes == [box]
    assert [h.id for h in result.hikes] == ["wikiloc:2"]


async def test_place_not_found_and_geocoder_down(settings):
    q = HikeQuery(start=FRI, end=SUN, mode="place", place="Atlantis")
    result = await make_engine(settings, geocoder=FakeGeocoder(None)).search_hikes(q)
    assert result.warnings == ["Couldn't find 'Atlantis' in Greece"]
    result = await make_engine(settings, geocoder=FakeGeocoder(fail=True)).search_hikes(q)
    assert result.warnings == ["Place search unavailable – try again later"]


async def test_wikiloc_unavailable_becomes_warning(settings):
    result = await make_engine(settings, wikiloc=FakeWikiloc(fail=True)).search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.hikes == [] and "Hikes temporarily unavailable" in result.warnings[0]


async def test_search_stays_by_hike_merges_providers_and_reports_failures(settings):
    wikiloc = FakeWikiloc([hike(2, 90, lat=39.4)])
    airbnb = FakeProvider("Airbnb", [stay(1), stay(2, pets=None, garden=True), stay(3, pets=None)])
    booking = FakeProvider("Booking.com", fail=True)
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb, booking], router=FakeRouter({39.4: 200}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, hike_id="wikiloc:2"))
    assert [s.id for s in result.stays] == ["airbnb:1", "airbnb:2"]
    assert [s.badge for s in result.stays] == ["pets", "garden_unconfirmed"]
    assert result.stays[0].distance_km is not None
    assert result.warnings == ["Booking.com unavailable – showing other sources"]
    assert result.center == LatLon(lat=39.4, lon=23.0)
    q = airbnb.queries[0]
    assert (q.check_in, q.check_out, q.radius_km, q.dog) == (FRI, SUN, 15.0, True)


async def test_same_day_trip_books_one_night(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    await engine.search_stays(StaySearchQuery(start=FRI, end=FRI, lat=39.4, lon=23.0))
    assert airbnb.queries[0].check_out == date(2026, 10, 24)


async def test_unknown_hike_id(settings):
    result = await make_engine(settings).search_stays(StaySearchQuery(start=FRI, end=SUN, hike_id="wikiloc:404"))
    assert result.warnings == ["Unknown hike – please search hikes again"]


async def test_stays_far_outside_radius_are_dropped(settings):
    airbnb = FakeProvider("Airbnb", [stay(1), stay(9, lat=39.8)])  # ~44 km away
    result = await make_engine(settings, providers=[airbnb]).search_stays(
        StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0))
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_lookup_finds_registered_objects(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0))
    found = engine.lookup("airbnb:1")
    assert isinstance(found, Stay) and found.badge == "pets"
    assert engine.lookup("airbnb:999") is None


async def test_plan_trip_ranks_combos(settings):
    wikiloc = FakeWikiloc([hike(1, 95, lat=39.4), hike(2, 60, lat=39.45)])
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.41, rating=5.0, price=100)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb],
                         router=FakeRouter({39.4: 200, 39.45: 100}))
    result = await engine.plan_trip(TripQuery(start=FRI, end=SUN))
    assert len(result.combos) == 2
    assert result.combos[0].hike.id == "wikiloc:1"
    assert result.combos[0].score >= result.combos[1].score


# ---- v2: filters ----

async def test_default_filters_drop_non_hiking_routes(settings):
    hikes = [hike(1, 50, lat=38.1), hike(2, 90, lat=38.1, activity=41)]
    engine = make_engine(settings, wikiloc=FakeWikiloc(hikes), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_v1_hike_cache_entries_are_not_reused(settings):
    # v1 cached hiking-only routes without `activity`; reusing them would now filter them all out.
    from app.models import BBox
    box = BBox(south=39.3, west=22.9, north=39.5, east=23.2)
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(2, 90, lat=39.4)]), router=FakeRouter({39.4: 10}),
                         geocoder=FakeGeocoder(box))
    engine.cache.set("hikes:39.300,22.900,39.500,23.200",
                     [hike(7, 99, lat=39.4, activity=None).model_dump(mode="json")], 3600)
    result = await engine.search_hikes(HikeQuery(start=FRI, end=FRI, mode="place", place="Pelion"))
    assert [h.id for h in result.hikes] == ["wikiloc:2"]
