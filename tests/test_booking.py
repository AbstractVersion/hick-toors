from datetime import date

import pytest

from app.models import StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import (
    BookingBlocked, BookingProvider, nflt_tokens, parse_search, review_score,
)
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


def test_nflt_tokens_map_every_supported_filter():
    f = StayFilters(price_min=60, price_max=120, min_rating=4.5, entire_place=True,
                    property_types=["house", "guesthouse"], min_bedrooms=7, min_bathrooms=2,
                    amenities=["parking", "ac"], free_cancellation=True, breakfast=True, min_stars=4,
                    beachfront=True)
    assert nflt_tokens(f, dog=False) == [
        "price=EUR-60-120-1", "review_score=90", "privacy_type=3",
        "ht_id=208", "ht_id=216", "ht_id=220", "ht_id=223",
        "entire_place_bedroom_count=5", "min_bathrooms=2",
        "hotelfacility=2", "roomfacility=11",
        "fc=2", "mealplan=1", "class=4", "class=5", "hotelfacility=146",
    ]


@pytest.mark.parametrize("low,high,token", [
    (None, 100, "price=EUR-min-100-1"),
    (80, None, "price=EUR-80-max-1"),
])
def test_open_price_ends_use_literal_words(low, high, token):
    assert nflt_tokens(StayFilters(price_min=low, price_max=high), dog=False) == [token]


@pytest.mark.parametrize("rating,score", [(3.5, 70), (4.0, 80), (4.5, 90), (4.8, 90), (2.5, None)])
def test_review_score_floors_to_step(rating, score):
    assert review_score(rating) == score


async def test_filters_join_pets_token_and_use_en_gb_endpoint():
    calls = []

    async def fetch(url, params):
        calls.append((url, params))
        return 200, booking_search_html([])

    q = Q.model_copy(update={"filters": StayFilters(price_max=120, free_cancellation=True)})
    await BookingProvider(fetch=fetch, sleep=no_sleep).search(q)
    url, params = calls[0]
    assert url == "https://www.booking.com/searchresults.en-gb.html"
    assert params["nflt"] == "hotelfacility=4;price=EUR-min-120-1;fc=2"


async def test_each_attempt_is_a_separate_fetch_call():
    # default_fetch opens a fresh curl_cffi session per call, so retrying = fresh session.
    calls = []

    async def fetch(url, params):
        calls.append(url)
        return (202, "") if len(calls) < 3 else (200, booking_search_html([]))

    assert await BookingProvider(fetch=fetch, sleep=no_sleep).search(Q) == []
    assert len(calls) == 3


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
