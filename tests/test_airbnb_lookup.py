from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.sources.stays.airbnb import (
    AirbnbParseError, AirbnbProvider, ListingNotFound, parse_room, room_id_from_url,
)
from app.sources.stays.base import ProviderUnavailable
from tests.builders import airbnb_result, airbnb_room_html, airbnb_search_html

USER_LINK = ("https://www.airbnb.com/rooms/576031322358400592?adults=4&check_in=2026-11-06&check_out=2026-11-08"
             "&pets=1&search_mode=regular_search&source_impression_id=p3_1791658&previous_page_section_name=1000")
FRI, SUN = date(2026, 10, 23), date(2026, 10, 25)
ID = "576031322358400592"


@pytest.mark.parametrize("url,expected", [
    (USER_LINK, ID),
    ("https://www.airbnb.gr/rooms/123?x=1", "123"),
    ("https://airbnb.co.uk/rooms/plus/456", "456"),
    ("https://www.airbnb.com/rooms/plus/456", "456"),
    ("http://airbnb.com/rooms/77/", "77"),
    ("  https://www.airbnb.com.au/rooms/88  ", "88"),
    ("789", "789"),
    ("https://evil.com/rooms/1", None),
    ("https://www.airbnb.com/s/homes", None),
    ("https://www.airbnb.com/rooms/abc", None),
    ("javascript:x", None),
    ("", None),
    ("https://airbnb.com.evil.com/rooms/1", None),
    ("https://evilairbnb.com/rooms/1", None),
    ("ftp://www.airbnb.com/rooms/1", None),
])
def test_room_id_from_url(url, expected):
    assert room_id_from_url(url) == expected


def test_parse_room_reads_json_ld():
    stay = parse_room(airbnb_room_html(), ID)
    assert stay.id == f"airbnb:{ID}" and stay.source == "airbnb"
    assert stay.name == "Bezeniko Amazing Arcadian Property!"
    assert stay.url == f"https://www.airbnb.com/rooms/{ID}"
    assert (stay.lat, stay.lon) == (37.71914, 22.25123)
    assert stay.rating == 4.96
    assert stay.photo == "https://a0.muscache.com/im/pictures/room-1.jpeg"
    assert stay.total_price is None


def test_parse_room_tolerates_graph_and_string_image():
    html = ('<script type="application/ld+json">{"@graph": [{"@type": "Product"}, {"@type": "VacationRental",'
            ' "name": "G", "image": "https://x/p.jpg", "latitude": "37.5", "longitude": 22.1}]}</script>'
            '<script type="application/ld+json">not json</script>')
    stay = parse_room(html, "1")
    assert (stay.name, stay.photo, stay.lat, stay.lon, stay.rating) == ("G", "https://x/p.jpg", 37.5, 22.1, None)


def test_parse_room_falls_back_to_og_tags_for_name_and_photo():
    html = ('<meta property="og:title" content="Cottage in Vlacherna &amp; more"/>'
            '<meta property="og:image" content="https://x/og.jpg?a=1&amp;b=2"/>'
            '<script type="application/ld+json">{"latitude": 37.7, "longitude": 22.2}</script>')
    stay = parse_room(html, "1")
    assert stay.name == "Cottage in Vlacherna & more"
    assert stay.photo == "https://x/og.jpg?a=1&b=2"


def test_parse_room_without_coordinates_raises():
    with pytest.raises(AirbnbParseError):
        parse_room(airbnb_room_html(ld=False), ID)
    with pytest.raises(AirbnbParseError):
        parse_room(airbnb_room_html(lat=None), ID)


class Fetch:
    """Fake fetch: the room page plus search pages with / without pets."""

    def __init__(self, room=None, with_pets=(), without=(), room_error=None):
        self.room = room if room is not None else airbnb_room_html(titles=["Backyard"])
        self.with_pets, self.without, self.room_error = with_pets, without, room_error
        self.calls = []

    async def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if "/rooms/" in url:
            if self.room_error:
                raise self.room_error
            return self.room
        ids = self.with_pets if params.get("pets") == 1 else self.without
        return airbnb_search_html([airbnb_result(i, f"L{i}", 37.719, 22.251, label="€410 total") for i in ids])

    def searches(self):
        return [p for u, p in self.calls if u.endswith("/s/homes")]


