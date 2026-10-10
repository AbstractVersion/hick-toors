import pytest

from app.filters import (
    apply_hike_filters, catalogue, hike_filters_active, skip_notice, stay_filters_active, stay_post_check,
    unsupported_filters,
)
from app.models import Hike, HikeFilters, Stay, StayFilters


def hike(id, activity=1, skill=2, km=10.0, gain=500, rank=50):
    return Hike(id=f"wikiloc:{id}", name=str(id), url="u", lat=39.4, lon=23.0, activity=activity,
                skill=skill, distance_km=km, elevation_gain_m=gain, trailrank=rank)


def stay(id, price=200.0, rating=4.6):
    return Stay(id=f"airbnb:{id}", source="airbnb", name=str(id), url="u", lat=39.4, lon=23.0,
                total_price=price, rating=rating)


def ids(items):
    return [i.id.split(":")[1] for i in items]


def test_catalogue_shape_and_defaults():
    cat = catalogue()
    assert cat["hike"]["defaults"] == HikeFilters().model_dump()
    assert cat["stay"]["defaults"] == StayFilters().model_dump()
    hike_keys = [f["key"] for f in cat["hike"]["fields"]]
    assert hike_keys == ["activities", "difficulty", "distance", "elevation", "min_trailrank", "loop_only",
                         "sort"]
    fields = {f["key"]: f for f in cat["stay"]["fields"]}
    assert fields["price"]["min_key"] == "price_min" and fields["price"]["unit"] == "€/night"
    assert fields["min_beds"]["sources"] == "Airbnb only"
    assert fields["breakfast"]["sources"] == "Booking only"
    assert fields["entire_place"]["sources"] == "Airbnb + Booking"
    amenity = {o["value"]: o for o in fields["amenities"]["options"]}
    assert amenity["fireplace"] == {"value": "fireplace", "label": "Fireplace", "sources": "Airbnb only"}
    assert amenity["wifi"]["sources"] == "Airbnb + Booking"
    # Every key the UI can set must be a real model field, so popups and models can't drift.
    model_keys = set(HikeFilters.model_fields) | set(StayFilters.model_fields)
    for kind in ("hike", "stay"):
        for f in cat[kind]["fields"]:
            keys = [f["min_key"], f["max_key"]] if f["type"] == "range" else [f["key"]]
            assert set(keys) <= model_keys, f


def test_catalogue_marks_loop_only_by_login():
    anon = {f["key"]: f for f in catalogue()["hike"]["fields"]}["loop_only"]
    assert catalogue()["wikiloc_login"] is False
    assert anon["disabled"] is True and anon["sources"] == "Needs Wikiloc login"
    cat = catalogue(wikiloc_login=True)
    logged = {f["key"]: f for f in cat["hike"]["fields"]}["loop_only"]
    assert cat["wikiloc_login"] is True and logged["disabled"] is False and logged["sources"] is None


def test_hike_filters_active_ignores_sort():
    assert hike_filters_active(HikeFilters()) is False
    assert hike_filters_active(HikeFilters(sort="top_rated")) is False
    assert hike_filters_active(HikeFilters(loop_only=True)) is False  # server-side only
    assert hike_filters_active(HikeFilters(min_trailrank=10)) is True
    assert hike_filters_active(HikeFilters(activities=[1, 43])) is True


def test_stay_filters_active():
    assert stay_filters_active(StayFilters()) is False
    assert stay_filters_active(StayFilters(beachfront=True)) is True


def test_default_hike_filters_keep_only_hiking_routes():
    hikes = [hike(1), hike(2, activity=43), hike(3, activity=None), hike(4, skill=None, km=None, gain=None, rank=None)]
    assert ids(apply_hike_filters(hikes, HikeFilters())) == ["1", "4"]


