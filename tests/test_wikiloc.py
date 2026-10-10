import httpx
import pytest

from app.models import BBox, HikeFilters
from app.sources.wikiloc import (
    WikilocLoginExpired, WikilocSource, WikilocUnavailable, make_fetch, parse_search, server_filter_params,
)
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
    assert hike.difficulty == "Hard" and hike.skill == 3
    assert hike.activity == 1
    assert hike.trailrank == 89
    assert (hike.lat, hike.lon) == (39.39, 22.99)
    assert hike.thumbnail == "https://s0.wklcdn.com/133095679_tn.jpg"


def test_parse_search_keeps_greek_routes_of_every_activity():
    # v2 contract change: activities are filtered in the engine (HikeFilters.activities), so
    # parse_search keeps non-hiking pictos and records them in Hike.activity. Greece-only stays.
    data = {"spas": [
        wikiloc_spa(1, "Greek script", 39.3, 22.9, near="Miléai, Θεσσαλία (Ελλάδα)"),
        wikiloc_spa(2, "English", 39.3, 22.9, near="Portariá, Thessaly (Greece)"),
        wikiloc_spa(3, "Spanish", 39.3, 22.9, near="Volos, Tesalia (Grecia)"),
        wikiloc_spa(4, "Diving", 39.3, 22.9, picto=41),
        wikiloc_spa(5, "Turkish coast", 39.3, 26.7, near="Ayvalık, Balıkesir (Türkiye)"),
    ]}
    hikes = parse_search(data)
    assert [h.id for h in hikes] == ["wikiloc:1", "wikiloc:2", "wikiloc:3", "wikiloc:4"]
    assert [h.activity for h in hikes] == [1, 1, 1, 41]


def test_parse_search_ignores_unknown_skill():
    [hike] = parse_search({"spas": [wikiloc_spa(1, "Odd", 39.3, 22.9, skill=9)]})
    assert hike.skill is None and hike.difficulty is None


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


async def test_search_sort_param_only_for_top_rated():
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return {"spas": []}

    source = WikilocSource(fetch=fetch)
    await source.search(BOX)
    await source.search(BOX, sort="top_rated")
    assert "sort" not in calls[0]
    assert calls[1]["sort"] == "trailrank"


async def test_search_pages_override():
    calls = []

    async def fetch(url, params):
        calls.append(params["from"])
        return {"spas": [wikiloc_spa(params["from"] + i, "r", 39.3, 22.9) for i in range(25)]}

    source = WikilocSource(fetch=fetch, pages=2)
    assert len(await source.search(BOX)) == 50
    calls.clear()
    assert len(await source.search(BOX, pages=4)) == 100
    assert calls == [0, 25, 50, 75]


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


def test_server_filter_params():
    assert server_filter_params(HikeFilters()) == {"a": "1"}
    f = HikeFilters(activities=[1, 43], distance_min_km=10, distance_max_km=15.5, elevation_min_m=800,
                    elevation_max_m=1500, difficulty_min=3, difficulty_max=4, loop_only=True,
                    min_trailrank=50, sort="top_rated")
    assert server_filter_params(f) == {"a": "1,43", "lfr": "10", "lto": "15.5", "ufr": 800, "uto": 1500,
                                       "sfr": 3, "sto": 4, "t": 1}
    assert server_filter_params(HikeFilters(difficulty_min=5)) == {"a": "1", "sfr": 4}  # max 5: no sto
    assert server_filter_params(HikeFilters(difficulty_max=2)) == {"a": "1", "sfr": 1, "sto": 2}


async def test_logged_in_search_sends_cookie_and_server_filters():
    calls = []

    async def fetch(url, params, headers=None):
        calls.append((params, headers))
        return {"spas": [wikiloc_spa(1, "r", 39.3, 22.9)], "forwardToLogin": None, "restricted": False}

    source = WikilocSource(fetch=fetch, cookie="SESSION=abc")
    assert source.logged_in and "abc" not in repr(source)
    hikes = await source.search(BOX, filters=HikeFilters(distance_min_km=10, loop_only=True))
    params, headers = calls[0]
    assert headers == {"Cookie": "SESSION=abc"}
    assert (params["a"], params["lfr"], params["t"]) == ("1", "10", 1)
    assert len(hikes) == 1


async def test_anonymous_search_sends_no_cookie_and_no_filter_params():
    calls = []

    async def fetch(url, params):  # two-argument fetch: anonymous calls must not pass headers
        calls.append(params)
        return {"spas": []}

    await WikilocSource(fetch=fetch).search(BOX, filters=HikeFilters(distance_min_km=10))
    await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(
        BOX, filters=HikeFilters(distance_min_km=10), use_login=False)
    assert all(not {"a", "lfr", "t"} & set(p) for p in calls)


@pytest.mark.parametrize("payload", [{"spas": [], "count": 0}, {"spas": None, "forwardToLogin": True}])
async def test_logged_in_empty_or_forward_to_login_raises_expired(payload):
    async def fetch(url, params, headers=None):
        return payload

    with pytest.raises(WikilocLoginExpired):
        await WikilocSource(fetch=fetch, cookie="SESSION=abc").search(BOX, filters=HikeFilters())


async def test_make_fetch_sends_extra_headers():
    seen = []

    def handler(request):
        seen.append((request.headers.get("cookie"), request.headers.get("user-agent")))
        return httpx.Response(200, json={"spas": []})

    fetch = make_fetch(transport=httpx.MockTransport(handler))
    await fetch("https://es.wikiloc.com/wikiloc/find.do", {}, {"Cookie": "SESSION=abc"})
    await fetch("https://es.wikiloc.com/wikiloc/find.do", {})
    assert seen == [("SESSION=abc", "wikiloc-app/1.0"), (None, "wikiloc-app/1.0")]



async def test_make_fetch_gives_up_after_retries():
    transport = httpx.MockTransport(lambda request: httpx.Response(403))

    async def sleep(seconds):
        pass

    fetch = make_fetch(transport=transport, sleep=sleep, retries=2)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}) is None


async def test_make_fetch_keeps_cookie_out_of_stderr_and_does_not_retry_bad_header(capsys):
    attempts = []

    async def no_sleep(_):
        attempts.append(1)

    def handler(request):  # what h11 raises for a header value containing a newline
        raise httpx.LocalProtocolError(f"Illegal header value {request.headers['cookie']!r}")

    fetch = make_fetch(transport=httpx.MockTransport(handler), sleep=no_sleep)
    assert await fetch("https://es.wikiloc.com/wikiloc/find.do", {}, {"Cookie": "a=SECRETX b=1"}) is None
    assert "SECRETX" not in capsys.readouterr().err
    assert attempts == []  # returned on the first attempt, no backoff sleep
