from datetime import date

import pytest
from pydantic import ValidationError

from app.models import HikeQuery, StaySearchQuery, TripQuery


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
