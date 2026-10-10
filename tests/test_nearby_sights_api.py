"""POST /api/saved-trips/{id}/nearby-sights: the real Engine with a fake NatureSource (no network)."""

import pytest
from fastapi.testclient import TestClient

from app.cache import DiskCache
from app.config import Settings
from app.engine import NATURE_GREECE_ONLY, NATURE_UNAVAILABLE, NEARBY_MAX_TILES, NEARBY_SPREAD, Engine
from app.main import create_app
from app.models import Hike, Sight, Stay
from app.sources.nature import NatureUnavailable, Tile

KM_LAT = 1 / 111.32  # degrees of latitude per km
HIKE = (37.6, 22.2)
STAY = (37.6, 22.54)  # ~30 km east of the hike


class FakeNature:
    budget = None

    def __init__(self, tiles=None, fail=()):
        self.tiles = tiles or {}
        self.fail = set(fail)
        self.calls = []

    async def fetch_tile(self, south, west):
        self.calls.append((south, west))
        if (south, west) in self.fail:
            raise NatureUnavailable("busy")
        return Tile(self.tiles.get((south, west), []), {})


def sight(id_, lat, lon, name=None, kind="waterfall", lines=None):
    return Sight(id=f"osm:node/{id_}", name=name or f"Sight {id_}", kind=kind, lat=lat, lon=lon,
                 url=f"https://www.openstreetmap.org/node/{id_}", lines=lines or [])


def hike(i, lat, lon):
    return Hike(id=f"wikiloc:{i}", name=f"Hike {i}", url="https://es.wikiloc.com/x", lat=lat, lon=lon).model_dump()


def stay(i, lat, lon):
    return Stay(id=f"airbnb:{i}", source="airbnb", name=f"Stay {i}", url="https://www.airbnb.com/rooms/1",
                lat=lat, lon=lon).model_dump()


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, cache_dir=tmp_path / "cache", trips_db=tmp_path / "trips.db")


def make_client(settings, nature):
    engine = Engine(settings, None, [], None, None, DiskCache(settings.cache_dir), nature=nature)
    return TestClient(create_app(engine=engine, ask_runner=object(), settings=settings))


def new_trip(c, **fields):
    r = c.post("/api/saved-trips", json={"name": "T", **fields})
    assert r.status_code == 201
    return r.json()


def area_tiles():
    near_hike_3 = sight(1, HIKE[0] + 3 * KM_LAT, HIKE[1])  # 3 km north of the hike
    diag_6 = sight(2, HIKE[0] + 4.3 * KM_LAT, HIKE[1] + 0.049)  # ~6 km away, but inside the hike's box
    unnamed = sight(3, HIKE[0] + KM_LAT, HIKE[1], name="Waterfall")
    present = sight(4, HIKE[0] + KM_LAT, HIKE[1] + 0.01)
    near_stay_2 = sight(5, STAY[0] + 2 * KM_LAT, STAY[1])
    # A river whose centre is far away but whose line passes ~1 km south of the stay.
    river = sight(6, STAY[0] - 0.3, STAY[1], name="Ladon", kind="river",
                  lines=[[[STAY[0] - KM_LAT, STAY[1] - 0.2], [STAY[0] - KM_LAT, STAY[1] + 0.2]]])
    return {(37.5, 22.0): [near_hike_3, diag_6, unnamed, present], (37.5, 22.5): [near_stay_2, river]}


def test_nearby_sights_adds_named_sights_within_radius_sorted(settings):
    nature = FakeNature(area_tiles())
    c = make_client(settings, nature)
    trip = new_trip(c, hikes=[hike(1, *HIKE)], stays=[stay(1, *STAY)],
                    sights=[sight(4, HIKE[0] + KM_LAT, HIKE[1] + 0.01).model_dump()])
    r = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights")
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(set(nature.calls)) == [(37.5, 22.0), (37.5, 22.25), (37.5, 22.5)]  # both areas
    assert [s["id"] for s in body["trip"]["sights"]] == ["osm:node/4", "osm:node/6", "osm:node/5", "osm:node/1"]
    assert body["added"] == 3 and body["found"] == 4 and body["warnings"] == []
    assert all(s["lines"] == [] for s in body["trip"]["sights"])
    assert c.get(f"/api/saved-trips/{trip['id']}").json() == body["trip"]
    assert body["trip"]["updated_at"] != trip["updated_at"]


