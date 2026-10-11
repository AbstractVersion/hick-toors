from datetime import date

import pytest

from app.models import StayFilters, StayQuery
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import (
    BookingBlocked, BookingProvider, HotelNotFound, HotelRef, hotel_ref_from_url, nflt_tokens, parse_search,
    review_score,
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


async def test_default_is_eight_attempts_with_jittered_exponential_backoff():
    sleeps, calls = [], []

    async def fetch(url, params):
        calls.append(1)
        return 202, ""

    async def sleep(seconds):
        sleeps.append(seconds)

    provider = BookingProvider(fetch=fetch, sleep=sleep, rand=lambda low, high: 1.0)
    with pytest.raises(ProviderUnavailable):
        await provider.search(Q)
    assert len(calls) == 8
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]  # no sleep after the last attempt


async def test_backoff_applies_jitter_range():
    seen, sleeps = [], []

    async def fetch(url, params):
        return 202, ""

    async def sleep(seconds):
        sleeps.append(seconds)

    def rand(low, high):
        seen.append((low, high))
        return 0.5

    with pytest.raises(ProviderUnavailable):
        await BookingProvider(fetch=fetch, sleep=sleep, retries=3, rand=rand).search(Q)
    assert set(seen) == {(0.6, 1.2)} and sleeps == [0.5, 1.0]


async def test_gives_up_with_provider_unavailable():
    async def fetch(url, params):
        raise ConnectionError("reset")

    with pytest.raises(ProviderUnavailable, match="Booking.com unavailable"):
        await BookingProvider(fetch=fetch, retries=2, sleep=no_sleep).search(Q)


APP_LINK = ("https://www.booking.com/hotel/gr/aenaon-guesthouse.el.html?aid=964694&app_hotel_id=5546346"
            "&checkin=2026-10-28&checkout=2026-11-01&from_sn=android&group_adults=6&group_children=0"
            "&label=hotel_details-Ykg6K3F%401791144453-QdJxjvg%401791566823&no_rooms=1&req_adults=6"
            "&room1=A%2CA%2CA%2CA%2CA%2CA&chal_t=1791706667807&force_referer=")


@pytest.mark.parametrize("url,ref", [
    (APP_LINK, HotelRef(page="aenaon-guesthouse", hotel_id="5546346")),
    ("https://www.booking.com/hotel/gr/aenaon-guesthouse.html", HotelRef(page="aenaon-guesthouse")),
    ("https://booking.com/hotel/gr/aenaon-guesthouse.en-gb.html?hotel_id=12", HotelRef("aenaon-guesthouse", "12")),
    ("  https://m.booking.com/hotel/gr/Aenaon-Guesthouse.EL.html  ", HotelRef(page="aenaon-guesthouse")),
    ("https://www.booking.com/hotel/gr/x.html?app_hotel_id=12ab", HotelRef(page="x")),  # non-digit id ignored
])
def test_hotel_ref_from_url(url, ref):
    assert hotel_ref_from_url(url) == ref


@pytest.mark.parametrize("url", [
    "https://evil.com/hotel/gr/x.html", "https://www.booking.com.evil.com/hotel/gr/x.html",
    "https://www.booking.com/searchresults.html", "https://www.booking.com/hotel/gr/../x.html",
    "ftp://www.booking.com/hotel/gr/x.html", "https://www.booking.com/hotel/gr/x", "aenaon-guesthouse", "",
    "https://www.airbnb.com/rooms/1", "http://[::1/hotel/gr/x.html",
])
def test_hotel_ref_rejects_other_links(url):
    assert hotel_ref_from_url(url) is None


def test_parse_search_reads_pets_badge():
    html = booking_search_html([booking_property(1, "A", 38.0, 22.4, pets_badge=True),
                                booking_property(2, "B", 38.0, 22.4)])
    assert [s.pets_allowed for s in parse_search(html)] == [True, None]


REF = HotelRef(page="aenaon-guesthouse", hotel_id="5546346")
CHECK_IN, CHECK_OUT = date(2026, 10, 30), date(2026, 11, 1)


