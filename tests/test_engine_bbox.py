import pytest

from app.models import StaySearchQuery
from tests.test_engine import FRI, SUN, FakeProvider, hike, make_engine, stay  # noqa: F401
from tests.test_engine import settings  # noqa: F401  (fixture)
from tests.test_engine import FakeWikiloc  # noqa: F401


async def test_rectangle_area_passes_its_exact_bbox(settings):
    airbnb = FakeProvider("Airbnb", [])
    rect = {"shape": "rectangle", "south": 39.0, "west": 22.8, "north": 39.2, "east": 23.2}
    await make_engine(settings, providers=[airbnb]).search_stays(StaySearchQuery(start=FRI, end=SUN, area=rect))
    b = airbnb.queries[0].bbox
    assert (b.south, b.west, b.north, b.east) == (39.0, 22.8, 39.2, 23.2)


async def test_circle_area_passes_its_bounding_box(settings):
    airbnb = FakeProvider("Airbnb", [])
    circle = {"shape": "circle", "lat": 39.0, "lon": 22.0, "radius_m": 10_000}
    await make_engine(settings, providers=[airbnb]).search_stays(StaySearchQuery(start=FRI, end=SUN, area=circle))
    b = airbnb.queries[0].bbox
    assert b.south == pytest.approx(38.91, abs=0.005) and b.north == pytest.approx(39.09, abs=0.005)
    assert b.west < 22.0 < b.east


async def test_latlon_search_has_no_bbox(settings):
    airbnb = FakeProvider("Airbnb", [])
    await make_engine(settings, providers=[airbnb]).search_stays(
        StaySearchQuery(start=FRI, end=SUN, lat=39.4, lon=23.0))
    assert airbnb.queries[0].bbox is None


def test_settings_defaults():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert (s.airbnb_pages, s.booking_pages) == (3, 2)
