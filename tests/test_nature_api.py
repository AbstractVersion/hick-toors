import json

import pytest
from fastapi.testclient import TestClient

import app.mcp_tools as tools
from app.cache import DAY, DiskCache
from app.config import Settings
from app.engine import NATURE_UNAVAILABLE, Engine
from app.main import create_app
from app.models import BBox, NatureSearch, Sight
from app.sources.nature import NatureUnavailable
from app.wiring import build_engine


def sight(id_, kind="waterfall", lat=37.6, lon=22.2, lines=None):
    return Sight(id=id_, name=id_, kind=kind, lat=lat, lon=lon, url="https://www.openstreetmap.org/node/1",
                 lines=lines or [])


class FakeNature:
    def __init__(self, tiles=None, fail=()):
        self.tiles = tiles or {}
        self.fail = set(fail)
        self.calls = []

    async def fetch_tile(self, south, west):
        self.calls.append((south, west))
        if (south, west) in self.fail:
            raise NatureUnavailable("busy")
        return self.tiles.get((south, west), [])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")


def make_engine(settings, nature, clock=None):
    cache = DiskCache(settings.cache_dir, clock=clock) if clock else DiskCache(settings.cache_dir)
    return Engine(settings, None, [], None, None, cache, nature=nature)


BOX = BBox(south=37.55, west=22.10, north=37.75, east=22.35)  # tiles (37.5, 22.0) and (37.5, 22.25)
ALL = ["water", "landforms", "views", "protected"]


async def test_engine_merges_tiles_filters_groups_and_caches(settings):
    clock = Clock()
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1"), sight("osm:node/2", "peak")],
                         (37.5, 22.25): [sight("osm:node/3", "viewpoint", lon=22.3)]})
    engine = make_engine(settings, nature, clock)
    result = await engine.nature(BOX, ["water", "views"])
    assert [s.id for s in result.sights] == ["osm:node/1", "osm:node/3"] and result.warnings == []
    assert sorted(nature.calls) == [(37.5, 22.0), (37.5, 22.25)]
    assert engine.cache.get("nature:v1:37.50_22.00") is not None
    again = await engine.nature(BOX, ALL)
    assert len(nature.calls) == 2 and [s.id for s in again.sights] == ["osm:node/1", "osm:node/2", "osm:node/3"]
    clock.now += 7 * DAY + 1
    await engine.nature(BOX, ALL)
    assert len(nature.calls) == 4


async def test_engine_merges_same_sight_lines_and_caps(settings):
    line = [[37.6, 22.2], [37.61, 22.21]]
    nature = FakeNature({(37.5, 22.0): [sight("osm:relation/9", "river", lines=[line] * 30)],
                         (37.5, 22.25): [sight("osm:relation/9", "river", lat=1.0, lines=[line] * 30)]})
    result = await make_engine(settings, nature).nature(BOX, ALL)
    (river,) = result.sights
    assert len(river.lines) == 40 and river.lat == 37.6


async def test_engine_partial_failure_warns_once_and_is_not_cached(settings):
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1")]}, fail={(37.5, 22.25)})
    engine = make_engine(settings, nature)
    result = await engine.nature(BOX, ALL)
    assert [s.id for s in result.sights] == ["osm:node/1"]
    assert result.warnings == [NATURE_UNAVAILABLE]
    assert NATURE_UNAVAILABLE == "Nature sights unavailable for part of the map – try again in a minute"
    assert engine.cache.get("nature:v1:37.50_22.25") is None
    nature.fail.clear()
    await engine.nature(BOX, ALL)
    assert nature.calls.count((37.5, 22.25)) == 2 and nature.calls.count((37.5, 22.0)) == 1


async def test_engine_all_tiles_failed(settings):
    nature = FakeNature(fail={(37.5, 22.0), (37.5, 22.25)})
    result = await make_engine(settings, nature).nature(BOX, ALL)
    assert result.sights == [] and result.warnings == [NATURE_UNAVAILABLE]


async def test_engine_drops_sights_outside_the_box(settings):
    far = sight("osm:node/8", lat=37.52)  # in tile (37.5, 22.0) but south of the requested box
    river = sight("osm:way/9", "river", lat=37.52, lines=[[[37.52, 22.2], [37.6, 22.2]]])  # line enters the box
    nature = FakeNature({(37.5, 22.0): [far, river]})
    result = await make_engine(settings, nature).nature(BOX, ALL)
    assert [s.id for s in result.sights] == ["osm:way/9"]


async def test_engine_limits_concurrency_to_two(settings):
    import asyncio

    running = {"now": 0, "max": 0}

    class Slow(FakeNature):
        async def fetch_tile(self, south, west):
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            await asyncio.sleep(0.01)
            running["now"] -= 1
            return []

    await make_engine(settings, Slow()).nature(BBox(south=37.0, west=22.0, north=38.0, east=23.0), ALL)
    assert running["max"] == 2


