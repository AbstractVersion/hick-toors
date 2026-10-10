from datetime import date

import pytest

from app.cache import DiskCache
from app.config import Settings
from app.engine import Engine
from app.models import BBox, Hike, HikeQuery, LatLon, Stay, StaySearchQuery, TripQuery
from app.routing import RouteTime
from app.sources.stays.base import ProviderUnavailable
from app.sources.wikiloc import WikilocLoginExpired, WikilocResult, WikilocUnavailable

FRI, SUN = date(2026, 10, 23), date(2026, 10, 25)


def hike(id, trailrank, lat=39.4, lon=23.0, activity=1, distance_km=None):
    # activity=1 (Hiking): the engine now applies the default activity filter itself.
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=lat, lon=lon, trailrank=trailrank,
                activity=activity, distance_km=distance_km)


def stay(id, source="airbnb", pets=True, garden=None, lat=39.41, lon=23.01, rating=4.5, price=200.0):
    return Stay(id=f"{source}:{id}", source=source, name=str(id), url="u", lat=lat, lon=lon,
                pets_allowed=pets, has_garden=garden, rating=rating, total_price=price)


class FakeWikiloc:
    def __init__(self, hikes=None, fail=False, logged_in=False, expired=False, total=None, has_more=False):
        self.hikes, self.fail, self.boxes, self.calls = hikes or [], fail, [], []
        self.logged_in, self.expired, self.modes = logged_in, expired, []
        self.total, self.has_more, self.starts = total, has_more, []

    async def search(self, bbox, query="", sort="popular", pages=None, filters=None, use_login=True,
                     start_page=0):
        self.boxes.append(bbox)
        self.calls.append({"sort": sort, "pages": pages})
        self.starts.append(start_page)
        self.modes.append({"filters": filters, "use_login": use_login})
        if self.expired and use_login and self.logged_in:
            raise WikilocLoginExpired()
        if self.fail:
            raise WikilocUnavailable("Hikes temporarily unavailable – Wikiloc is rate limiting, try again in a minute")
        return WikilocResult(list(self.hikes), self.total, pages or 0, self.has_more)


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



async def test_filtered_search_fetches_more_pages_and_passes_sort(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"sort": "top_rated"}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10}))
    assert wikiloc.calls == [{"sort": "popular", "pages": 20}, {"sort": "top_rated", "pages": 20},
                             {"sort": "popular", "pages": 30}]


async def test_hike_cache_is_shared_by_post_fetch_filters(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1, distance_km=8.0), hike(2, 90, lat=38.1, distance_km=20.0)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    a = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"distance_max_km": 10}))
    b = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"distance_min_km": 10}))
    assert len(wikiloc.calls) == 1  # same sort + page count -> same raw Wikiloc fetch
    assert [h.id for h in a.hikes] == ["wikiloc:1"] and [h.id for h in b.hikes] == ["wikiloc:2"]


async def test_hike_filters_leaving_nothing_give_notice(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 95}))
    assert result.hikes == [] and result.warnings == ["No hikes match your filters"]


# ---- v2: drawn area ----

def drawn(area, **kw):
    return HikeQuery(start=FRI, end=SUN, mode="drawn", area=area, **kw)


async def test_drawn_rectangle_searches_its_box_without_drive_limit(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.5, lon=22.6)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.5: 999}))
    rect = {"shape": "rectangle", "south": 38.3, "west": 22.3, "north": 38.7, "east": 22.9}
    result = await engine.search_hikes(drawn(rect))
    assert wikiloc.boxes == [BBox(south=38.3, west=22.3, north=38.7, east=22.9)]
    assert [h.id for h in result.hikes] == ["wikiloc:1"] and result.drive_limit_minutes is None


async def test_drawn_circle_drops_hikes_outside_radius(settings):
    inside, corner = hike(1, 50, lat=39.03, lon=22.02), hike(2, 90, lat=39.08, lon=22.10)
    engine = make_engine(settings, wikiloc=FakeWikiloc([inside, corner]),
                         router=FakeRouter({39.03: 100, 39.08: 100}))
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000}))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_circle_crossing_greece_border_is_clamped_but_keeps_greek_hikes(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=41.75, lon=24.0)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({41.75: 200}))
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 41.7, "lon": 24.0, "radius_m": 30_000}))
    box = wikiloc.boxes[0]
    assert box.north == 41.8 and box.south == pytest.approx(41.7 - 30 / 111.32)
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_area_outside_greece_warns_without_fetching(settings):
    wikiloc = FakeWikiloc([hike(1, 50)])
    engine = make_engine(settings, wikiloc=wikiloc)
    rome = {"shape": "rectangle", "south": 41.0, "west": 12.0, "north": 42.0, "east": 13.0}
    result = await engine.search_hikes(drawn(rome))
    assert result.hikes == [] and result.warnings == ["The drawn area is outside Greece – draw it over Greece"]
    assert wikiloc.boxes == []


