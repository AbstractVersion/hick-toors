from datetime import date

import pytest

from app.trip_window import max_drive_minutes, stay_window, trip_days

FRI = date(2026, 10, 23)


def d(day):
    return date(2026, 10, day)


def test_trip_days_inclusive():
    assert trip_days(FRI, FRI) == 1
    assert trip_days(FRI, d(25)) == 3


@pytest.mark.parametrize(
    "end,expected",
    [(23, 90), (24, 240), (25, 240), (26, None), (30, None)],
)
def test_auto_bands(end, expected):
    assert max_drive_minutes(FRI, d(end), "auto", 90, 240) == expected


def test_override_and_unlimited():
    assert max_drive_minutes(FRI, d(25), 60, 90, 240) == 60
    assert max_drive_minutes(FRI, FRI, "unlimited", 90, 240) is None


def test_stay_window_gives_at_least_one_night():
    assert stay_window(FRI, FRI) == (FRI, d(24))
    assert stay_window(FRI, d(25)) == (FRI, d(25))
