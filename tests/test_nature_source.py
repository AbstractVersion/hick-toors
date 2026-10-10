import json

import pytest

from app.models import SIGHT_GROUPS, BBox
from app.sources import nature
from app.sources.nature import (ENDPOINTS, MAX_TILES, NatureSource, NatureUnavailable, Tile, cap_lines,
                                parse_overpass, parse_tile, simplify, tile_query, tiles_for)


def node(id_, tags, lat=37.6, lon=22.2):
    return {"type": "node", "id": id_, "lat": lat, "lon": lon, "tags": tags}


def geom(points):
    return [{"lat": lat, "lon": lon} for lat, lon in points]


def zigzag(n, start=0.0):
    """n points far enough apart that Douglas–Peucker keeps every one."""
    return [(37.0 + i * 0.01, 22.0 + (0.01 if i % 2 else 0.0) + start) for i in range(n)]


LAKE_LINE = [(37.60, 22.20), (37.61, 22.21), (37.60, 22.22), (37.60, 22.20)]

FIXTURE = {"elements": [
    node(1, {"waterway": "waterfall", "name": "Καταρράκτης", "name:en": "Kalavrita falls", "ele": "120"}),
    node(2, {"natural": "spring"}),
    node(3, {"natural": "peak", "name": "Μαίναλο", "ele": "1981 m", "wikipedia": "el:Μαίναλο"}),
    {"type": "way", "id": 10, "tags": {"natural": "water", "water": "lake", "name": "Lake Doxa"},
     "center": {"lat": 37.605, "lon": 22.21}, "geometry": geom(LAKE_LINE)},
    {"type": "relation", "id": 20, "tags": {"waterway": "river", "name": "Ladonas"},
     "center": {"lat": 37.7, "lon": 22.1},
     "members": [
         {"type": "way", "ref": 201, "role": "", "geometry": geom(zigzag(3))},
         {"type": "way", "ref": 202, "role": "", "geometry": geom(zigzag(4, 0.1))},
         {"type": "node", "ref": 203, "role": "spring", "lat": 37.0, "lon": 22.0},
     ]},
    {"type": "relation", "id": 30, "tags": {"boundary": "protected_area", "name": "Mainalo Natura"},
     "bounds": {"minlat": 37.5, "minlon": 22.0, "maxlat": 37.7, "maxlon": 22.4},
     "members": [{"type": "way", "ref": 300 + i, "role": "outer", "geometry": geom(zigzag(3, i * 0.001))}
                 for i in range(60)]},
    {"type": "way", "id": 40, "tags": {"natural": "beach", "name": "Long beach"},
     "center": {"lat": 37.0, "lon": 22.0},
     "geometry": geom([(37.0 + i * 0.00001, 22.0 + i * 0.00001) for i in range(5000)])},
    node(4, {"amenity": "public_bath", "bath:type": "hot_spring", "name": "Loutra"}),
    {"type": "way", "id": 50, "tags": {"natural": "valley", "valley": "canyon", "name": "Lousios gorge"},
     "center": {"lat": 37.6, "lon": 22.1}, "geometry": geom([(37.6, 22.1), (37.62, 22.12)])},
    node(5, {"natural": "rock"}),
    node(6, {"shop": "bakery", "name": "Bakery"}),
    node(7, {"tourism": "viewpoint", "name": "x" * 300}),
]}


def by_id(sights):
    return {s.id: s for s in sights}


def test_parse_points_names_ele_wikipedia_urls():
    s = by_id(parse_overpass(FIXTURE))
    falls = s["osm:node/1"]
    assert (falls.kind, falls.name, falls.elevation_m) == ("waterfall", "Kalavrita falls", 120)
    assert falls.url == "https://www.openstreetmap.org/node/1"
    assert (falls.lat, falls.lon) == (37.6, 22.2) and falls.lines == []
    assert (s["osm:node/2"].kind, s["osm:node/2"].name) == ("spring", "Spring")
    peak = s["osm:node/3"]
    assert (peak.kind, peak.elevation_m) == ("peak", 1981)
    assert peak.wikipedia == "https://el.wikipedia.org/wiki/Μαίναλο"
    assert s["osm:node/4"].kind == "hot_spring"


def test_parse_skips_unnamed_rock_and_unknown_tags():
    s = by_id(parse_overpass(FIXTURE))
    assert "osm:node/5" not in s and "osm:node/6" not in s


def test_name_is_cut_to_120_chars():
    assert by_id(parse_overpass(FIXTURE))["osm:node/7"].name == "x" * 120


