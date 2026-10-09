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


async def test_invalid_input_returns_error_json(engine):
    out = json.loads(await tools.search_hikes("2026-10-25", "2026-10-23"))
    assert "end date must be on or after start date" in out["error"]