async def test_lookup_price_from_pets_search_marks_pets_allowed():
    fetch = Fetch(with_pets=[1, int(ID)])
    stay = await AirbnbProvider(fetch=fetch).lookup(ID, FRI, SUN, 4, True)
    assert (stay.total_price, stay.currency, stay.pets_allowed, stay.has_garden) == (410.0, "EUR", True, True)
    [search] = fetch.searches()
    assert search["pets"] == 1 and search["checkin"] == "2026-10-23" and search["adults"] == 4
    assert search["sw_lat"] < 37.71914 < search["ne_lat"] and search["ne_lat"] - search["sw_lat"] < 0.05
    assert search["sw_lng"] < 22.25123 < search["ne_lng"]
    query = parse_qs(urlparse(stay.url).query)
    assert stay.url.startswith(f"https://www.airbnb.com/rooms/{ID}?")
    assert query == {"check_in": ["2026-10-23"], "check_out": ["2026-10-25"], "adults": ["4"], "pets": ["1"]}


async def test_lookup_price_found_only_without_pets_keeps_page_pets_hint():
    fetch = Fetch(room=airbnb_room_html(titles=["Wifi"]), with_pets=[1], without=[int(ID)])
    stay = await AirbnbProvider(fetch=fetch).lookup(ID, FRI, SUN, 2, True)
    assert stay.total_price == 410.0 and stay.pets_allowed is None and stay.has_garden is False
    assert [s.get("pets") for s in fetch.searches()] == [1, None]


async def test_lookup_not_found_in_search_has_no_price():
    fetch = Fetch(with_pets=[1], without=[2])
    stay = await AirbnbProvider(fetch=fetch).lookup(ID, FRI, SUN, 2, True)
    assert stay.total_price is None and stay.currency is None
    assert len(fetch.searches()) == 2


async def test_lookup_without_dog_runs_one_search_without_pets():
    fetch = Fetch(room=airbnb_room_html(titles=["Pets allowed"]), without=[int(ID)])
    stay = await AirbnbProvider(fetch=fetch).lookup(ID, FRI, SUN, 2, False)
    assert stay.total_price == 410.0 and stay.pets_allowed is True
    [search] = fetch.searches()
    assert "pets" not in search
    assert "pets" not in parse_qs(urlparse(stay.url).query)


async def test_lookup_without_dates_makes_no_search():
    fetch = Fetch()
    stay = await AirbnbProvider(fetch=fetch).lookup(ID, None, None, 2, True)
    assert fetch.searches() == [] and len(fetch.calls) == 1
    assert stay.total_price is None and stay.url == f"https://www.airbnb.com/rooms/{ID}"


async def test_lookup_search_failure_keeps_listing_without_price():
    class Blocked(Fetch):
        async def __call__(self, url, params):
            if url.endswith("/s/homes"):
                self.calls.append((url, dict(params)))
                return "<html>captcha</html>"
            return await super().__call__(url, params)

    stay = await AirbnbProvider(fetch=Blocked()).lookup(ID, FRI, SUN, 2, True)
    assert stay.name == "Bezeniko Amazing Arcadian Property!" and stay.total_price is None


def _status_error(code):
    request = httpx.Request("GET", "https://www.airbnb.com/rooms/1")
    return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))


async def test_lookup_404_raises_listing_not_found():
    with pytest.raises(ListingNotFound):
        await AirbnbProvider(fetch=Fetch(room_error=_status_error(404))).lookup(ID, None, None, 2, True)


@pytest.mark.parametrize("error", [_status_error(503), httpx.ConnectError("down")])
async def test_lookup_http_errors_raise_provider_unavailable(error):
    with pytest.raises(ProviderUnavailable, match="Airbnb didn't answer – try again in a minute"):
        await AirbnbProvider(fetch=Fetch(room_error=error)).lookup(ID, None, None, 2, True)


async def test_lookup_unparsable_page_raises_parse_error():
    with pytest.raises(AirbnbParseError):
        await AirbnbProvider(fetch=Fetch(room="<html>captcha</html>")).lookup(ID, None, None, 2, True)
