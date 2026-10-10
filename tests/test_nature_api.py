import json

import pytest
from fastapi.testclient import TestClient

import app.mcp_tools as tools
from app.cache import DAY, DiskCache
from app.config import Settings
import app.engine as engine_module
from app.engine import NATURE_LIMIT, NATURE_TRUNCATED, NATURE_UNAVAILABLE, Engine, nature_box
from app.main import create_app
from app.models import BBox, NatureSearch, Sight
from app.sources.nature import DailyBudget, NatureLimitReached, NatureUnavailable, Tile
from app.wiring import build_engine


def sight(id_, kind="waterfall", lat=37.6, lon=22.2, lines=None):
    return Sight(id=id_, name=id_, kind=kind, lat=lat, lon=lon, url="https://www.openstreetmap.org/node/1",
                 lines=lines or [])


class FakeNature:
    def __init__(self, tiles=None, fail=(), truncated=(), meta=None, budget=None):
        self.budget = budget
        self.tiles = tiles or {}
        self.fail = set(fail)
        self.truncated = set(truncated)
        self.meta = meta or {}
        self.calls = []

    async def fetch_tile(self, south, west):
        if self.budget is not None and not self.budget.take():
            raise NatureLimitReached("limit")
        self.calls.append((south, west))
        if (south, west) in self.fail:
            raise NatureUnavailable("busy")
        return Tile(self.tiles.get((south, west), []), dict(self.meta), (south, west) in self.truncated)


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
    assert len(river.lines) == 40 and river.lat == pytest.approx(37.605)  # centre of the combined lines


async def test_engine_merges_same_named_river_across_tiles(settings):
    a = sight("osm:way/1", "river", lines=[[[37.6, 22.2], [37.7, 22.24]]])
    b = sight("osm:relation/2", "river", lon=22.3, lines=[[[37.7, 22.26], [37.74, 22.3]]])
    a.name = b.name = "Λούσιος"
    nature = FakeNature({(37.5, 22.0): [a], (37.5, 22.25): [b]})
    (river,) = (await make_engine(settings, nature).nature(BOX, ALL)).sights
    assert river.id == "osm:relation/2" and len(river.lines) == 2
    assert river.lines[0] == [[37.7, 22.26], [37.74, 22.3]]  # the relation's lines first
    assert (river.lat, river.lon) == pytest.approx((37.67, 22.25))


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
            return Tile([], {})

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
    ("south=48.1&west=11.5&north=48.2&east=11.6", "Nature sights are available for Greece only"),  # Munich
    ("south=42.31&west=22.1&north=42.4&east=22.2", "Nature sights are available for Greece only"),  # > 0.5° north
    ("south=37.5&west=28.81&north=37.6&east=28.9", "Nature sights are available for Greece only"),  # > 0.5° east
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


# --- fix round -----------------------------------------------------------------------------------------


async def test_engine_truncated_tile_warns_and_is_cached_seven_days(settings):
    clock = Clock()
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1")]}, truncated={(37.5, 22.0)})
    engine = make_engine(settings, nature, clock)
    result = await engine.nature(BOX, ALL)
    assert result.warnings == [NATURE_TRUNCATED]
    assert NATURE_TRUNCATED == "Some nature sights were left out here (too many in this area)"
    clock.now += 7 * DAY - 1
    again = await engine.nature(BOX, ALL)  # cached like any tile (the grid is fixed: refetching won't help)
    assert again.warnings == [NATURE_TRUNCATED] and len(nature.calls) == 2
    clock.now += 2
    await engine.nature(BOX, ALL)
    assert nature.calls.count((37.5, 22.0)) == 2 and nature.calls.count((37.5, 22.25)) == 2


async def test_engine_cache_write_failure_still_answers(settings):
    engine = make_engine(settings, FakeNature({(37.5, 22.0): [sight("osm:node/1")]}))

    def broken(*args, **kwargs):
        raise OSError("disk full")

    engine.cache.set = broken
    result = await engine.nature(BOX, ALL)
    assert [s.id for s in result.sights] == ["osm:node/1"] and result.warnings == []


@pytest.mark.parametrize("entry", [[{"id": "osm:node/1"}], {"sights": [{"bad": 1}]}, "junk", {"sights": "x"}])
async def test_engine_corrupt_cache_entry_is_a_miss(settings, entry):
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1")]})
    engine = make_engine(settings, nature)
    engine.cache.set("nature:v1:37.50_22.00", entry, DAY)
    result = await engine.nature(BOX, ALL)
    assert [s.id for s in result.sights] == ["osm:node/1"] and (37.5, 22.0) in nature.calls


