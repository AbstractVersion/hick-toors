import json
from datetime import date

import pytest

import app.mcp_tools as tools
from app.models import Hike, HikeSearch, StaySearch, TripSearch


class FakeEngine:
    def __init__(self):
        self.queries = []

    async def search_hikes(self, q):
        self.queries.append(q)
        return HikeSearch(hikes=[Hike(id="wikiloc:1", name="h", url="u", lat=39.4, lon=23.0)])

    async def search_stays(self, q):
        self.queries.append(q)
        return StaySearch()

    async def plan_trip(self, q):
        self.queries.append(q)
        return TripSearch()


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(tools, "_engine", fake)
    return fake


async def test_search_hikes_near_home_with_limit(engine):
    out = json.loads(await tools.search_hikes("2026-10-23", "2026-10-25", max_drive_minutes=120))
    assert out["hikes"][0]["id"] == "wikiloc:1"
    q = engine.queries[0]
    assert (q.mode, q.drive_limit, q.start) == ("near_home", 120, date(2026, 10, 23))


async def test_search_hikes_place_and_unlimited(engine):
    await tools.search_hikes("2026-10-23", "2026-10-25", place="Zagori")
    await tools.search_hikes("2026-10-23", "2026-10-28", unlimited=True)
    assert (engine.queries[0].mode, engine.queries[0].place) == ("place", "Zagori")
    assert engine.queries[1].drive_limit == "unlimited"


async def test_search_stays_and_plan_trip(engine):
    await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1", dog=False)
    await tools.plan_trip("2026-10-23", "2026-10-25", guests=3)
    assert engine.queries[0].hike_id == "wikiloc:1" and engine.queries[0].dog is False
    assert engine.queries[1].guests == 3


async def test_search_stays_takes_a_drawn_area(engine):
    area = {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000}
    await tools.search_stays("2026-10-23", "2026-10-25", area=area)
    q = engine.queries[0]
    assert q.area.shape == "circle" and q.area.radius_m == 8000 and q.hike_id is None


async def test_invalid_area_returns_error_json(engine):
    out = json.loads(await tools.search_stays("2026-10-23", "2026-10-25",
                                              area={"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 10}))
    assert "area" in out["error"]
    out = json.loads(await tools.search_stays("2026-10-23", "2026-10-25"))
    assert "hike_id or lat/lon" in out["error"]
    assert engine.queries == []


async def test_invalid_input_returns_error_json(engine):
    out = json.loads(await tools.search_hikes("2026-10-25", "2026-10-23"))
    assert "end date must be on or after start date" in out["error"]


async def test_filter_dicts_are_passed_through(engine):
    await tools.search_hikes("2026-10-23", "2026-10-25", hike_filters={"min_trailrank": 60})
    await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1",
                             stay_filters={"price_max": 120, "amenities": ["parking"]})
    await tools.plan_trip("2026-10-23", "2026-10-25", hike_filters={"sort": "top_rated"},
                          stay_filters={"entire_place": True})
    assert engine.queries[0].filters.min_trailrank == 60
    assert engine.queries[1].filters.price_max == 120
    assert engine.queries[2].hike_filters.sort == "top_rated" and engine.queries[2].stay_filters.entire_place


async def test_invalid_filters_return_error_json(engine):
    out = json.loads(await tools.search_stays("2026-10-23", "2026-10-25", hike_id="wikiloc:1",
                                              stay_filters={"price_min": 200, "price_max": 100}))
    assert "price per night: minimum must not be greater than maximum" in out["error"]
    out = json.loads(await tools.search_hikes("2026-10-23", "2026-10-25", hike_filters={"min_rank": 5}))
    assert out["error"] == "filters.min_rank: Extra inputs are not permitted"
    assert engine.queries == []