def test_way_lake_has_center_and_one_line():
    lake = by_id(parse_overpass(FIXTURE))["osm:way/10"]
    assert (lake.kind, lake.lat, lake.lon) == ("lake", 37.605, 22.21)
    assert lake.url == "https://www.openstreetmap.org/way/10"
    assert lake.lines == [[list(p) for p in LAKE_LINE]]


def test_relation_river_gives_one_line_per_member_way():
    river = by_id(parse_overpass(FIXTURE))["osm:relation/20"]
    assert river.kind == "river" and river.url == "https://www.openstreetmap.org/relation/20"
    assert [len(line) for line in river.lines] == [4, 3]  # longest first


def test_relation_lines_capped_at_40_and_center_from_bounds():
    area = by_id(parse_overpass(FIXTURE))["osm:relation/30"]
    assert area.kind == "protected_area" and len(area.lines) == 40
    assert (area.lat, area.lon) == pytest.approx((37.6, 22.2))


def test_collinear_way_simplified_to_two_points():
    beach = by_id(parse_overpass(FIXTURE))["osm:way/40"]
    assert beach.lines == [[[37.0, 22.0], [37.0 + 4999 * 0.00001, 22.0 + 4999 * 0.00001]]]


def test_valley_canyon_is_gorge():
    assert by_id(parse_overpass(FIXTURE))["osm:way/50"].kind == "gorge"


def test_points_total_capped_at_2000():
    big = {"elements": [{"type": "relation", "id": 9, "tags": {"leisure": "nature_reserve", "name": "R"},
                         "center": {"lat": 37.0, "lon": 22.0},
                         "members": [{"type": "way", "ref": i, "geometry": geom(zigzag(300))} for i in range(10)]}]}
    (sight,) = parse_overpass(big)
    assert sum(len(line) for line in sight.lines) <= 2000 and len(sight.lines) == 10


def test_lake_without_name_and_reservoir():
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"natural": "water", "water": "lake"}, "center": {"lat": 1, "lon": 2}},
        {"type": "way", "id": 2, "tags": {"natural": "water", "water": "reservoir", "name": "Mornos"},
         "center": {"lat": 1, "lon": 2}},
        {"type": "way", "id": 3, "tags": {"landuse": "forest", "name": "Fir forest"}, "center": {"lat": 1, "lon": 2}},
        {"type": "way", "id": 4, "tags": {"natural": "wood"}, "center": {"lat": 1, "lon": 2}},
    ]}
    assert [(s.id, s.kind) for s in parse_overpass(data)] == [("osm:way/2", "lake"), ("osm:way/3", "forest")]


def test_wikipedia_only_for_lang_title():
    data = {"elements": [node(1, {"natural": "peak", "wikipedia": "Mount Olympus"}),
                         node(2, {"natural": "peak", "wikipedia": "en:Mount Olympus"})]}
    s = by_id(parse_overpass(data))
    assert s["osm:node/1"].wikipedia is None
    assert s["osm:node/2"].wikipedia == "https://en.wikipedia.org/wiki/Mount_Olympus"


def test_non_numeric_ele_ignored():
    (s,) = parse_overpass({"elements": [node(1, {"natural": "peak", "ele": "high"})]})
    assert s.elevation_m is None and s.name == "Peak"


def test_simplify_keeps_corners_and_handles_short_lines():
    assert simplify([[0, 0], [0, 1], [0, 2]], 0.0005) == [[0, 0], [0, 2]]
    assert simplify([[0, 0], [1, 1], [0, 2]], 0.0005) == [[0, 0], [1, 1], [0, 2]]
    assert simplify([[0, 0]], 0.0005) == [[0, 0]]


def test_groups_cover_every_kind_once():
    kinds = [k for ks in SIGHT_GROUPS.values() for k in ks]
    assert len(kinds) == len(set(kinds)) == 14
    assert set(SIGHT_GROUPS) == {"water", "landforms", "views", "protected"}


def test_tiles_for_snaps_to_quarter_degree():
    assert tiles_for(BBox(south=37.55, west=22.10, north=37.75, east=22.35)) == [(37.5, 22.0), (37.5, 22.25)]


def test_tiles_for_negative_coords_floor():
    assert tiles_for(BBox(south=-0.1, west=-0.3, north=-0.05, east=-0.26)) == [(-0.25, -0.5)]


def test_tiles_for_too_many_raises():
    assert MAX_TILES == 16
    assert len(tiles_for(BBox(south=37.0, west=22.0, north=38.0, east=23.0))) == 16
    with pytest.raises(ValueError):
        tiles_for(BBox(south=37.0, west=22.0, north=38.01, east=23.0))


