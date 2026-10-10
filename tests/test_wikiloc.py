import httpx
import pytest

from app.models import BBox
from app.sources.wikiloc import WikilocSource, WikilocUnavailable, make_fetch, parse_search
from tests.builders import wikiloc_spa

BOX = BBox(south=39.3, west=22.9, north=39.5, east=23.2)


def test_parse_search_maps_fields():
    data = {"spas": [wikiloc_spa(133095679, "Portaria - Makrinitsa", 39.39, 22.99,
                                 distance="11,81", slope="1.136", skill=3, trailrank=89)]}
    [hike] = parse_search(data)
    assert hike.id == "wikiloc:133095679"
    assert hike.url == "https://es.wikiloc.com/rutas-senderismo/route-133095679"
    assert hike.distance_km == 11.81
    assert hike.elevation_gain_m == 1136
    assert hike.difficulty == "Hard"
    assert hike.trailrank == 89
    assert (hike.lat, hike.lon) == (39.39, 22.99)
    assert hike.thumbnail == "https://s0.wklcdn.com/133095679_tn.jpg"


def test_parse_search_keeps_only_greek_hiking_routes():
    data = {"spas": [
        wikiloc_spa(1, "Greek script", 39.3, 22.9, near="Miléai, Θεσσαλία (Ελλάδα)"),
        wikiloc_spa(2, "English", 39.3, 22.9, near="Portariá, Thessaly (Greece)"),
        wikiloc_spa(3, "Spanish", 39.3, 22.9, near="Volos, Tesalia (Grecia)"),
        wikiloc_spa(4, "Diving", 39.3, 22.9, picto=41),
        wikiloc_spa(5, "Turkish coast", 39.3, 26.7, near="Ayvalık, Balıkesir (Türkiye)"),
    ]}
    assert [h.id for h in parse_search(data)] == ["wikiloc:1", "wikiloc:2", "wikiloc:3"]


def test_parse_search_skips_routes_without_coordinates():
    spa = wikiloc_spa(1, "No coords", None, None)
    assert parse_search({"spas": [spa]}) == []


def test_parse_search_handles_missing_spas():
    assert parse_search({}) == []


async def test_search_pages_by_offset_and_dedupes():
    # Wikiloc pages with from/to offsets (probed 2026-10-10); a `page` param is ignored.
    pages = {
        0: {"spas": [wikiloc_spa(i, f"r{i}", 39.3, 22.9) for i in range(25)]},
        25: {"spas": [wikiloc_spa(24, "dup", 39.3, 22.9), wikiloc_spa(99, "r99", 39.3, 22.9)]},
    }
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return pages[params["from"]]

    hikes = await WikilocSource(fetch=fetch, pages=3).search(BOX)
    assert len(hikes) == 26
    assert calls[0]["sw"] == "39.3,22.9" and calls[0]["ne"] == "39.5,23.2"
    assert [(c["from"], c["to"]) for c in calls] == [(0, 25), (25, 50)]
    assert all("page" not in c for c in calls)
    assert len(calls) == 2  # second page had < 25 results, so no third


async def test_search_raises_when_first_page_fails():
    async def fetch(url, params):
        return None

    with pytest.raises(WikilocUnavailable, match="Hikes temporarily unavailable"):
        await WikilocSource(fetch=fetch).search(BOX)


async def test_search_returns_partial_when_later_page_fails():
    async def fetch(url, params):
        if params["from"] == 0:
            return {"spas": [wikiloc_spa(i, f"r{i}", 39.3, 22.9) for i in range(25)]}
        return None

    assert len(await WikilocSource(fetch=fetch, pages=2).search(BOX)) == 25


async def test_make_fetch_retries_403_then_succeeds():
    responses = iter([httpx.Response(403), httpx.Response(200, json={"spas": []})])
    transport = httpx.MockTransport(lambda request: next(responses))
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    fetch = make_fetch(transport=transport, sleep=sleep)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}) == {"spas": []}
    assert len(sleeps) == 1


async def test_make_fetch_gives_up_after_retries():
    transport = httpx.MockTransport(lambda request: httpx.Response(403))

    async def sleep(seconds):
        pass

    fetch = make_fetch(transport=transport, sleep=sleep, retries=2)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}) is None