async def test_engine_semaphore_shared_across_requests_and_tiles_coalesced(settings):
    import asyncio

    running = {"now": 0, "max": 0}
    calls = []

    class Slow(FakeNature):
        async def fetch_tile(self, south, west):
            calls.append((south, west))
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            await asyncio.sleep(0.02)
            running["now"] -= 1
            return Tile([], {})

    engine = make_engine(settings, Slow())
    other = BBox(south=36.55, west=22.10, north=36.75, east=22.35)
    await asyncio.gather(engine.nature(BOX, ALL), engine.nature(BOX, ALL), engine.nature(other, ALL))
    assert running["max"] == 2
    assert sorted(calls) == [(36.5, 22.0), (36.5, 22.25), (37.5, 22.0), (37.5, 22.25)]  # BOX fetched once


async def test_engine_tile_deadline(settings, monkeypatch):
    import asyncio

    monkeypatch.setattr(engine_module, "NATURE_TILE_DEADLINE_S", 0.01)

    class Hang(FakeNature):
        async def fetch_tile(self, south, west):
            await asyncio.sleep(1)
            return Tile([], {})

    result = await make_engine(settings, Hang()).nature(BOX, ALL)
    assert result.warnings == [NATURE_UNAVAILABLE]


async def test_engine_merges_across_tiles_by_wikidata_and_prefers_english_name(settings):
    a = sight("osm:way/1", "river", lines=[[[37.6, 22.2], [37.7, 22.24]]])
    b = sight("osm:relation/2", "river", lon=22.3, lines=[[[37.7, 22.26], [37.74, 22.3]]])
    a.name, b.name = "Lousios", "Λούσιος"
    meta = {"osm:way/1": {"wd": "Q1872337", "name": "λούσιος", "en": True},
            "osm:relation/2": {"wd": None, "name": "λούσιος", "en": False}}
    nature = FakeNature({(37.5, 22.0): [a], (37.5, 22.25): [b]}, meta=meta)
    (river,) = (await make_engine(settings, nature).nature(BOX, ALL)).sights
    assert (river.id, river.name) == ("osm:relation/2", "Lousios")


async def test_mcp_nature_sights_outside_greece(mcp_engine):
    out = json.loads(await tools.nature_sights(80.0, 22.2, radius_km=30))
    assert out == {"error": "Nature sights are available for Greece only"} and mcp_engine.calls == []


# --- abuse limits ------------------------------------------------------------------------------------------


def test_nature_box_near_greece_is_allowed():
    nature_box(41.9, 22.0, 42.0, 22.2)  # just north of the Greece box, within the 0.5° padding
    nature_box(35.0, 18.9, 35.1, 19.0)  # just west


async def test_engine_queue_cap_skips_new_tiles(settings, monkeypatch):
    import asyncio

    monkeypatch.setattr(engine_module, "NATURE_MAX_INFLIGHT", 2)
    release = asyncio.Event()

    class Slow(FakeNature):
        async def fetch_tile(self, south, west):
            self.calls.append((south, west))
            await release.wait()
            return Tile([], {})

    nature = Slow()
    engine = make_engine(settings, nature)
    first = asyncio.ensure_future(engine.nature(BOX, ALL))
    for _ in range(200):
        if len(nature.calls) == 2:
            break
        await asyncio.sleep(0.005)
    other = BBox(south=36.55, west=22.10, north=36.75, east=22.35)
    busy = await engine.nature(other, ALL)
    assert busy.warnings == [NATURE_UNAVAILABLE] and len(nature.calls) == 2  # no fetch started
    release.set()
    assert (await first).warnings == []
    assert (await engine.nature(other, ALL)).warnings == [] and len(nature.calls) == 4


async def test_engine_daily_limit_serves_cache_only(settings):
    clock = Clock()
    cache = DiskCache(settings.cache_dir, clock=clock)
    nature = FakeNature({(37.5, 22.0): [sight("osm:node/1")]}, budget=DailyBudget(cache, limit=3, clock=clock))
    engine = Engine(settings, None, [], None, None, cache, nature=nature)
    assert (await engine.nature(BOX, ALL)).warnings == []  # 2 calls
    other = BBox(south=36.55, west=22.10, north=36.75, east=22.35)
    limited = await engine.nature(other, ALL)  # 1 call left: one tile fetched, one refused
    assert limited.warnings == [NATURE_LIMIT] and len(nature.calls) == 3
    assert NATURE_LIMIT == "Nature sights limit reached for today – showing cached areas only"
    cached = await engine.nature(BOX, ALL)
    assert cached.warnings == [] and [s.id for s in cached.sights] == ["osm:node/1"]
    # A restart keeps the count: still limited, and no fetch is even started
    cache2 = DiskCache(settings.cache_dir, clock=clock)
    nature2 = FakeNature(budget=DailyBudget(cache2, limit=3, clock=clock))
    engine2 = Engine(settings, None, [], None, None, cache2, nature=nature2)
    far = BBox(south=35.55, west=22.10, north=35.75, east=22.35)
    assert (await engine2.nature(far, ALL)).warnings == [NATURE_LIMIT] and nature2.calls == []
    clock.now += DAY
    assert (await engine2.nature(far, ALL)).warnings == [] and len(nature2.calls) == 2
