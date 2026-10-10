"""Part A (stays inside a drawn area) and Part B (deeper Wikiloc fetching, Load more) of the engine."""

import pytest

from app.models import HikeQuery, LatLon, StaySearchQuery
from tests.test_engine import FRI, SUN, FakeProvider, FakeRouter, FakeWikiloc, hike, make_engine, stay  # noqa: F401
from tests.test_engine import settings  # noqa: F401  (fixture)

RECT = {"shape": "rectangle", "south": 39.0, "west": 22.8, "north": 39.2, "east": 23.2}
CIRCLE = {"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000}


# ---- Part A: stays inside the drawn area ----

def area_query(area, **kw):
    return StaySearchQuery(start=FRI, end=SUN, area=area, **kw)


async def test_rectangle_area_uses_centre_and_half_diagonal_and_trims_to_the_box(settings):
    inside, outside = stay(1, lat=39.05, lon=23.0), stay(2, lat=39.25, lon=23.0)  # 2 is north of the box
    airbnb = FakeProvider("Airbnb", [inside, outside])
    result = await make_engine(settings, providers=[airbnb]).search_stays(area_query(RECT))
    q = airbnb.queries[0]
    assert (q.lat, q.lon) == pytest.approx((39.1, 23.0))
    assert q.radius_km == pytest.approx(20.5, abs=0.2)  # centre -> corner of a 22 x 35 km box
    assert [s.id for s in result.stays] == ["airbnb:1"]
    assert (result.center.lat, result.center.lon) == pytest.approx((39.1, 23.0))
    assert result.stays[0].distance_km == pytest.approx(5.6, abs=0.2)  # measured from the centre
    assert result.warnings == []


async def test_in_box_stay_beyond_the_default_radius_slack_is_kept(settings):
    # Hike-centred searches drop stays beyond radius * 1.5; for areas the in-shape trim decides.
    wide = {"shape": "rectangle", "south": 38.5, "west": 22.0, "north": 39.5, "east": 24.0}
    far_corner = stay(1, lat=39.49, lon=23.98)
    airbnb = FakeProvider("Airbnb", [far_corner])
    result = await make_engine(settings, providers=[airbnb]).search_stays(area_query(wide))
    assert airbnb.queries[0].radius_km == 100  # half-diagonal (~120 km) clamped to the 100 km bound
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_circle_area_uses_its_radius_and_trims_to_the_circle(settings):
    inside, corner = stay(1, lat=39.03, lon=22.02), stay(2, lat=39.08, lon=22.10)  # 2 is ~11 km away
    airbnb = FakeProvider("Airbnb", [inside, corner])
    result = await make_engine(settings, providers=[airbnb]).search_stays(area_query(CIRCLE))
    q = airbnb.queries[0]
    assert (q.lat, q.lon, q.radius_km) == (39.0, 22.0, 10.0)
    assert [s.id for s in result.stays] == ["airbnb:1"]


async def test_tiny_and_huge_circles_are_clamped_for_the_providers(settings):
    airbnb = FakeProvider("Airbnb", [])
    engine = make_engine(settings, providers=[airbnb])
    await engine.search_stays(area_query({**CIRCLE, "radius_m": 300}))
    await engine.search_stays(area_query({**CIRCLE, "radius_m": 250_000}))
    assert [q.radius_km for q in airbnb.queries] == [1.0, 100.0]


async def test_trim_leaving_nothing_gives_notice(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.9, lon=22.0)])
    result = await make_engine(settings, providers=[airbnb]).search_stays(area_query(CIRCLE))
    assert result.stays == []
    assert result.warnings == ["No stays inside the drawn area – draw a bigger shape or relax the stay filters"]


async def test_no_provider_results_gives_no_area_notice(settings):
    result = await make_engine(settings, providers=[FakeProvider("Airbnb", [])]).search_stays(area_query(CIRCLE))
    assert result.stays == [] and result.warnings == []


