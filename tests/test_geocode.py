import pytest

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
    assert p["q"] == "Μακρινίτσα" and p["countrycodes"] == "gr" and p["limit"] == 10 and p["format"] == "json"
    assert p["addressdetails"] == 0 and p["accept-language"] == "el"


async def test_locate_english_input_and_cache_hit(tmp_path):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [HIT]

    geocoder = Geocoder(DiskCache(tmp_path), fetch=fetch)
    first = await geocoder.locate("Makrinitsa")
    again = await geocoder.locate("  MAKRINITSA ")
    assert again == first and len(calls) == 1 and calls[0]["q"] == "Makrinitsa"
    assert calls[0]["accept-language"] == "en"


@pytest.mark.parametrize("text,lang", [("Athens", "en"), ("Αθήνα", "el"), ("Mount Όλυμπος", "el"),
                                       ("ἀθῆναι", "el"), ("Zagori 2", "en")])
async def test_locate_language_follows_the_script(tmp_path, text, lang):
    calls = []

    async def fetch(url, params):
        calls.append(params)
        return [HIT]

    await Geocoder(DiskCache(tmp_path), fetch=fetch).locate(text)
    assert calls[0]["accept-language"] == lang


async def test_locate_cache_key_includes_language(tmp_path):
    cache = DiskCache(tmp_path)

    async def fetch(url, params):
        return [HIT]

    geocoder = Geocoder(cache, fetch=fetch)
    await geocoder.locate("Athens")
    assert cache.get("locate3:en:athens") is not None
    assert cache.get("locate3:el:athens") is None


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


def _cand(name, cls, typ, importance=None, lat="39.0", lon="21.0"):
    c = {"display_name": f"{name}, Ελλάδα", "lat": lat, "lon": lon, "class": cls, "type": typ,
         "boundingbox": ["38.9", "39.1", "20.9", "21.1"]}
    if importance is not None:
        c["importance"] = importance
    return c


async def _locate(tmp_path, data, text="Ζαγόρι"):
    async def fetch(url, params):
        return data

    return await Geocoder(DiskCache(tmp_path), fetch=fetch).locate(text)


async def test_locate_prefers_geographic_over_first_poi(tmp_path):
    data = [_cand("Φυσικό Νερό Ζαγόρι", "craft", "bottling", 0.9),
            _cand("Ζαγόρι", "boundary", "administrative", 0.4)]
    assert (await _locate(tmp_path, data)).name == "Ζαγόρι, Ελλάδα"


async def test_locate_highest_importance_among_geographic(tmp_path):
    data = [_cand("Low", "place", "village", 0.2), _cand("High", "boundary", "administrative", 0.7),
            _cand("Mid", "natural", "peak", 0.5), _cand("Nameless", "place", "hamlet")]
    assert (await _locate(tmp_path, data)).name == "High, Ελλάδα"


async def test_locate_only_pois_returns_first_poi(tmp_path):
    data = [_cand("Shop", "shop", "kiosk", 0.1), _cand("Hotel", "tourism", "hotel", 0.9)]
    assert (await _locate(tmp_path, data)).name == "Shop, Ελλάδα"


async def test_locate_sends_limit_10(tmp_path):
    seen = []

    async def fetch(url, params):
        seen.append(params)
        return [HIT]

    await Geocoder(DiskCache(tmp_path), fetch=fetch).locate("x")
    assert seen[0]["limit"] == 10


def _box(c, south, north, west, east):
    c["boundingbox"] = [str(south), str(north), str(west), str(east)]
    return c


async def test_locate_higher_importance_region_beats_same_named_village(tmp_path):
    data = [_box(_cand("Village", "place", "village", 0.30), 39.0, 39.01, 21.0, 21.01),
            _box(_cand("Range", "natural", "mountain_range", 0.60), 38.0, 40.0, 20.0, 22.0)]
    assert (await _locate(tmp_path, data)).name == "Range, Ελλάδα"


async def test_locate_near_tied_importance_prefers_larger_bbox(tmp_path):
    data = [_box(_cand("Small", "place", "village", 0.50), 39.0, 39.01, 21.0, 21.01),
            _box(_cand("Big", "boundary", "administrative", 0.49), 38.0, 40.0, 20.0, 22.0)]
    assert (await _locate(tmp_path, data)).name == "Big, Ελλάδα"