async def test_engine_without_source_warns(settings):
    result = await make_engine(settings, None).nature(BOX, ALL)
    assert result.warnings == [NATURE_UNAVAILABLE]


def test_build_engine_wires_nature_source(settings):
    from app.sources.nature import NatureSource

    assert isinstance(build_engine(settings).nature_source, NatureSource)


# --- API ---------------------------------------------------------------------------------------------


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def nature(self, bbox, groups):
        self.calls.append((bbox, groups))
        return NatureSearch(sights=[sight("osm:node/1")], warnings=["w"])


def client(settings, engine):
    return TestClient(create_app(engine=engine, ask_runner=object(), settings=settings))


def test_api_nature_defaults_to_all_groups(settings):
    engine = FakeEngine()
    r = client(settings, engine).get("/api/nature", params={"south": 37.55, "west": 22.1, "north": 37.75,
                                                            "east": 22.35})
    assert r.status_code == 200
    assert r.json()["sights"][0]["id"] == "osm:node/1" and r.json()["warnings"] == ["w"]
    bbox, groups = engine.calls[0]
    assert (bbox.south, bbox.west, bbox.north, bbox.east) == (37.55, 22.1, 37.75, 22.35)
    assert groups == ALL


def test_api_nature_groups_param(settings):
    engine = FakeEngine()
    r = client(settings, engine).get("/api/nature?south=37.55&west=22.1&north=37.75&east=22.35"
                                     "&groups=water,%20views,water")
    assert r.status_code == 200 and engine.calls[0][1] == ["water", "views"]


@pytest.mark.parametrize("query, detail", [
    ("south=37.55&west=22.1&north=37.75&east=22.35&groups=water,lava", "Unknown group: lava"),
    ("south=37.75&west=22.1&north=37.55&east=22.35", "south must be < north and west < east"),
    ("south=37.55&west=22.35&north=37.75&east=22.1", "south must be < north and west < east"),
    ("south=36&west=21&north=39&east=24", "Zoom in to see nature sights"),
    ("south=-91&west=22.1&north=37.75&east=22.35", "Latitude must be within -90..90 and longitude within -180..180"),
    ("south=37.55&west=22.1&north=37.75&east=181", "Latitude must be within -90..90 and longitude within -180..180"),
    ("south=nan&west=22.1&north=37.75&east=22.35", "Latitude must be within -90..90 and longitude within -180..180"),
])
def test_api_nature_validation(settings, query, detail):
    engine = FakeEngine()
    r = client(settings, engine).get(f"/api/nature?{query}")
    assert r.status_code == 422 and r.json()["detail"] == detail
    assert engine.calls == []


def test_api_nature_missing_param_is_422(settings):
    assert client(settings, FakeEngine()).get("/api/nature?south=1&west=2&north=3").status_code == 422


def test_api_nature_real_engine_cache_hit(settings):
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1")]})
    c = client(settings, make_engine(settings, nature))
    url = "/api/nature?south=37.55&west=22.1&north=37.75&east=22.35&groups=water"
    first, second = c.get(url), c.get(url)
    assert first.json() == second.json() and [s["id"] for s in first.json()["sights"]] == ["osm:node/1"]
    assert len(nature.calls) == 2  # two tiles, fetched once each


# --- MCP ---------------------------------------------------------------------------------------------


@pytest.fixture
def mcp_engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(tools, "_engine", fake)
    return fake


async def test_mcp_nature_sights(mcp_engine):
    out = json.loads(await tools.nature_sights(37.6, 22.2, radius_km=5, groups=["water"]))
    assert out["sights"][0]["id"] == "osm:node/1"
    bbox, groups = mcp_engine.calls[0]
    assert groups == ["water"] and bbox.south < 37.6 < bbox.north and bbox.west < 22.2 < bbox.east
    assert bbox.north - bbox.south == pytest.approx(10 / 111.32)


async def test_mcp_nature_sights_defaults(mcp_engine):
    await tools.nature_sights(37.6, 22.2)
    assert mcp_engine.calls[0][1] == ALL


@pytest.mark.parametrize("kwargs", [{"radius_km": 0.5}, {"radius_km": 31}, {"groups": ["lava"]},
                                    {"lat": 91}, {"lon": float("nan")}])
async def test_mcp_nature_sights_errors(mcp_engine, kwargs):
    args = {"lat": 37.6, "lon": 22.2, **kwargs}
    out = json.loads(await tools.nature_sights(**args))
    assert "error" in out and mcp_engine.calls == []
