import pytest

from app.dog_rule import apply_dog_rule, assign_badge, badge_tier
from app.models import Stay


def stay(id, pets=None, garden=None):
    return Stay(id=id, source="airbnb", name=id, url="u", lat=39.4, lon=23.0,
                pets_allowed=pets, has_garden=garden)


@pytest.mark.parametrize("pets,garden,dog,expected", [
    (True, None, True, "pets"),
    (True, True, True, "pets_garden"),
    (None, True, True, "garden_unconfirmed"),
    (None, True, False, "garden"),
    (False, True, True, None),
    (False, True, False, "garden"),
    (None, None, True, None),
    (None, False, False, None),
])
def test_assign_badge(pets, garden, dog, expected):
    assert assign_badge(stay("s", pets, garden), dog) == expected


def test_dog_on_filters_and_orders_garden_only_last():
    stays = [
        stay("garden-only", None, True),
        stay("plain", None, False),
        stay("pets", True, False),
        stay("no-pets-garden", False, True),
        stay("pets-garden", True, True),
    ]
    out = apply_dog_rule(stays, dog=True)
    assert [s.id for s in out] == ["pets", "pets-garden", "garden-only"]
    assert [badge_tier(s) for s in out] == [0, 0, 1]


def test_dog_off_keeps_everything_with_badges():
    stays = [stay("a", None, True), stay("b", None, None)]
    out = apply_dog_rule(stays, dog=False)
    assert [(s.id, s.badge) for s in out] == [("a", "garden"), ("b", None)]


def test_does_not_mutate_input():
    s = stay("a", True)
    apply_dog_rule([s], dog=True)
    assert s.badge is None
