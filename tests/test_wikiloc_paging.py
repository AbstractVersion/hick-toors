"""Concurrent page batches, total count and start_page of WikilocSource.search."""

import asyncio

import pytest

from app.models import BBox, HikeFilters
from app.sources.wikiloc import WikilocLoginExpired, WikilocSource, WikilocUnavailable
from tests.builders import wikiloc_spa

BOX = BBox(south=39.3, west=22.9, north=39.5, east=23.2)


def full_page(start):
    return {"spas": [wikiloc_spa(start + i, f"r{start + i}", 39.3, 22.9) for i in range(25)]}


async def test_pages_are_fetched_concurrently_in_batches_and_keep_order():
    in_flight, peak, order = 0, 0, []

    async def fetch(url, params):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        order.append(params["from"])
        # Earlier pages answer last: the merged output must still follow page order.
        await asyncio.sleep(0.01 * (10 - params["from"] // 25))
        in_flight -= 1
        return full_page(params["from"])

    result = await WikilocSource(fetch=fetch, concurrency=3).search(BOX, pages=7)
    assert peak == 3
    assert sorted(order[:3]) == [0, 25, 50] and sorted(order) == [i * 25 for i in range(7)]
    assert [h.id for h in result.hikes] == [f"wikiloc:{i}" for i in range(175)]
    assert result.pages_fetched == 7 and result.has_more is True


async def test_short_page_stops_after_its_batch():
    calls = []

    async def fetch(url, params):
        calls.append(params["from"])
        if params["from"] == 25:
            return {"spas": [wikiloc_spa(1000, "last", 39.3, 22.9)]}
        return full_page(params["from"]) if params["from"] == 0 else {"spas": []}

    result = await WikilocSource(fetch=fetch, concurrency=2).search(BOX, pages=10)
    assert sorted(calls) == [0, 25]  # batch of two, page 25 was short: no further batch
    assert len(result.hikes) == 26 and result.has_more is False and result.pages_fetched == 2


async def test_start_page_offsets_the_requests():
    calls = []

    async def fetch(url, params):
        calls.append((params["from"], params["to"]))
        return full_page(params["from"])

    result = await WikilocSource(fetch=fetch, concurrency=2).search(BOX, pages=3, start_page=10)
    assert sorted(calls) == [(250, 275), (275, 300), (300, 325)]
    assert result.pages_fetched == 3 and result.has_more is True


async def test_total_comes_from_the_first_page_count():
    async def fetch(url, params):
        page = full_page(params["from"])
        page["count"] = 17042 if params["from"] == 0 else 5
        return page

    result = await WikilocSource(fetch=fetch).search(BOX, pages=2)
    assert result.total == 17042


@pytest.mark.parametrize("count", [None, "n/a", True])
async def test_total_is_none_when_count_is_missing_or_odd(count):
    async def fetch(url, params):
        return {"spas": [], "count": count}

    assert (await WikilocSource(fetch=fetch).search(BOX)).total is None


async def test_later_page_failure_in_a_batch_keeps_earlier_pages_only():
    async def fetch(url, params):
        return None if params["from"] == 50 else full_page(params["from"])

    result = await WikilocSource(fetch=fetch, concurrency=4).search(BOX, pages=4)
    assert len(result.hikes) == 50  # pages 0 and 1; page 3 is dropped to keep the offsets contiguous
    assert result.pages_fetched == 2 and result.has_more is True  # Load more can retry from page 2


async def test_first_page_failure_still_raises_with_concurrency():
    async def fetch(url, params):
        return None if params["from"] == 0 else full_page(params["from"])

    with pytest.raises(WikilocUnavailable):
        await WikilocSource(fetch=fetch, concurrency=4).search(BOX, pages=4)


async def test_login_checks_apply_to_the_first_page_of_a_fresh_search():
    async def fetch(url, params, headers=None):
        return {"spas": [], "forwardToLogin": True} if params["from"] == 0 else full_page(params["from"])

    with pytest.raises(WikilocLoginExpired):
        await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(BOX, filters=HikeFilters(), pages=4)


async def test_load_more_past_the_end_with_login_is_empty_not_expired():
    async def fetch(url, params, headers=None):
        return {"spas": [], "count": 90}

    result = await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(
        BOX, filters=HikeFilters(distance_min_km=5), pages=2, start_page=4)
    assert result.hikes == [] and result.has_more is False


async def test_load_more_with_forward_to_login_raises_expired():
    async def fetch(url, params, headers=None):
        return {"spas": [], "forwardToLogin": True}

    with pytest.raises(WikilocLoginExpired):
        await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(BOX, filters=HikeFilters(), start_page=4)


async def test_failure_after_a_short_page_reports_no_more():
    async def fetch(url, params):
        if params["from"] == 0:
            return full_page(0)
        if params["from"] == 25:
            return {"spas": [wikiloc_spa(900 + i, "s", 39.3, 22.9) for i in range(10)]}
        return None

    result = await WikilocSource(fetch=fetch, concurrency=3).search(BOX, pages=3)
    assert len(result.hikes) == 35 and result.has_more is False


async def test_load_more_first_page_failure_is_unavailable():
    async def fetch(url, params):
        return None

    with pytest.raises(WikilocUnavailable):
        await WikilocSource(fetch=fetch).search(BOX, pages=2, start_page=4)
