from datetime import date

import pytest

from app.cache import DiskCache
from app.config import Settings
from app.engine import NO_DATES, PRICE_NOT_FOUND, Engine
from app.models import Stay
from app.sources.stays.airbnb import ListingNotFound
from app.sources.stays.base import ProviderUnavailable
from app.sources.stays.booking import HotelNotFound, HotelRef
from tests.test_engine import FakeGeocoder, FakeRouter, FakeWikiloc

FRI, SUN = date(2026, 10, 23), date(2026, 10, 25)


class FakeAirbnb:
    name = "Airbnb"

    def __init__(self, price=410.0, pets=None, garden=None, error=None):
        self.price, self.pets, self.garden, self.error = price, pets, garden, error
        self.calls = []

    async def search(self, q):
        return []

    async def lookup(self, room_id, check_in, check_out, guests, dog):
        self.calls.append((room_id, check_in, check_out, guests, dog))
        if self.error:
            raise self.error
        return Stay(id=f"airbnb:{room_id}", source="airbnb", name="Bezeniko", url="u", lat=37.7, lon=22.2,
                    total_price=self.price if check_in else None, currency="EUR" if check_in else None,
                    pets_allowed=self.pets, has_garden=self.garden)


class FakeBooking:
    name = "Booking.com"

    def __init__(self, price=875.0, pets=None, error=None):
        self.price, self.pets, self.error = price, pets, error
        self.calls = []

    async def search(self, q):
        return []

    async def lookup(self, ref, check_in, check_out, guests):
        self.calls.append((ref, check_in, check_out, guests))
        if self.error:
            raise self.error
        return Stay(id=f"booking:{ref.key}", source="booking", name="Aenaon Guesthouse", url="u", lat=38.0,
                    lon=22.47, total_price=self.price if check_in else None, currency="EUR" if check_in else None,
                    pets_allowed=self.pets)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, cache_dir=tmp_path)


def make(settings, airbnb):
    return Engine(settings, FakeWikiloc(), [FakeBooking(), airbnb], FakeRouter({}), FakeGeocoder(),
                  DiskCache(settings.cache_dir))


async def test_lookup_returns_stay_with_badge_and_registers_it(settings):
    airbnb = FakeAirbnb(pets=True, garden=True)
    engine = make(settings, airbnb)
    result = await engine.lookup_airbnb("123", FRI, SUN, 4, True)
    assert result.stay.id == "airbnb:123" and result.stay.total_price == 410.0
    assert result.stay.badge == "pets_garden" and result.warnings == []
    assert airbnb.calls == [("123", FRI, SUN, 4, True)]
    assert engine.lookup("airbnb:123").name == "Bezeniko"


async def test_lookup_keeps_listing_that_refuses_pets_without_badge(settings):
    # Unlike a search, a pasted listing is never dropped by the dog rule.
    result = await make(settings, FakeAirbnb(pets=False, garden=True)).lookup_airbnb("1", FRI, SUN, 2, True)
    assert result.stay.badge is None
    result = await make(settings, FakeAirbnb(pets=None, garden=True)).lookup_airbnb("2", FRI, SUN, 2, False)
    assert result.stay.badge == "garden"


async def test_same_day_trip_books_one_night(settings):
    airbnb = FakeAirbnb()
    await make(settings, airbnb).lookup_airbnb("1", FRI, FRI, 2, True)
    assert airbnb.calls[0][1:3] == (FRI, date(2026, 10, 24))


async def test_lookup_warnings(settings):
    result = await make(settings, FakeAirbnb(price=None)).lookup_airbnb("1", FRI, SUN, 2, True)
    assert result.warnings == [PRICE_NOT_FOUND] == ["Price not found for these dates – you can type it in"]
    airbnb = FakeAirbnb()
    result = await make(settings, airbnb).lookup_airbnb("1", None, None, 2, True)
    assert result.warnings == [NO_DATES] == ["No dates set – price not looked up"]
    assert airbnb.calls[0][1:3] == (None, None)
    await make(settings, airbnb).lookup_airbnb("2", FRI, None, 2, True)  # one date alone counts as none
    assert airbnb.calls[1][1:3] == (None, None)


async def test_second_identical_lookup_is_cached(settings):
    airbnb = FakeAirbnb()
    engine = make(settings, airbnb)
    first = await engine.lookup_airbnb("1", FRI, SUN, 2, True)
    second = await engine.lookup_airbnb("1", FRI, SUN, 2, True)
    assert second == first and len(airbnb.calls) == 1
    await engine.lookup_airbnb("1", FRI, SUN, 3, True)  # guests are part of the key
    await engine.lookup_airbnb("1", FRI, SUN, 2, False)  # and so is the dog
    assert len(airbnb.calls) == 3


@pytest.mark.parametrize("error", [ListingNotFound("1"), ProviderUnavailable("Airbnb didn't answer")])
async def test_lookup_errors_propagate_and_are_not_cached(settings, error):
    airbnb = FakeAirbnb(error=error)
    engine = make(settings, airbnb)
    for _ in range(2):
        with pytest.raises(type(error)):
            await engine.lookup_airbnb("1", FRI, SUN, 2, True)
    assert len(airbnb.calls) == 2


async def test_no_airbnb_provider_is_unavailable(settings):
    engine = Engine(settings, FakeWikiloc(), [FakeBooking()], FakeRouter({}), FakeGeocoder(),
                    DiskCache(settings.cache_dir))
    with pytest.raises(ProviderUnavailable):
        await engine.lookup_airbnb("1", FRI, SUN, 2, True)


REF = HotelRef(page="aenaon-guesthouse", hotel_id="5546346")


def make_booking(settings, booking):
    return Engine(settings, FakeWikiloc(), [booking, FakeAirbnb()], FakeRouter({}), FakeGeocoder(),
                  DiskCache(settings.cache_dir))


async def test_booking_lookup_badges_registers_and_uses_trip_window(settings):
    booking = FakeBooking(pets=True)
    engine = make_booking(settings, booking)
    result = await engine.lookup_booking(REF, FRI, SUN, 6, True)
    assert result.stay.id == "booking:5546346" and result.stay.total_price == 875.0
    assert result.stay.badge == "pets" and result.warnings == []
    assert booking.calls == [(REF, FRI, SUN, 6)]
    assert engine.lookup("booking:5546346").name == "Aenaon Guesthouse"


async def test_booking_lookup_warnings_and_cache(settings):
    booking = FakeBooking(price=None)
    engine = make_booking(settings, booking)
    assert (await engine.lookup_booking(REF, FRI, SUN, 2, True)).warnings == [PRICE_NOT_FOUND]
    assert (await engine.lookup_booking(REF, None, None, 2, True)).warnings == [NO_DATES]
    await engine.lookup_booking(REF, None, None, 2, True)
    assert len(booking.calls) == 2  # the second no-dates lookup came from the cache


@pytest.mark.parametrize("error", [HotelNotFound("x"), ProviderUnavailable("blocked")])
async def test_booking_lookup_errors_are_not_cached(settings, error):
    booking = FakeBooking(error=error)
    engine = make_booking(settings, booking)
    for _ in range(2):
        with pytest.raises(type(error)):
            await engine.lookup_booking(REF, FRI, SUN, 2, True)
    assert len(booking.calls) == 2


async def test_no_booking_provider_is_unavailable(settings):
    engine = Engine(settings, FakeWikiloc(), [FakeAirbnb()], FakeRouter({}), FakeGeocoder(),
                    DiskCache(settings.cache_dir))
    with pytest.raises(ProviderUnavailable):
        await engine.lookup_booking(REF, FRI, SUN, 2, True)