async def test_tiny_shapes_are_widened_to_five_km(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.52, lon=22.5)])  # ~2.2 km north of the centre
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.52: 100}))
    tiny_rect = {"shape": "rectangle", "south": 38.5, "west": 22.5, "north": 38.505, "east": 22.505}
    await engine.search_hikes(drawn(tiny_rect))
    box = wikiloc.boxes[0]
    assert box.north - box.south == pytest.approx(2 * 5 / 111.32)
    result = await engine.search_hikes(drawn({"shape": "circle", "lat": 38.5, "lon": 22.5, "radius_m": 300}))
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


# ---- v2: stay filters ----

async def test_unsupported_filter_skips_provider_with_notice(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", [stay(2, "booking")])
    engine = make_engine(settings, providers=[airbnb, booking])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"amenities": ["fireplace"]}))
    assert result.warnings == ["Booking.com skipped – it can't filter by Fireplace"]
    assert booking.queries == [] and airbnb.queries[0].filters.amenities == ["fireplace"]
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_every_provider_skipped_gives_no_source_notice(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", [stay(2, "booking")])
    engine = make_engine(settings, providers=[airbnb, booking])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"min_beds": 2, "breakfast": True}))
    assert result.stays == [] and airbnb.queries == [] and booking.queries == []
    assert result.warnings == ["Airbnb skipped – it can't filter by Breakfast included",
                               "Booking.com skipped – it can't filter by Beds",
                               "No source supports this combination of filters"]


async def test_provider_selection_runs_only_chosen_providers(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", [stay(2, "booking")])
    engine = make_engine(settings, providers=[airbnb, booking])
    base = dict(start=FRI, end=SUN, lat=39.4, lon=23.0)
    only_booking = await engine.search_stays(StaySearchQuery(**base, providers=["booking"]))
    assert [s.id for s in only_booking.stays] == ["booking:2"] and airbnb.queries == []
    only_airbnb = await engine.search_stays(StaySearchQuery(**base, providers=["airbnb"]))
    assert [s.id for s in only_airbnb.stays] == ["airbnb:1"] and len(booking.queries) == 1
    both = await engine.search_stays(StaySearchQuery(**base))
    assert {s.id for s in both.stays} == {"airbnb:1", "booking:2"}


async def test_selected_provider_failure_warns_and_skip_rules_still_apply(settings):
    airbnb, booking = FakeProvider("Airbnb", [stay(1)]), FakeProvider("Booking.com", fail=True)
    engine = make_engine(settings, providers=[airbnb, booking])
    base = dict(start=FRI, end=SUN, lat=39.4, lon=23.0)
    result = await engine.search_stays(StaySearchQuery(**base, providers=["booking"]))
    assert result.stays == [] and result.warnings == ["Booking.com unavailable – showing other sources"]
    skipped = await engine.search_stays(StaySearchQuery(**base, providers=["booking"],
                                                        filters={"amenities": ["fireplace"]}))
    assert skipped.warnings == ["Booking.com skipped – it can't filter by Fireplace"]


def test_empty_providers_list_is_rejected():
    with pytest.raises(ValueError):
        StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0, providers=[])


async def test_price_post_check_is_per_night(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, price=200.0), stay(2, price=300.0)])  # 2 nights: 100 / 150
    engine = make_engine(settings, providers=[airbnb])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"price_max": 120}))
    assert [s.id for s in result.stays] == ["airbnb:1"] and result.warnings == []


async def test_stay_filters_leaving_nothing_give_notice(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, price=200.0)])
    engine = make_engine(settings, providers=[airbnb])
    result = await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0,
                                                       filters={"price_max": 50}))
    assert result.stays == [] and result.warnings == ["No stays match your filters"]


async def test_stay_cache_key_includes_filters(settings):
    airbnb = FakeProvider("Airbnb", [stay(1)])
    engine = make_engine(settings, providers=[airbnb])
    base = dict(start=FRI, end=SUN, lat=39.4, lon=23.0)
    for filters in ({"price_max": 120}, {"price_max": 200}, {"price_max": 120}):
        await engine.search_stays(StaySearchQuery(**base, filters=filters))
    assert [q.filters.price_max for q in airbnb.queries] == [120, 200]


