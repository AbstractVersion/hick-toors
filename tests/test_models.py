from datetime import date

import pytest
from pydantic import ValidationError

from app.models import (
    CircleArea, HikeFilters, HikeQuery, RectangleArea, StayFilters, StayQuery, StaySearchQuery, TripQuery,
)


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="end date must be on or after start date"):
        TripQuery(start=date(2026, 10, 25), end=date(2026, 10, 23))


def test_same_day_is_allowed():
    q = TripQuery(start=date(2026, 10, 23), end=date(2026, 10, 23))
    assert q.drive_limit == "auto"


def test_place_mode_requires_place():
    with pytest.raises(ValidationError, match="place is required"):
        HikeQuery(start=date(2026, 10, 23), end=date(2026, 10, 25), mode="place")


def test_drive_limit_accepts_minutes_and_keywords():
    base = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))
    assert HikeQuery(**base, drive_limit=120).drive_limit == 120
    assert HikeQuery(**base, drive_limit="unlimited").drive_limit == "unlimited"
    with pytest.raises(ValidationError):
        HikeQuery(**base, drive_limit=0)


def test_stay_search_needs_hike_or_coordinates():
    base = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))
    with pytest.raises(ValidationError, match="hike_id or lat/lon"):
        StaySearchQuery(**base)
    assert StaySearchQuery(**base, lat=39.4, lon=23.0).dog is True
    assert StaySearchQuery(**base, hike_id="wikiloc:1").hike_id == "wikiloc:1"


BASE = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))


def test_filter_defaults_reproduce_v1():
    f = HikeFilters()
    assert f.activities == [1] and (f.difficulty_min, f.difficulty_max) == (1, 5)
    assert f.sort == "popular" and f.distance_max_km is None and f.min_trailrank is None
    s = StayFilters()
    assert s.price_max is None and s.amenities == [] and s.entire_place is False
    assert HikeQuery(**BASE).filters == HikeFilters()
    assert StaySearchQuery(**BASE, lat=39.4, lon=23.0).filters == StayFilters()
    t = TripQuery(**BASE)
    assert t.hike_filters == HikeFilters() and t.stay_filters == StayFilters() and t.area is None


@pytest.mark.parametrize("kwargs,message", [
    ({"difficulty_min": 4, "difficulty_max": 2}, "difficulty: minimum must not be greater than maximum"),
    ({"distance_min_km": 12, "distance_max_km": 5}, "distance: minimum"),
    ({"elevation_min_m": 900, "elevation_max_m": 300}, "elevation gain: minimum"),
])
def test_hike_filter_ranges_are_validated(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        HikeFilters(**kwargs)


def test_hike_filter_rejects_unknown_activity_empty_list_and_unknown_key():
    with pytest.raises(ValidationError):
        HikeFilters(activities=[41])
    with pytest.raises(ValidationError):
        HikeFilters(activities=[])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HikeFilters(min_rank=50)


def test_stay_filter_validation():
    with pytest.raises(ValidationError, match="price per night: minimum"):
        StayFilters(price_min=150, price_max=100)
    assert StayFilters(price_min=100, price_max=100).price_max == 100
    with pytest.raises(ValidationError):
        StayFilters(amenities=["sauna"])
    with pytest.raises(ValidationError):
        StayFilters(min_rating=6)


def test_filter_lists_are_deduplicated():
    assert StayFilters(amenities=["wifi", "pool", "wifi"]).amenities == ["wifi", "pool"]
    assert HikeFilters(activities=[1, 43, 1]).activities == [1, 43]


def test_drawn_mode_requires_area_and_parses_shapes():
    with pytest.raises(ValidationError, match="Draw a rectangle or circle first"):
        HikeQuery(**BASE, mode="drawn")
    q = HikeQuery(**BASE, mode="drawn", area={"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000})
    assert isinstance(q.area, CircleArea) and q.area.radius_m == 8000
    q = HikeQuery(**BASE, mode="drawn",
                  area={"shape": "rectangle", "south": 38.4, "west": 22.4, "north": 38.6, "east": 22.8})
    assert isinstance(q.area, RectangleArea)


@pytest.mark.parametrize("area", [
    {"shape": "rectangle", "south": 38.6, "west": 22.4, "north": 38.4, "east": 22.8},
    {"shape": "rectangle", "south": 38.4, "west": 22.8, "north": 38.6, "east": 22.4},
    {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 150},
    {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 300_001},
    {"shape": "polygon", "points": []},
])
def test_invalid_areas_are_rejected(area):
    with pytest.raises(ValidationError):
        HikeQuery(**BASE, mode="drawn", area=area)


def test_stay_query_carries_filters_into_its_json():
    q = StayQuery(lat=39.4, lon=23.0, radius_km=15, check_in=date(2026, 10, 23),
                  check_out=date(2026, 10, 25), guests=2, dog=True, filters={"price_max": 120})
    assert '"price_max":120' in q.model_dump_json()
