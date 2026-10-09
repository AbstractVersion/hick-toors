import pytest

from app.models import Hike, Stay
from app.ranking import rank_combos

W = {"hike": 40, "stay": 25, "proximity": 20, "price": 15}


def hike(id, trailrank):
    return Hike(id=id, name=id, url="u", lat=39.40, lon=23.00, trailrank=trailrank)


def stay(id, rating, price, lat=39.40, lon=23.00, badge="pets"):
    return Stay(id=id, source="airbnb", name=id, url="u", lat=lat, lon=lon,
                rating=rating, total_price=price, badge=badge)


def test_perfect_combo_scores_100():
    [combo] = rank_combos([(hike("h", 100), stay("s", 5.0, 100))], W, radius_km=15, dog=True)
    assert combo.score == 100
    assert combo.breakdown.model_dump() == {"hike": 40, "stay": 25, "proximity": 20, "price": 15}


def test_price_is_normalised_within_set_and_sorted():
    h = hike("h", 80)
    cheap = stay("cheap", 4.5, 100)
    pricey = stay("pricey", 4.5, 300)
    combos = rank_combos([(h, pricey), (h, cheap)], W, radius_km=15, dog=True)
    assert [c.stay.id for c in combos] == ["cheap", "pricey"]
    assert combos[0].breakdown.price == 15 and combos[1].breakdown.price == 0


def test_far_stay_gets_no_proximity_points():
    far = stay("far", 4.0, 100, lat=39.60)  # ~22 km north
    [combo] = rank_combos([(hike("h", 50), far)], W, radius_km=15, dog=True)
    assert combo.breakdown.proximity == 0


def test_missing_values_use_neutral_defaults():
    [combo] = rank_combos([(hike("h", None), stay("s", None, None))], W, radius_km=15, dog=True)
    assert combo.breakdown.hike == 20 and combo.breakdown.stay == 15 and combo.breakdown.price == 7.5


def test_garden_unconfirmed_ranks_after_pet_friendly_when_dog():
    h = hike("h", 80)
    great_garden = stay("garden", 5.0, 50, badge="garden_unconfirmed")
    ok_pets = stay("pets", 3.0, 300, badge="pets")
    combos = rank_combos([(h, great_garden), (h, ok_pets)], W, radius_km=15, dog=True)
    assert [c.stay.id for c in combos] == ["pets", "garden"]
    combos = rank_combos([(h, great_garden), (h, ok_pets)], W, radius_km=15, dog=False)
    assert [c.stay.id for c in combos] == ["garden", "pets"]


def test_empty():
    assert rank_combos([], W, radius_km=15, dog=True) == []