def test_tile_query_text():
    q = tile_query(37.5, 22.0)
    assert q.startswith("[out:json][timeout:25];")
    assert "(37.5,22.0,37.75,22.25)" in q
    assert 'node["waterway"="waterfall"]' in q and 'relation["boundary"="protected_area"]' in q
    assert 'way["natural"="water"]' in q
    assert ")->.points;\n.points out body 400;" in q
    assert q.rstrip().endswith(")->.areas;\n.areas out body geom(37.5,22.0,37.75,22.25) 300;")


async def test_fetch_tile_falls_back_to_second_endpoint():
    calls = []

    async def fetch(url, data):
        calls.append((url, data))
        if url == ENDPOINTS[0]:
            raise RuntimeError("504")
        return json.dumps(FIXTURE)

    tile = await NatureSource(fetch=fetch).fetch_tile(37.5, 22.0)
    assert isinstance(tile, Tile) and tile.truncated is False
    sights = tile.sights
    assert [u for u, _ in calls] == ENDPOINTS
    assert calls[0][1] == {"data": tile_query(37.5, 22.0)}
    assert "osm:node/1" in by_id(sights)


async def test_fetch_tile_first_endpoint_ok():
    calls = []

    async def fetch(url, data):
        calls.append(url)
        return json.dumps({"elements": []})

    assert (await NatureSource(fetch=fetch).fetch_tile(37.5, 22.0)).sights == []
    assert calls == [ENDPOINTS[0]]


@pytest.mark.parametrize("answers", [
    [RuntimeError("504"), RuntimeError("500")],
    ["<html>busy</html>", "not json"],
    [RuntimeError("504"), json.dumps(["no", "elements"])],
    [json.dumps({"elements": [], "remark": "runtime error: Query timed out"}), RuntimeError("500")],
])
async def test_fetch_tile_both_endpoints_fail(answers):
    answers = list(answers)

    async def fetch(url, data):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    with pytest.raises(NatureUnavailable):
        await NatureSource(fetch=fetch).fetch_tile(37.5, 22.0)


def test_default_fetch_is_built():
    assert NatureSource()._fetch is not None and nature.USER_AGENT.startswith("hick-toors/1.0")


def river_way(id_, points, name="Λούσιος"):
    return {"type": "way", "id": id_, "tags": {"waterway": "river", "name": name},
            "bounds": {"minlat": 0, "minlon": 0, "maxlat": 0, "maxlon": 0}, "geometry": geom(points)}


LOUSIOS = {"elements": [
    node(1, {"natural": "spring"}),
    river_way(101, [(37.50, 22.00), (37.55, 22.05)]),
    river_way(102, [(37.55, 22.05), (37.60, 22.02)]),
    river_way(103, [(37.60, 22.02), (37.70, 22.10)]),
    river_way(104, [(37.0, 22.0), (37.1, 22.1)], name="Ladonas"),
    {"type": "relation", "id": 900, "tags": {"waterway": "river", "name": "Λούσιος", "wikipedia": "el:Λούσιος"},
     "bounds": {"minlat": 37.5, "minlon": 22.0, "maxlat": 37.6, "maxlon": 22.05},
     "members": [{"type": "way", "ref": 101, "role": "main_stream",
                  "geometry": geom([(37.50, 22.00), (37.55, 22.05)])},
                 {"type": "way", "ref": 102, "role": "main_stream",
                  "geometry": geom([(37.55, 22.05), (37.60, 22.02)])}]},
]}


def test_river_ways_in_a_relation_are_dropped_and_same_name_merged():
    sights = parse_overpass(LOUSIOS)
    assert [s.id for s in sights] == ["osm:node/1", "osm:relation/900", "osm:way/104"]
    river = sights[1]
    assert (river.kind, river.name, river.wikipedia) == ("river", "Λούσιος", "https://el.wikipedia.org/wiki/Λούσιος")
    assert river.url == "https://www.openstreetmap.org/relation/900"
    assert river.lines == [[[37.50, 22.00], [37.55, 22.05]], [[37.55, 22.05], [37.60, 22.02]],
                           [[37.60, 22.02], [37.70, 22.10]]]
    assert (river.lat, river.lon) == pytest.approx((37.6, 22.05))  # centre of the combined bounds