async def test_lookup_by_hotel_id_with_pets_filter_first():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return 200, booking_search_html([
            booking_property(99, "Neighbour", 38.0, 22.4, page="other"),
            booking_property(5546346, "Aenaon Guesthouse", 37.99, 22.46, amount=875.0, page="aenaon-guesthouse",
                             pets_badge=True)])

    stay = await BookingProvider(fetch=fetch, sleep=no_sleep).lookup(REF, CHECK_IN, CHECK_OUT, 6)
    assert len(calls) == 1
    assert calls[0]["dest_id"] == "5546346" and calls[0]["dest_type"] == "hotel" and "ss" not in calls[0]
    assert calls[0]["nflt"] == "hotelfacility=4" and calls[0]["group_adults"] == 6
    assert calls[0]["checkin"] == "2026-10-30" and calls[0]["checkout"] == "2026-11-01"
    assert stay.id == "booking:5546346" and stay.total_price == 875.0 and stay.pets_allowed is True
    assert stay.url == ("https://www.booking.com/hotel/gr/aenaon-guesthouse.html"
                        "?checkin=2026-10-30&checkout=2026-11-01&group_adults=6")


async def test_lookup_by_name_then_by_found_id_for_price_and_pets():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        if "ss" in params:  # text search: listed without a price, and only without the pets filter
            props = [booking_property(1, "Other", 38.0, 22.4, page="other")]
            if "nflt" not in params:
                props.append(booking_property(7, "Aenaon Guesthouse", 37.99, 22.46, amount=None,
                                              page="aenaon-guesthouse"))
            return 200, booking_search_html(props)
        return 200, booking_search_html([booking_property(7, "Aenaon Guesthouse", 37.99, 22.46, amount=875.0,
                                                          page="aenaon-guesthouse", pets_badge=True)])

    stay = await BookingProvider(fetch=fetch, sleep=no_sleep).lookup(HotelRef(page="aenaon-guesthouse"),
                                                                      CHECK_IN, CHECK_OUT, 2)
    assert [(c.get("ss"), c.get("dest_id"), c.get("nflt")) for c in calls] == [
        ("aenaon guesthouse", None, "hotelfacility=4"), ("aenaon guesthouse", None, None),
        (None, "7", "hotelfacility=4")]
    assert stay.id == "booking:7" and stay.total_price == 875.0 and stay.pets_allowed is True


async def test_lookup_by_name_keeps_name_result_when_id_search_misses():
    async def fetch(url, params):
        if "ss" in params:
            return 200, booking_search_html([booking_property(7, "Aenaon", 37.99, 22.46, amount=None,
                                                              page="aenaon-guesthouse")])
        return 200, booking_search_html([])

    stay = await BookingProvider(fetch=fetch, sleep=no_sleep).lookup(HotelRef(page="aenaon-guesthouse"), None,
                                                                      None, 2)
    assert stay.id == "booking:7" and stay.total_price is None and stay.pets_allowed is None
    assert stay.url == "https://www.booking.com/hotel/gr/aenaon-guesthouse.html"  # no dates: plain link


async def test_lookup_by_id_falls_back_to_unfiltered_search():
    calls = []

    async def fetch(url, params):
        calls.append(params.get("nflt"))
        props = [] if "nflt" in params else [booking_property(5546346, "Aenaon", 37.99, 22.46,
                                                                page="aenaon-guesthouse")]
        return 200, booking_search_html(props)

    stay = await BookingProvider(fetch=fetch, sleep=no_sleep).lookup(REF, None, None, 2)
    assert calls == ["hotelfacility=4", None] and stay.pets_allowed is None


async def test_lookup_not_in_results_is_not_found():
    async def fetch(url, params):
        return 200, booking_search_html([booking_property(1, "Other", 38.0, 22.4, page="other")])

    with pytest.raises(HotelNotFound):
        await BookingProvider(fetch=fetch, sleep=no_sleep).lookup(REF, CHECK_IN, CHECK_OUT, 2)


async def test_lookup_blocked_every_time_is_unavailable():
    async def fetch(url, params):
        return 202, BOOKING_BLOCKED_HTML

    with pytest.raises(ProviderUnavailable, match="Booking.com didn't answer"):
        await BookingProvider(fetch=fetch, sleep=no_sleep, retries=2).lookup(REF, CHECK_IN, CHECK_OUT, 2)