async def test_area_search_keeps_dog_rule_provider_skipping_and_failures(settings):
    airbnb = FakeProvider("Airbnb", [stay(1, lat=39.01, lon=22.0), stay(2, pets=None, lat=39.02, lon=22.0)])
    booking = FakeProvider("Booking.com", fail=True)
    result = await make_engine(settings, providers=[airbnb, booking]).search_stays(area_query(CIRCLE))
    assert [s.id for s in result.stays] == ["airbnb:1"]
    assert result.warnings == ["Booking.com unavailable – showing other sources"]
    skipped = FakeProvider("Booking.com", [stay(3, "booking", lat=39.0, lon=22.0)])
    result = await make_engine(settings, providers=[airbnb, skipped]).search_stays(
        area_query(CIRCLE, filters={"amenities": ["fireplace"]}))
    assert skipped.queries == [] and result.warnings == ["Booking.com skipped – it can't filter by Fireplace"]


async def test_hike_id_beats_lat_lon_beats_area(settings):
    wikiloc = FakeWikiloc([hike(2, 90, lat=39.4, lon=23.0)])
    airbnb = FakeProvider("Airbnb", [])
    engine = make_engine(settings, wikiloc=wikiloc, providers=[airbnb], router=FakeRouter({39.4: 200}))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, hike_id="wikiloc:2", lat=1.0, lon=2.0, area=CIRCLE))
    await engine.search_stays(StaySearchQuery(start=FRI, end=SUN, lat=39.5, lon=23.1, area=CIRCLE))
    assert [(q.lat, q.lon) for q in airbnb.queries] == [(39.4, 23.0), (39.5, 23.1)]
    assert [q.radius_km for q in airbnb.queries] == [15.0, 15.0]  # no area radius when another centre wins


# ---- Part B: depth, total, Load more ----

async def test_hike_search_reports_total_and_next_page_offset(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], total=17042, has_more=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.total_available == 17042
    assert result.next_page_offset == 20  # page_offset 0 + 20 pages fetched
    assert wikiloc.starts == [0]


async def test_no_next_page_offset_when_data_ended_and_total_unknown(settings):
    engine = make_engine(settings, wikiloc=FakeWikiloc([hike(1, 50, lat=38.1)]), router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    assert result.total_available is None and result.next_page_offset is None


async def test_page_offset_is_passed_as_start_page_and_continues_the_offset(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], total=900, has_more=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, page_offset=20, filters={"min_trailrank": 10}))
    assert wikiloc.starts == [20] and wikiloc.calls[0]["pages"] == 30
    assert result.next_page_offset == 50


async def test_cache_key_includes_page_offset_and_keeps_total(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], total=900, has_more=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    first = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))
    await engine.search_hikes(HikeQuery(start=FRI, end=SUN, page_offset=20))
    again = await engine.search_hikes(HikeQuery(start=FRI, end=SUN))  # served from the cache
    again_more = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, page_offset=20))
    assert wikiloc.starts == [0, 20]
    assert (again.total_available, again.next_page_offset) == (first.total_available, first.next_page_offset) == (900, 20)
    assert again_more.next_page_offset == 40


async def test_total_and_next_offset_survive_a_filter_notice(settings):
    wikiloc = FakeWikiloc([hike(1, 50, lat=38.1)], total=900, has_more=True)
    engine = make_engine(settings, wikiloc=wikiloc, router=FakeRouter({38.1: 60}))
    result = await engine.search_hikes(HikeQuery(start=FRI, end=SUN, filters={"min_trailrank": 95}))
    assert result.warnings == ["No hikes match your filters"]
    assert result.total_available == 900 and result.next_page_offset == 30  # filtered: 30 pages


def test_defaults_are_deeper():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert (s.hike_pages, s.hike_pages_filtered, s.max_hikes, s.wikiloc_concurrency) == (20, 30, 500, 4)