def test_nearby_sights_radius_param(settings):
    c = make_client(settings, FakeNature(area_tiles()))
    trip = new_trip(c, hikes=[hike(1, *HIKE)])
    r = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights", json={"radius_km": 10})
    assert [s["id"] for s in r.json()["trip"]["sights"]] == ["osm:node/4", "osm:node/1", "osm:node/2"]
    for bad in (0.5, 11, "x"):
        assert c.post(f"/api/saved-trips/{trip['id']}/nearby-sights", json={"radius_km": bad}).status_code == 422


def test_nearby_sights_nothing_new_keeps_trip(settings):
    c = make_client(settings, FakeNature())
    trip = new_trip(c, hikes=[hike(1, *HIKE)])
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert body["added"] == 0 and body["found"] == 0 and body["trip"]["updated_at"] == trip["updated_at"]


def test_nearby_sights_404_and_422(settings):
    nature = FakeNature()
    c = make_client(settings, nature)
    r = c.post("/api/saved-trips/missing/nearby-sights")
    assert r.status_code == 404 and r.json()["detail"] == "Trip not found"
    trip = new_trip(c)
    r = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights")
    assert r.status_code == 422 and r.json()["detail"] == "Add a hike or stay first"
    assert nature.calls == []


def test_nearby_sights_passes_nature_warnings_through(settings):
    nature = FakeNature(area_tiles(), fail={(37.5, 22.5)})
    c = make_client(settings, nature)
    trip = new_trip(c, hikes=[hike(1, *HIKE)], stays=[stay(1, *STAY)])
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert body["warnings"] == [NATURE_UNAVAILABLE]
    assert [s["id"] for s in body["trip"]["sights"]] == ["osm:node/4", "osm:node/1"]


def test_nearby_sights_outside_greece(settings):
    nature = FakeNature()
    c = make_client(settings, nature)
    trip = new_trip(c, hikes=[hike(1, 48.15, 11.55)])  # Munich
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert body["added"] == 0 and body["warnings"] == [NATURE_GREECE_ONLY] and nature.calls == []


def test_nearby_sights_tile_cap_searches_near_first_places(settings):
    nature = FakeNature()
    c = make_client(settings, nature)
    # 40 places on a 0.5° grid, each on a tile corner (4 tiles each = 160 tiles); the first hike is in the middle.
    places = [(37.0 + 0.5 * r, 21.0 + 0.5 * k) for r in range(5) for k in range(8)]
    places.remove((38.0, 23.0))
    trip = new_trip(c, hikes=[hike(0, 38.0, 23.0)] + [hike(i + 1, *p) for i, p in enumerate(places[:30])],
                    stays=[stay(i, *p) for i, p in enumerate(places[30:])])
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert NEARBY_SPREAD in body["warnings"]
    assert NEARBY_SPREAD == "Trip is spread out – searched near the first places only"
    called = set(nature.calls)
    assert 40 <= len(called) <= NEARBY_MAX_TILES == 48 and len(nature.calls) == len(called)
    assert {(37.75, 22.75), (37.75, 23.0), (38.0, 22.75), (38.0, 23.0)} <= called  # around the first hike
    assert (36.75, 20.75) not in called and (38.75, 24.25) not in called  # the far corners were left out


def test_nearby_sights_cap_warning(settings):
    c = make_client(settings, FakeNature(area_tiles()))
    full = [sight(100 + i, 38.0, 22.0).model_dump() for i in range(99)]
    trip = new_trip(c, hikes=[hike(1, *HIKE)], sights=full)
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert body["added"] == 1 and len(body["trip"]["sights"]) == 100
    assert body["warnings"] == ["Trip already holds 100 nature sights"]


# --- fix round: inside areas, limits, deadline --------------------------------------------------------


def square(lat, lon, half_lat, half_lon, closed=True):
    ring = [[lat - half_lat, lon - half_lon], [lat - half_lat, lon + half_lon], [lat + half_lat, lon + half_lon],
            [lat + half_lat, lon - half_lon]]
    return [ring + [ring[0]]] if closed else [ring]