def test_same_name_merge_only_for_line_kinds():
    data = {"elements": [node(1, {"natural": "peak", "name": "Profitis Ilias"}),
                         node(2, {"natural": "peak", "name": "Profitis Ilias"}, lat=37.7),
                         {"type": "way", "id": 3, "tags": {"landuse": "forest", "name": "Fir"},
                          "geometry": geom([(37.0, 22.0), (37.1, 22.0)])},
                         {"type": "way", "id": 4, "tags": {"landuse": "forest", "name": "Fir"},
                          "geometry": geom([(37.2, 22.0), (37.3, 22.0)])}]}
    assert [s.id for s in parse_overpass(data)] == ["osm:node/1", "osm:node/2", "osm:way/3"]


def test_merge_named_without_relation_keeps_first_id():
    data = {"elements": [river_way(5, [(37.0, 22.0), (37.1, 22.1)]), river_way(6, [(37.2, 22.2), (37.3, 22.3)])]}
    (river,) = parse_overpass(data)
    assert river.id == "osm:way/5" and len(river.lines) == 2
    assert (river.lat, river.lon) == pytest.approx((37.15, 22.15))


def test_park_relation_does_not_swallow_river_way():
    data = {"elements": [
        river_way(101, [(37.50, 22.00), (37.55, 22.05)], name="Ladonas"),
        {"type": "relation", "id": 7, "tags": {"boundary": "protected_area", "name": "Park"},
         "bounds": {"minlat": 37.5, "minlon": 22.0, "maxlat": 37.6, "maxlon": 22.05},
         "members": [{"type": "way", "ref": 101, "geometry": geom([(37.50, 22.00), (37.55, 22.05)])}]},
    ]}
    assert [s.id for s in parse_overpass(data)] == ["osm:way/101", "osm:relation/7"]


def lousios_way(id_, points, wikidata=None, en="Lousios", name="Λούσιος"):
    tags = {"waterway": "river", "name": name, "name:en": en}
    if wikidata:
        tags["wikidata"] = wikidata
    return {"type": "way", "id": id_, "tags": tags, "geometry": geom(points)}


def test_mixed_wikidata_and_names_merge_into_one_river_named_in_english():
    data = {"elements": [
        lousios_way(1, [(37.50, 22.00), (37.55, 22.05)], wikidata="Q1872337"),
        lousios_way(2, [(37.55, 22.05), (37.60, 22.02)]),
        lousios_way(3, [(37.60, 22.02), (37.70, 22.10)], wikidata="Q1872337", name=" λούσιος"),
        {"type": "relation", "id": 9, "tags": {"waterway": "river", "name": "Λούσιος"},
         "members": [{"type": "way", "ref": 99, "geometry": geom([(37.70, 22.10), (37.75, 22.12)])}]},
    ]}
    (river,) = parse_overpass(data)
    assert (river.id, river.name, len(river.lines)) == ("osm:relation/9", "Lousios", 4)


def test_same_name_different_wikidata_stay_apart():
    data = {"elements": [lousios_way(1, [(37.0, 22.0), (37.1, 22.0)], wikidata="Q1"),
                         lousios_way(2, [(37.2, 22.0), (37.3, 22.0)], wikidata="Q2")]}
    assert [s.id for s in parse_overpass(data)] == ["osm:way/1", "osm:way/2"]


def test_parse_tile_meta_carries_merge_keys():
    tile = parse_tile({"elements": [lousios_way(1, [(37.0, 22.0), (37.1, 22.0)], wikidata="Q1")]})
    assert tile.meta["osm:way/1"] == {"wd": "Q1", "name": "λούσιοσ", "en": True}  # casefold: final ς -> σ
    assert tile.truncated is False


def test_cap_lines_longest_first_resimplifies_and_drops_degenerate():
    long = [[37.0 + i * 0.01, 22.0 + (0.01 if i % 2 else 0.0)] for i in range(1500)]
    other = [[38.0 + i * 0.01, 22.0 + (0.01 if i % 2 else 0.0)] for i in range(800)]
    out = cap_lines([[[1.0, 1.0], [1.0, 1.0]], other, long])
    assert out[0] is long or out[0] == long
    assert len(out) == 2 and 2 <= len(out[1]) <= 500 and out[1][0] == other[0] and out[1][-1] == other[-1]
    assert sum(len(line) for line in out) <= 2000


def _fetch_with(elements):
    async def fetch(url, data):
        return json.dumps({"elements": elements})
    return fetch


async def test_fetch_tile_flags_truncated_points():
    tile = await NatureSource(fetch=_fetch_with([node(i, {"natural": "spring"}) for i in range(400)])).fetch_tile(
        37.5, 22.0)
    assert tile.truncated is True and len(tile.sights) == 400


