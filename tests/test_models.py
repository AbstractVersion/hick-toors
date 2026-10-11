from datetime import date

import pytest
from pydantic import ValidationError

from app.models import (
    CircleArea, Hike, HikeFilters, HikeQuery, RectangleArea, SavedTripIn, Stay, StayFilters, StayQuery,
    StaySearchQuery, TripItemAdd, TripQuery,
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


TRIP = dict(start=date(2026, 10, 23), end=date(2026, 10, 25))
RECT = {"shape": "rectangle", "south": 38.3, "west": 22.3, "north": 38.7, "east": 22.9}


def test_trip_query_modes():
    assert TripQuery(**TRIP).mode == "near_home"
    assert TripQuery(**TRIP, mode="place", place="Zagori").place == "Zagori"
    assert TripQuery(**TRIP, mode="drawn", area=RECT).mode == "drawn"
    with pytest.raises(ValidationError, match="place is required"):
        TripQuery(**TRIP, mode="place")
    with pytest.raises(ValidationError, match="place is required"):
        TripQuery(**TRIP, mode="place", place="  ")
    with pytest.raises(ValidationError, match="Draw a rectangle or circle first"):
        TripQuery(**TRIP, mode="drawn")
    with pytest.raises(ValidationError):
        TripQuery(**TRIP, mode="trip")


def test_trip_query_area_without_mode_means_drawn():
    assert TripQuery(**TRIP, area=RECT).mode == "drawn"


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


def test_stay_search_accepts_a_drawn_area_as_the_only_centre():
    area = {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 5000}
    q = StaySearchQuery(**BASE, area=area)
    assert q.area.shape == "circle" and q.hike_id is None and q.lat is None
    rect = {"shape": "rectangle", "south": 38.4, "west": 22.4, "north": 38.6, "east": 22.8}
    assert StaySearchQuery(**BASE, area=rect).area.shape == "rectangle"
    with pytest.raises(ValidationError):
        StaySearchQuery(**BASE, area={"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 10})
    with pytest.raises(ValidationError, match="hike_id or lat/lon"):
        StaySearchQuery(**BASE)


def test_hike_query_page_offset_bounds():
    assert HikeQuery(**BASE).page_offset == 0
    assert HikeQuery(**BASE, page_offset=400).page_offset == 400
    for bad in (-1, 401):
        with pytest.raises(ValidationError):
            HikeQuery(**BASE, page_offset=bad)


def _hike(i="wikiloc:1"):
    return Hike(id=i, name="h", url="https://es.wikiloc.com/x", lat=39.0, lon=21.0)


def _stay(i="airbnb:1"):
    return Stay(id=i, source="airbnb", name="s", url="https://www.airbnb.com/rooms/1", lat=39.0, lon=21.0)


def test_saved_trip_in_defaults_and_strip():
    t = SavedTripIn(name="  Zagori  ")
    assert (t.name, t.start, t.end, t.guests, t.dog, t.notes, t.source, t.score) == (
        "Zagori", None, None, 2, True, "", "manual", None)
    assert t.hikes == [] and t.stays == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_saved_trip_in_rejects_bad_names(name):
    with pytest.raises(ValidationError):
        SavedTripIn(name=name)


def test_saved_trip_in_rejects_end_before_start():
    with pytest.raises(ValidationError, match="end must be on or after start"):
        SavedTripIn(name="t", start="2026-10-25", end="2026-10-23")


def test_saved_trip_in_limits():
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", notes="x" * 5001)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", guests=0)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", score=101)
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", hikes=[_hike(f"wikiloc:{i}") for i in range(51)])


def test_saved_trip_in_dedupes_items_keeping_first():
    first = _hike("wikiloc:1")
    dup = first.model_copy(update={"name": "dup"})
    t = SavedTripIn(name="t", hikes=[first, _hike("wikiloc:2"), dup], stays=[_stay(), _stay()])
    assert [h.id for h in t.hikes] == ["wikiloc:1", "wikiloc:2"]
    assert t.hikes[0].name == "h"
    assert len(t.stays) == 1


def test_trip_item_add_needs_exactly_one():
    assert TripItemAdd(hike=_hike()).stay is None
    with pytest.raises(ValidationError, match="Give exactly one of hike, stay or sight"):
        TripItemAdd()
    with pytest.raises(ValidationError, match="Give exactly one of hike, stay or sight"):
        TripItemAdd(hike=_hike(), stay=_stay())


@pytest.mark.parametrize("update, message", [
    ({"lat": float("nan")}, "hikes.0: lat/lon must be finite coordinates"),
    ({"lon": float("inf")}, "hikes.0: lat/lon must be finite coordinates"),
    ({"lat": 91.0}, "hikes.0: lat/lon must be finite coordinates"),
    ({"lon": -180.5}, "hikes.0: lat/lon must be finite coordinates"),
    ({"distance_km": float("nan")}, "hikes.0: distance_km must be a finite number"),
])
def test_saved_trip_in_rejects_non_finite_hike_numbers(update, message):
    bad = _hike().model_copy(update=update)  # Hike itself still accepts it (scrapers build Hikes)
    with pytest.raises(ValidationError, match=message):
        SavedTripIn(name="t", hikes=[bad])


@pytest.mark.parametrize("update, message", [
    ({"lat": float("-inf")}, "stays.1: lat/lon must be finite coordinates"),
    ({"total_price": float("inf")}, "stays.1: total_price must be a finite number"),
    ({"rating": float("nan")}, "stays.1: rating must be a finite number"),
    ({"distance_km": float("-inf")}, "stays.1: distance_km must be a finite number"),
])
def test_saved_trip_in_rejects_non_finite_stay_numbers(update, message):
    bad = _stay("airbnb:2").model_copy(update=update)
    with pytest.raises(ValidationError, match=message):
        SavedTripIn(name="t", stays=[_stay(), bad])


def test_saved_trip_rejects_negative_stay_price():
    bad = _stay("airbnb:2").model_copy(update={"total_price": -5.0})
    with pytest.raises(ValidationError, match="stays.1: total_price must not be negative"):
        SavedTripIn(name="t", stays=[_stay(), bad])
    with pytest.raises(ValidationError, match="stay: total_price must not be negative"):
        TripItemAdd(stay=bad)
    assert SavedTripIn(name="t", stays=[_stay().model_copy(update={"total_price": 0.0})]).stays[0].total_price == 0
    assert Stay(id="s", source="airbnb", name="s", url="u", lat=0, lon=0, total_price=-1).total_price == -1


def test_hike_and_stay_themselves_still_accept_nan():
    assert Hike(id="h", name="h", url="u", lat=float("nan"), lon=0.0).lon == 0.0


def test_saved_trip_in_accepts_edge_coordinates():
    edge = _hike().model_copy(update={"lat": -90.0, "lon": 180.0})
    assert SavedTripIn(name="t", hikes=[edge]).hikes[0].lat == -90.0


def test_trip_item_add_rejects_non_finite_numbers():
    with pytest.raises(ValidationError, match="hike: lat/lon must be finite coordinates"):
        TripItemAdd(hike=_hike().model_copy(update={"lon": float("nan")}))
    with pytest.raises(ValidationError, match="stay: total_price must be a finite number"):
        TripItemAdd(stay=_stay().model_copy(update={"total_price": float("nan")}))


def test_saved_trip_in_rejects_documents_over_256_kb():
    big = [_hike(f"wikiloc:{i}").model_copy(update={"name": "x" * 6000}) for i in range(45)]
    with pytest.raises(ValidationError, match=r"Trip is too large \(max 256 KB\)"):
        SavedTripIn(name="t", hikes=big)
    assert len(SavedTripIn(name="t", hikes=big[:40]).hikes) == 40


def _sight(i=1, **kw):
    from app.models import Sight
    base = dict(id=f"osm:node/{i}", name="Falls", kind="waterfall", lat=38.0, lon=22.0,
                url=f"https://www.openstreetmap.org/node/{i}")
    return Sight(**{**base, **kw})


def test_saved_trip_sights_dedupe_and_drop_lines():
    t = SavedTripIn(name="t", sights=[_sight(1, lines=[[[1.0, 2.0], [3.0, 4.0]]]), _sight(1), _sight(2)])
    assert [s.id for s in t.sights] == ["osm:node/1", "osm:node/2"]
    assert all(s.lines == [] for s in t.sights)


def test_saved_trip_sights_limit_and_nan():
    with pytest.raises(ValidationError):
        SavedTripIn(name="t", sights=[_sight(i) for i in range(101)])
    with pytest.raises(ValidationError, match="finite"):
        SavedTripIn(name="t", sights=[_sight(1, lat=float("nan"))])


def test_old_trip_doc_without_sights_loads():
    from app.models import SavedTrip
    doc = ('{"name": "t", "id": "x", "created_at": "2026-10-10T12:00:00Z", "updated_at": "2026-10-10T12:00:00Z",'
           ' "hikes": [], "stays": []}')
    assert SavedTrip.model_validate_json(doc).sights == []


def test_trip_item_add_needs_exactly_one():
    msg = "Give exactly one of hike, stay or sight"
    with pytest.raises(ValidationError, match=msg):
        TripItemAdd()
    with pytest.raises(ValidationError, match=msg):
        TripItemAdd(sight=_sight(1), hike=Hike(id="w:1", name="n", url="u", lat=1, lon=1))
    assert TripItemAdd(sight=_sight(1)).sight.id == "osm:node/1"


def test_hike_route_is_optional_and_validated():
    base = {"id": "wikiloc:1", "name": "h", "url": "u", "lat": 39.0, "lon": 21.0}
    assert Hike(**base).route is None
    assert Hike(**base, route="_p~iF~ps|U_ulLnnqC_mqNvxq`@").route == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert Hike(**base, route="").route is None
    for bad in ("not a polyline", "_p~iF~ps|U_", "a" * 9000, "_p~iF\n~ps|U"):
        with pytest.raises(ValidationError):
            Hike(**base, route=bad)
