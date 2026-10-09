from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import StayQuery
from app.sources.stays.airbnb import AirbnbParseError, AirbnbProvider, parse_details, parse_search
from app.sources.stays.base import ProviderUnavailable
from tests.builders import airbnb_listing_html, airbnb_result, airbnb_search_html

Q = StayQuery(lat=39.40, lon=23.03, radius_km=15, check_in=date(2026, 10, 23),
              check_out=date(2026, 10, 25), guests=2, dog=True)


def test_parse_search_maps_fields():
    html = airbnb_search_html([airbnb_result(898234413854573103, "Rustic Stone House", 39.4016, 22.985,
                                             label="€1,202 total")])
    [stay] = parse_search(html)
    assert stay.id == "airbnb:898234413854573103"
    assert stay.source == "airbnb"
    assert stay.name == "Rustic Stone House"
    assert stay.url == "https://www.airbnb.com/rooms/898234413854573103"
    assert (stay.lat, stay.lon) == (39.4016, 22.985)
    assert stay.total_price == 1202.0
    assert stay.currency == "EUR"
    assert stay.rating == 4.85
    assert stay.photo == "https://a0.muscache.com/898234413854573103.jpg"


def test_parse_search_takes_discounted_price_and_handles_new_listing():
    html = airbnb_search_html([airbnb_result(1, "A", 39.4, 23.0, label="originally €250, now €202 total",
                                             rating="New place to stay")])
    [stay] = parse_search(html)
    assert stay.total_price == 202.0
    assert stay.rating is None


def test_parse_search_raises_without_state_script():
    with pytest.raises(AirbnbParseError):
        parse_search("<html>captcha</html>")


@pytest.mark.parametrize("titles,expected", [
    (["Private backyard – Fully fenced", "Pets allowed"], (True, True)),
    (["Garden view", "Wifi"], (None, True)),
    (["Backyard", "No pets"], (False, True)),
    (["Wifi", "Pet-friendly vacation rentals in Greece"], (None, False)),
])
def test_parse_details(titles, expected):
    assert parse_details(airbnb_listing_html(titles)) == expected


class FakeFetch:
    def __init__(self, search_with_pets, search_without, details):
        self.search_with_pets = search_with_pets
        self.search_without = search_without
        self.details = details
        self.calls = []

    async def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if url.endswith("/s/homes"):
            return self.search_with_pets if params.get("pets") == 1 else self.search_without
        listing_id = int(url.rsplit("/", 1)[1])
        return self.details[listing_id]


async def test_dog_search_marks_pets_and_finds_garden_only_listings():
    fetch = FakeFetch(
        search_with_pets=airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)]),
        search_without=airbnb_search_html([
            airbnb_result(1, "Pet house", 39.4, 23.0),
            airbnb_result(2, "Garden house", 39.41, 23.01),
            airbnb_result(3, "Plain flat", 39.42, 23.02),
        ]),
        details={
            1: airbnb_listing_html(["Backyard", "Pets allowed"]),
            2: airbnb_listing_html(["Garden"]),
            3: airbnb_listing_html(["Wifi"]),
        },
    )
    stays = await AirbnbProvider(fetch=fetch, detail_top_n=10).search(Q)
    by_id = {s.id: s for s in stays}
    assert [s.id for s in stays] == ["airbnb:1", "airbnb:2", "airbnb:3"]
    assert (by_id["airbnb:1"].pets_allowed, by_id["airbnb:1"].has_garden) == (True, True)
    assert (by_id["airbnb:2"].pets_allowed, by_id["airbnb:2"].has_garden) == (None, True)
    assert (by_id["airbnb:3"].pets_allowed, by_id["airbnb:3"].has_garden) == (None, False)
    query = parse_qs(urlparse(by_id["airbnb:1"].url).query)
    assert query["check_in"] == ["2026-10-23"] and query["pets"] == ["1"]
    search_params = fetch.calls[0][1]
    assert search_params["checkin"] == "2026-10-23" and search_params["adults"] == 2
    assert search_params["sw_lat"] < 39.40 < search_params["ne_lat"]


async def test_no_dog_search_is_single_request_without_details():
    fetch = FakeFetch(search_with_pets="", search_without=airbnb_search_html(
        [airbnb_result(5, "Flat", 39.4, 23.0)]), details={})
    stays = await AirbnbProvider(fetch=fetch).search(Q.model_copy(update={"dog": False}))
    assert [s.id for s in stays] == ["airbnb:5"]
    assert len(fetch.calls) == 1 and "pets" not in fetch.calls[0][1]


async def test_detail_failure_is_ignored():
    class Failing(FakeFetch):
        async def __call__(self, url, params):
            if "/rooms/" in url:
                raise RuntimeError("blocked")
            return await super().__call__(url, params)

    fetch = Failing(airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)]),
                    airbnb_search_html([]), {})
    [stay] = await AirbnbProvider(fetch=fetch).search(Q)
    assert stay.pets_allowed is True and stay.has_garden is None


async def test_blocked_search_raises_provider_unavailable():
    async def fetch(url, params):
        return "<html>captcha</html>"

    with pytest.raises(ProviderUnavailable, match="Airbnb unavailable"):
        await AirbnbProvider(fetch=fetch).search(Q)


async def test_dog_search_keeps_pet_results_when_no_pets_search_fails():
    pet_html = airbnb_search_html([airbnb_result(1, "Pet house", 39.4, 23.0)])

    async def fetch(url, params):
        if "/rooms/" in url:
            return airbnb_listing_html(["Pets allowed"])
        return pet_html if params.get("pets") else "<html>captcha</html>"

    stays = await AirbnbProvider(fetch=fetch).search(Q)
    assert [s.id for s in stays] == ["airbnb:1"] and stays[0].pets_allowed is True
