from datetime import date

import pytest

from app.models import StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import BookingBlocked, BookingProvider, parse_search
from tests.builders import BOOKING_BLOCKED_HTML, booking_property, booking_search_html

Q = StayQuery(lat=39.40, lon=23.03, radius_km=15, check_in=date(2026, 10, 23),
              check_out=date(2026, 10, 25), guests=2, dog=True)


def test_parse_search_maps_fields():
    html = booking_search_html([booking_property(2228437, "Anna's Villa Portaria", 39.3906, 23.0020,
                                                 page="anna-39-s-villa-portaria-portaria")])
    [stay] = parse_search(html)
    assert stay.id == "booking:2228437"
    assert stay.source == "booking"
    assert stay.name == "Anna's Villa Portaria"
    assert stay.url == "https://www.booking.com/hotel/gr/anna-39-s-villa-portaria-portaria.html"
    assert stay.rating == 4.8
    assert stay.total_price == 354.0 and stay.currency == "EUR"
    assert stay.photo == "https://cf.bstatic.com/xdata/images/hotel/square600/2228437.webp"


def test_parse_search_genuine_empty_result():
    assert parse_search(booking_search_html([])) == []


def test_parse_search_challenge_page_raises():
    with pytest.raises(BookingBlocked):
        parse_search(BOOKING_BLOCKED_HTML)


async def no_sleep(seconds):
    pass


async def test_search_with_dog_uses_pets_filter_and_marks_pets():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return 200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)])

    [stay] = await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q)
    assert calls[0]["nflt"] == "hotelfacility=4"
    assert calls[0]["checkin"] == "2026-10-23" and calls[0]["group_adults"] == 2
    assert stay.pets_allowed is True and stay.has_garden is None
    assert "checkin=2026-10-23" in stay.url


async def test_search_without_dog_has_no_filter():
    async def fetch(url, params):
        assert "nflt" not in params
        return 200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)])

    [stay] = await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q.model_copy(update={"dog": False}))
    assert stay.pets_allowed is None


async def test_retries_after_challenge_then_succeeds():
    responses = iter([(202, ""), (200, BOOKING_BLOCKED_HTML),
                      (200, booking_search_html([booking_property(1, "Villa", 39.4, 23.0)]))])

    async def fetch(url, params):
        return next(responses)

    assert len(await BookingProvider(fetch=fetch, retries=3, sleep=no_sleep).search(Q)) == 1


async def test_gives_up_with_provider_unavailable():
    async def fetch(url, params):
        raise ConnectionError("reset")

    with pytest.raises(ProviderUnavailable, match="Booking.com unavailable"):
        await BookingProvider(fetch=fetch, retries=2, sleep=no_sleep).search(Q)
