from app.cache import DiskCache
from app.geo import haversine_km
from app.geocode import Geocoder
from app.models import LatLon


async def test_geocode_expands_small_box_and_caches(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [{"boundingbox": ["39.38", "39.40", "22.98", "23.00"]}]

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch, min_radius_km=15)
    box = await geocoder.geocode("Makrinitsa")
    assert calls[0]["countrycodes"] == "gr" and calls[0]["q"] == "Makrinitsa"
    assert haversine_km(LatLon(lat=39.39, lon=22.99), LatLon(lat=box.north, lon=22.99)) >= 14.5
    again = await geocoder.geocode("  makrinitsa ")
    assert again == box and len(calls) == 1


async def test_geocode_unknown_place_returns_none(tmp_path):
    async def fetch(url, params):
        return []

    assert await Geocoder(DiskCache(tmp_path), fetch=fetch).geocode("Atlantis") is None
