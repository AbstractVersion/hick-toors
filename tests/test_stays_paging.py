"""Drawn-area stay searches: exact bbox and paging for Airbnb (cursor) and Booking (offset)."""

from datetime import date

import httpx

from app.models import BBox, StayQuery
from app.sources.stays.airbnb import AirbnbProvider, parse_cursors
from app.sources.stays.booking import BookingProvider
from tests.builders import (
    airbnb_listing_html, airbnb_result, airbnb_search_html, booking_property, booking_search_html,
)

BOX = BBox(south=38.10, west=23.60, north=38.25, east=23.80)
Q = StayQuery(lat=39.40, lon=23.03, radius_km=15, check_in=date(2026, 10, 23),
              check_out=date(2026, 10, 25), guests=2, dog=False)
AREA_Q = Q.model_copy(update={"bbox": BOX})


async def no_sleep(seconds):
    pass


# ---- Airbnb ----

def paged(pages):
    """pages = list of id lists; page 1 lists every cursor, cursor c<i> selects page i."""
    calls = []
    cursors = [f"c{i}" for i in range(len(pages))]

    async def fetch(url, params):
        calls.append(dict(params))
        idx = cursors.index(params["cursor"]) if "cursor" in params else 0
        results = [airbnb_result(i, f"S{i}", 38.15, 23.7) for i in pages[idx]]
        return airbnb_search_html(results, cursors=cursors)

    return fetch, calls


def test_parse_cursors():
    assert parse_cursors(airbnb_search_html([], cursors=["a", "b"])) == ["a", "b"]
    assert parse_cursors(airbnb_search_html([])) == []


async def test_airbnb_bbox_is_passed_exactly():
    fetch, calls = paged([[1]])
    await AirbnbProvider(fetch=fetch).search(AREA_Q)
    p = calls[0]
    assert (p["sw_lat"], p["sw_lng"], p["ne_lat"], p["ne_lng"]) == (38.10, 23.60, 38.25, 23.80)


async def test_airbnb_without_bbox_uses_square_and_one_page():
    fetch, calls = paged([[1], [2]])
    await AirbnbProvider(fetch=fetch, area_pages=3).search(Q)
    assert len(calls) == 1 and "cursor" not in calls[0]
    assert calls[0]["sw_lat"] < 39.40 < calls[0]["ne_lat"]


async def test_airbnb_area_search_follows_cursors_and_dedupes():
    fetch, calls = paged([[1, 2], [2, 3], [4]])
    stays = await AirbnbProvider(fetch=fetch, area_pages=3).search(AREA_Q)
    assert [s.id for s in stays] == ["airbnb:1", "airbnb:2", "airbnb:3", "airbnb:4"]
    assert [c.get("cursor") for c in calls] == [None, "c1", "c2"]


async def test_airbnb_page_cap_and_single_cursor():
    fetch, calls = paged([[1], [2], [3], [4]])
    await AirbnbProvider(fetch=fetch, area_pages=2).search(AREA_Q)
    assert len(calls) == 2
    fetch, calls = paged([[1]])
    await AirbnbProvider(fetch=fetch, area_pages=3).search(AREA_Q)
    assert len(calls) == 1


async def test_airbnb_later_page_failure_keeps_earlier_pages():
    fetch, _ = paged([[1], [2]])

    async def flaky(url, params):
        if "cursor" in params:
            raise httpx.ConnectError("boom")
        return await fetch(url, params)

    stays = await AirbnbProvider(fetch=flaky, area_pages=3).search(AREA_Q)
    assert [s.id for s in stays] == ["airbnb:1"]


async def test_airbnb_dog_flow_paginates_both_searches():
    calls = []

    async def fetch(url, params):
        calls.append(dict(params))
        if "/rooms/" in url:
            return airbnb_listing_html(["Backyard"])
        pets = params.get("pets") == 1
        page2 = "cursor" in params
        ids = ([11] if page2 else [10]) if pets else ([21] if page2 else [10, 20])
        return airbnb_search_html([airbnb_result(i, f"S{i}", 38.15, 23.7) for i in ids],
                                  cursors=["c0", "c1"])

    stays = await AirbnbProvider(fetch=fetch, area_pages=2).search(AREA_Q.model_copy(update={"dog": True}))
    assert [s.id for s in stays] == ["airbnb:10", "airbnb:11", "airbnb:20", "airbnb:21"]
    assert [s.pets_allowed for s in stays] == [True, True, None, None]
    searches = [c for c in calls if "ne_lat" in c]
    assert len(searches) == 4 and sum(1 for c in searches if c.get("pets") == 1) == 2


# ---- Booking ----

async def test_booking_area_search_pages_with_offset_and_dedupes():
    calls = []

    async def fetch(url, params):
        calls.append(dict(params))
        if params.get("offset"):
            props = [booking_property(2, "B", 38.15, 23.7), booking_property(3, "C", 38.16, 23.7)]
        else:
            props = [booking_property(1, "A", 38.15, 23.7), booking_property(2, "B", 38.15, 23.7)]
        return 200, booking_search_html(props)

    stays = await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=2).search(AREA_Q)
    assert [s.id for s in stays] == ["booking:1", "booking:2", "booking:3"]
    assert "offset" not in calls[0] and calls[1]["offset"] == 2  # step = results received so far


async def test_booking_page_two_skipped_when_page_one_has_nothing_in_the_box():
    calls = []

    async def fetch(url, params):
        calls.append(dict(params))
        return 200, booking_search_html([booking_property(1, "Far", 39.4, 23.0)])  # outside BOX

    stays = await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=3).search(AREA_Q)
    assert len(calls) == 1 and [s.id for s in stays] == ["booking:1"]


async def test_booking_page_two_fetched_when_page_one_has_a_stay_in_the_box():
    calls = []

    async def fetch(url, params):
        calls.append(dict(params))
        return 200, booking_search_html([booking_property(1, "Far", 39.4, 23.0),
                                         booking_property(2, "In", 38.15, 23.7)])

    await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=2).search(AREA_Q)
    assert len(calls) == 2


async def test_booking_non_area_search_is_one_page():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return 200, booking_search_html([booking_property(1, "A", 39.4, 23.0)])

    await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=3).search(Q)
    assert len(calls) == 1


async def test_booking_later_page_blocked_keeps_first_page():
    async def fetch(url, params):
        if params.get("offset"):
            return 202, "challenge"
        return 200, booking_search_html([booking_property(1, "A", 38.15, 23.7)])

    stays = await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=2).search(AREA_Q)
    assert [s.id for s in stays] == ["booking:1"]


async def test_booking_empty_later_page_stops():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return 200, booking_search_html([booking_property(1, "A", 38.15, 23.7)] if len(calls) == 1 else [])

    stays = await BookingProvider(fetch=fetch, sleep=no_sleep, area_pages=4).search(AREA_Q)
    assert len(calls) == 2 and len(stays) == 1