async def test_fetch_tile_flags_truncated_areas_only_at_cap():
    def lake(i):
        return {"type": "way", "id": i, "tags": {"natural": "water", "water": "lake", "name": f"L{i}"},
                "geometry": geom([(37.0, 22.0), (37.1, 22.1)])}

    assert (await NatureSource(fetch=_fetch_with([lake(i) for i in range(299)])).fetch_tile(37.5, 22.0)).truncated \
        is False
    assert (await NatureSource(fetch=_fetch_with([lake(i) for i in range(300)])).fetch_tile(37.5, 22.0)).truncated


def test_wikipedia_url_encodes_unsafe_characters():
    (s,) = parse_overpass({"elements": [node(1, {"natural": "peak",
                                                 "wikipedia": 'en:Olympus (mountain) [x]"<>\\|{}`\tΌλυμπος'})]})
    assert s.wikipedia == ("https://en.wikipedia.org/wiki/Olympus_%28mountain%29_%5Bx%5D%22%3C%3E%5C%7C%7B%7D%60%09"
                           "Όλυμπος")


# --- abuse limits ----------------------------------------------------------------------------------------


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def scripted(answers, calls):
    answers = list(answers)

    async def fetch(url, data):
        calls.append(url)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    return fetch


OK = json.dumps({"elements": []})
DOWN = [RuntimeError("504"), RuntimeError("500")]


async def test_backoff_after_both_endpoints_fail_doubles_and_resets():
    clock, calls = Clock(), []
    src = NatureSource(fetch=scripted(DOWN * 3 + [OK] + DOWN + [OK], calls), clock=clock)
    with pytest.raises(NatureUnavailable):
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 2
    clock.now += 59
    with pytest.raises(NatureUnavailable):  # backing off: no HTTP call
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 2
    clock.now += 2
    with pytest.raises(NatureUnavailable):  # second failure in a row: 120 s
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 4
    clock.now += 119
    with pytest.raises(NatureUnavailable):
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 4
    clock.now += 2
    with pytest.raises(NatureUnavailable):  # third: 240 s
        await src.fetch_tile(37.5, 22.0)
    clock.now += 239
    with pytest.raises(NatureUnavailable):
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 6
    clock.now += 2
    await src.fetch_tile(37.5, 22.0)  # success resets the backoff
    assert len(calls) == 7
    with pytest.raises(NatureUnavailable):
        await src.fetch_tile(37.5, 22.0)
    clock.now += 61  # back to 60 s after a success
    await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 10


async def test_backoff_capped_at_ten_minutes():
    clock, calls = Clock(), []
    src = NatureSource(fetch=scripted(DOWN * 8, calls), clock=clock)
    for _ in range(8):
        with pytest.raises(NatureUnavailable):
            await src.fetch_tile(37.5, 22.0)
        clock.now += 601
    assert len(calls) == 16  # never skipped: every wait was longer than the 600 s cap
    clock.now -= 2  # 599 s after the last failure: still backing off
    with pytest.raises(NatureUnavailable):
        await src.fetch_tile(37.5, 22.0)
    assert len(calls) == 16


async def test_last_answering_endpoint_is_tried_first():
    calls = []
    src = NatureSource(fetch=scripted([RuntimeError("504"), OK, OK], calls))
    await src.fetch_tile(37.5, 22.0)
    await src.fetch_tile(37.5, 22.25)
    assert calls == [ENDPOINTS[0], ENDPOINTS[1], ENDPOINTS[1]]


async def test_daily_budget_counts_http_calls_and_survives_restart(tmp_path):
    from app.cache import DAY, DiskCache
    from app.sources.nature import DailyBudget, NatureLimitReached

    clock, calls = Clock(), []
    cache = DiskCache(tmp_path, clock=clock)
    src = NatureSource(fetch=scripted([RuntimeError("504"), OK, OK], calls),
                       budget=DailyBudget(cache, limit=3, clock=clock))
    await src.fetch_tile(37.5, 22.0)  # two calls
    await src.fetch_tile(37.5, 22.25)  # one call
    assert src.budget.exhausted()
    restarted = DailyBudget(DiskCache(tmp_path, clock=clock), limit=3, clock=clock)
    assert restarted.exhausted() and restarted.take() is False
    src2 = NatureSource(fetch=scripted([OK], calls), budget=restarted)
    with pytest.raises(NatureLimitReached):
        await src2.fetch_tile(37.0, 22.0)
    assert len(calls) == 3
    clock.now += DAY  # the next UTC day
    assert not restarted.exhausted()
    await src2.fetch_tile(37.0, 22.0)
    assert len(calls) == 4


def test_daily_budget_default_limit():
    from app.sources.nature import DAILY_CALLS

    assert DAILY_CALLS == 5000
