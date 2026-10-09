import pytest

from app.geo import GREECE_BBOX, bbox_around, center_of, clamp_to_greece, ensure_min_size, haversine_km
from app.models import BBox, LatLon

ATHENS = LatLon(lat=37.9838, lon=23.7275)
VOLOS = LatLon(lat=39.3622, lon=22.9420)


def test_haversine_athens_volos():
    assert haversine_km(ATHENS, VOLOS) == pytest.approx(170, abs=10)


def test_bbox_around_contains_radius():
    box = bbox_around(ATHENS, 50)
    assert box.south < ATHENS.lat < box.north
    assert box.west < ATHENS.lon < box.east
    assert haversine_km(ATHENS, LatLon(lat=box.north, lon=ATHENS.lon)) == pytest.approx(50, abs=1)
    assert haversine_km(ATHENS, LatLon(lat=ATHENS.lat, lon=box.east)) == pytest.approx(50, abs=1)


def test_clamp_to_greece():
    huge = bbox_around(ATHENS, 2000)
    assert clamp_to_greece(huge) == GREECE_BBOX


def test_ensure_min_size_expands_tiny_box():
    tiny = BBox(south=39.39, west=22.99, north=39.40, east=23.00)
    out = ensure_min_size(tiny, 15)
    c = center_of(tiny)
    assert haversine_km(c, LatLon(lat=out.north, lon=c.lon)) >= 14.5


def test_ensure_min_size_keeps_big_box():
    big = bbox_around(ATHENS, 100)
    assert ensure_min_size(big, 15) == big