async def test_plan_trip_with_area_and_filters(settings):
    inside, outside = hike(1, 50, lat=39.03, lon=22.02), hike(2, 99, lat=39.3, lon=22.4)
    wikiloc = FakeWikiloc([inside, outside])
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.031, lon=22.021, price=100)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb],
                         router=FakeRouter({39.03: 500, 39.3: 500}))
    result = await engine.plan_trip(TripQuery(
        start=FRI, end=SUN, area={"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000},
        hike_filters={"min_trailrank": 10}, stay_filters={"price_max": 120}))
    assert [c.hike.id for c in result.combos] == ["wikiloc:1"]  # 500 min drive ignored inside a shape
    assert wikiloc.boxes[0].north == pytest.approx(39.0 + 10 / 111.32)
    assert wikiloc.calls[0]["pages"] == 30 and airbnb.queries[0].filters.price_max == 120


async def test_plan_trip_drawn_mode_without_explicit_mode(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=39.03, lon=22.02)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[FakeProvider("Airbnb", [stay(1, lat=39.031, lon=22.021)])],
                         router=FakeRouter({39.03: 500}))
    result = await engine.plan_trip(TripQuery(
        start=FRI, end=SUN, mode="drawn", area={"shape": "rectangle", "south": 39.0, "west": 22.0,
                                                "north": 39.1, "east": 22.1}))
    assert [c.hike.id for c in result.combos] == ["wikiloc:1"]
    assert len(wikiloc.boxes) == 1 and wikiloc.boxes[0].south <= 39.0 <= wikiloc.boxes[0].north


async def test_plan_trip_place_mode_geocodes_and_ignores_drive_limit(settings):
    box = BBox(south=39.0, west=22.0, north=39.2, east=22.2)
    wikiloc = FakeWikiloc([hike(1, 50, lat=39.03, lon=22.02)])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[FakeProvider("Airbnb", [stay(1, lat=39.031, lon=22.021)])],
                         router=FakeRouter({39.03: 500}), geocoder=FakeGeocoder(box))
    result = await engine.plan_trip(TripQuery(start=FRI, end=SUN, mode="place", place="Zagori"))
    assert wikiloc.boxes[0] == box and [c.hike.id for c in result.combos] == ["wikiloc:1"]  # 500 min kept


async def test_plan_trip_place_not_found_warns(settings):
    engine = make_engine(settings, geocoder=FakeGeocoder(None))
    result = await engine.plan_trip(TripQuery(start=FRI, end=SUN, mode="place", place="Atlantis"))
    assert result.combos == [] and "Couldn't find 'Atlantis' in Greece" in result.warnings


# ---- v2: Wikiloc login mode ----

async def test_logged_in_search_sends_filters_with_normal_page_count(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10,
                                                                              "loop_only": True}))
    assert wikiloc.calls == [{"sort": "popular", "pages": 20}]
    assert wikiloc.modes[0]["use_login"] is True and wikiloc.modes[0]["filters"].loop_only is True
    assert [h.id for h in result.hikes] == ["wikiloc:1"] and result.warnings == []


async def test_anonymous_loop_only_is_ignored_with_notice(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)])
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"loop_only": True}))
    assert result.warnings == ["Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"]
    assert [h.id for h in result.hikes] == ["wikiloc:1"]
    assert wikiloc.modes == [{"filters": None, "use_login": False}] and wikiloc.calls[0]["pages"] == 20


async def test_expired_login_falls_back_to_local_filtering_with_warning(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True, expired=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10}))
    assert [m["use_login"] for m in wikiloc.modes] == [True, False]
    assert wikiloc.calls[1]["pages"] == 30  # anonymous fallback fetches the filtered page count
    assert result.warnings == [
        "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"]
    assert [h.id for h in result.hikes] == ["wikiloc:1"]


async def test_empty_area_with_login_does_not_claim_expiry(settings):
    # Server-filtered page empty, anonymous retry has routes but none match -> genuinely no match.
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True, expired=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 95}))
    assert result.warnings == ["No hikes match your filters"]


async def test_login_and_anonymous_results_are_cached_separately(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    q = lambda f: HikeQuery(start=FRI, end=SUN, filters=f)  # noqa: E731
    await engine.search_hikes(q({"activities": [1]}))
    await engine.search_hikes(q({"activities": [1, 43]}))  # different server params -> new fetch
    await engine.search_hikes(q({"activities": [1]}))  # cached
    assert len(wikiloc.calls) == 2


LOGIN_EXPIRED_TEXT = "Wikiloc login may have expired – using local filtering. Update WIKILOC_COOKIE in .env"


async def test_expired_login_with_loop_only_adds_loop_notice(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], logged_in=True, expired=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 10,
                                                                              "loop_only": True}))
    assert result.warnings == [LOGIN_EXPIRED_TEXT, "Circular-only needs a Wikiloc login (WIKILOC_COOKIE)"]


async def test_default_filters_dropping_everything_point_to_hike_filters(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1, activity=43)]),
                         router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.warnings == ["No hiking routes here – other activities can be enabled in Hike filters"]


async def test_drawn_area_trim_leaving_nothing_warns(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1, lon=23.0)]),
                         router=FakeRouter({38.1: 60}))
    area = {"shape": "circle", "lat": 38.0, "lon": 23.0, "radius_m": 6000}  # hike is ~11 km away
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, mode="drawn", area=area))
    assert result.hikes == []
    assert result.warnings == ["No hikes inside the drawn area – draw a bigger shape or change the hike filters"]


async def test_drive_limit_trim_leaving_nothing_warns(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 300}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, drive_limit=45))
    assert result.hikes == []
    assert result.warnings == ["No hikes within your drive limit – try more days or a bigger drive limit"]