@pytest.mark.parametrize("filters,expected", [
    (HikeFilters(activities=[43, 66]), ["2"]),
    (HikeFilters(difficulty_min=2, difficulty_max=3), ["1", "3"]),
    (HikeFilters(distance_min_km=8, distance_max_km=12), ["1"]),
    (HikeFilters(distance_max_km=12), ["1", "3"]),
    (HikeFilters(elevation_min_m=400), ["1", "4"]),
    (HikeFilters(min_trailrank=60), ["4"]),
])
def test_apply_hike_filters_each_field(filters, expected):
    hikes = [hike(1), hike(2, activity=43, skill=1, km=20, gain=100, rank=40),
             hike(3, skill=3, km=5, gain=300, rank=55), hike(4, skill=5, km=30, gain=1500, rank=90)]
    assert ids(apply_hike_filters(hikes, filters)) == expected


def test_missing_value_is_dropped_only_when_that_field_is_filtered():
    bare = hike(9, skill=None, km=None, gain=None, rank=None)
    assert ids(apply_hike_filters([bare], HikeFilters(sort="top_rated"))) == ["9"]
    for f in (HikeFilters(difficulty_max=4), HikeFilters(distance_min_km=1),
              HikeFilters(elevation_max_m=2000), HikeFilters(min_trailrank=0)):
        assert apply_hike_filters([bare], f) == []


def test_stay_post_check_uses_price_per_night():
    stays = [stay(1, price=200.0), stay(2, price=300.0), stay(3, price=100.0), stay(4, price=None)]
    # 2 nights: 100/night, 150/night, 50/night, unknown (kept)
    assert ids(stay_post_check(stays, StayFilters(price_max=120), nights=2)) == ["1", "3", "4"]
    assert ids(stay_post_check(stays, StayFilters(price_min=60, price_max=120), nights=2)) == ["1", "4"]
    assert ids(stay_post_check(stays, StayFilters(price_max=120), nights=0)) == ["3", "4"]  # treated as 1 night


def test_stay_post_check_rating_keeps_unrated():
    stays = [stay(1, rating=4.9), stay(2, rating=4.2), stay(3, rating=None)]
    assert ids(stay_post_check(stays, StayFilters(min_rating=4.5), nights=1)) == ["1", "3"]
    assert ids(stay_post_check(stays, StayFilters(), nights=1)) == ["1", "2", "3"]


def test_unsupported_filters_per_provider():
    assert unsupported_filters("Airbnb", StayFilters()) == []
    assert unsupported_filters("Booking.com", StayFilters()) == []
    f = StayFilters(price_max=120, min_rating=4.5, entire_place=True, amenities=["parking"],
                    free_cancellation=True)
    assert unsupported_filters("Airbnb", f) == []
    assert unsupported_filters("Booking.com", f) == []
    f = StayFilters(amenities=["fireplace", "wifi"], min_beds=2, breakfast=True)
    assert unsupported_filters("Airbnb", f) == ["Breakfast included"]
    assert unsupported_filters("Booking.com", f) == ["Beds", "Fireplace"]
    assert unsupported_filters("Somewhere", StayFilters(beachfront=True)) == ["Beachfront"]
    assert unsupported_filters("Somewhere", StayFilters()) == []


def test_booking_cannot_require_two_values_of_one_facility_field():
    assert unsupported_filters("Booking.com", StayFilters(amenities=["parking"]), dog=False) == []
    assert unsupported_filters("Booking.com", StayFilters(amenities=["parking"]), dog=True) == [
        "Pets + Free parking together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["wifi", "pool"])) == [
        "Wifi + Pool together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["ac", "kitchen", "parking"])) == [
        "Air conditioning + Kitchen together"]
    assert unsupported_filters("Booking.com", StayFilters(amenities=["pool"], beachfront=True)) == [
        "Pool + Beachfront together"]
    assert unsupported_filters("Airbnb", StayFilters(amenities=["wifi", "pool"]), dog=True) == []


def test_skip_notice_text():
    assert skip_notice("Booking.com", ["Fireplace"]) == "Booking.com skipped – it can't filter by Fireplace"