def test_nearby_sights_point_inside_big_area_is_kept(settings):
    # Each park is ~30 km wide; its edges and centre point are far from the hike.
    inside = sight(10, HIKE[0] + 0.3, HIKE[1], name="Big park", kind="protected_area",
                   lines=square(HIKE[0], HIKE[1], 0.15, 0.2))
    outside = sight(11, HIKE[0] + 0.3, HIKE[1], name="Other park", kind="protected_area",
                    lines=square(HIKE[0], HIKE[1] + 0.51, 0.15, 0.2))  # hike ~10 km west of its edge
    open_forest = sight(12, HIKE[0] + 0.3, HIKE[1], name="Clipped forest", kind="forest",
                        lines=square(HIKE[0], HIKE[1], 0.15, 0.2, closed=False))  # no closed ring: bbox fallback
    c = make_client(settings, FakeNature({(37.5, 22.0): [inside, outside, open_forest]}))
    trip = new_trip(c, hikes=[hike(1, *HIKE)])
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert sorted(s["id"] for s in body["trip"]["sights"]) == ["osm:node/10", "osm:node/12"]


def test_nearby_sights_full_trip_skips_fetching(settings):
    nature = FakeNature(area_tiles())
    c = make_client(settings, nature)
    trip = new_trip(c, hikes=[hike(1, *HIKE)], sights=[sight(100 + i, 38.0, 22.0).model_dump() for i in range(100)])
    body = c.post(f"/api/saved-trips/{trip['id']}/nearby-sights").json()
    assert body["added"] == 0 and body["found"] == 0 and nature.calls == []
    assert body["warnings"] == ["Trip already holds 100 nature sights"]


def test_nearby_sights_limits_429(settings):
    engine = Engine(settings, None, [], None, None, DiskCache(settings.cache_dir), nature=FakeNature())
    app = create_app(engine=engine, ask_runner=object(), settings=settings)
    c = TestClient(app)
    limits = app.state.nearby
    now = {"t": 1000.0}
    limits.clock = lambda: now["t"]
    a, b, d = (new_trip(c, hikes=[hike(1, *HIKE)]) for _ in range(3))

    def url(t):
        return f"/api/saved-trips/{t['id']}/nearby-sights"

    assert c.post(url(a)).status_code == 200
    r = c.post(url(a))  # within 30 s of the last search of this trip
    assert r.status_code == 429 and r.json()["detail"] == "Wait a moment before searching this trip again"
    now["t"] += 31
    assert c.post(url(a)).status_code == 200

    limits.running.add(b["id"])  # a search for b is in flight
    r = c.post(url(b))
    assert r.status_code == 429 and r.json()["detail"] == "A search for this trip is already running"
    limits.running.add("other")  # two searches in flight overall
    r = c.post(url(d))
    assert r.status_code == 429 and r.json()["detail"] == "Too many searches right now – try again in a minute"
    limits.running.clear()
    assert c.post(url(d)).status_code == 200 and limits.running == set()


async def test_nearby_sights_deadline_returns_finished_tiles(settings, monkeypatch):
    import asyncio

    import app.engine as engine_module
    from app.models import LatLon

    class Slow(FakeNature):
        async def fetch_tile(self, south, west):
            if (south, west) == (37.5, 22.5):
                await asyncio.sleep(0.5)
            return await super().fetch_tile(south, west)

    monkeypatch.setattr(engine_module, "NEARBY_DEADLINE_S", 0.1)
    engine = Engine(settings, None, [], None, None, DiskCache(settings.cache_dir), nature=Slow(area_tiles()))
    result = await engine.nearby_sights([LatLon(lat=HIKE[0], lon=HIKE[1]), LatLon(lat=STAY[0], lon=STAY[1])], 5)
    assert result.warnings == ["Search timed out – showing what was found; try again to continue"]
    assert [s.id for s in result.sights] == ["osm:node/4", "osm:node/1"]  # the hike's tiles only
    await asyncio.sleep(0.6)  # the slow tile keeps going in the background and is cached
    assert engine.cache.get("nature:v1:37.50_22.50") is not None
