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


# ---- locate (navigation only) ----

HIT = {"display_name": "Μακρινίτσα, Δήμος Βόλου, Περιφερειακή Ενότητα Μαγνησίας, Θεσσαλία, Ελλάδα",
       "lat": "39.3925", "lon": "22.9926", "boundingbox": ["39.38", "39.40", "22.98", "23.00"]}


async def test_locate_returns_place_with_unwidened_bbox(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [HIT]

    place = await Geocoder(DiskCache(tmp_path), fetch=fetch, min_radius_km=15).locate("Μακρινίτσα")
    assert place.name == "Μακρινίτσα, Δήμος Βόλου"
    assert (place.lat, place.lon) == (39.3925, 22.9926)
    assert (place.bbox.south, place.bbox.north, place.bbox.west, place.bbox.east) == (39.38, 39.40, 22.98, 23.00)
    p = calls[0]
    assert p["q"] == "Μακρινίτσα" and p["countrycodes"] == "gr" and p["limit"] == 1 and p["format"] == "json"
    assert p["addressdetails"] == 0 and p["accept-language"] == "el,en"


async def test_locate_english_input_and_cache_hit(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [HIT]

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch)
    first = await geocoder.locate("Makrinitsa")
    again = await geocoder.locate("  MAKRINITSA ")
    assert again == first and len(calls) == 1 and calls[0]["q"] == "Makrinitsa"


async def test_locate_cache_is_separate_from_geocode(tmp_path):
    async def fetch(url, params):
        return [HIT]

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch, min_radius_km=15)
    box = await geocoder.geocode("Makrinitsa")
    place = await geocoder.locate("Makrinitsa")
    assert place.bbox != box  # geocode widened it, locate did not


async def test_locate_not_found_is_none_and_not_cached(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return []

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch)
    assert await geocoder.locate("Atlantis") is None
    assert await geocoder.locate("Atlantis") is None and len(calls) == 2
