import base64
import json
from pathlib import Path

import httpx
import pytest

from app.cache import DiskCache
from app.sources.wikiloc_route import (
    MAX_ROUTE_POINTS, ROUTE_TTL, RouteFetcher, decode_polyline, decode_twkb, encode_polyline, make_page_fetch,
    parse_route, route_points, simplify_route, wikiloc_number,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "wikiloc_geom.json").read_text())


# --- hand-built TWKB --------------------------------------------------------

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def zigzag(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def twkb(kind: int, parts: list[list[tuple]], precision: int = 5, dims: int = 2,
         ids: list[int] | None = None) -> bytes:
    """Encode lines (lon, lat[, z[, m]]) as TWKB type `kind` (2 = LineString, 5 = MultiLineString)."""
    header = bytes([(zigzag(precision) << 4) | kind])
    meta = (0x08 if dims > 2 else 0) | (0x04 if ids is not None else 0)
    body = bytearray()
    if dims > 2:
        body.append(0x01 | (0x02 if dims == 4 else 0) | (1 << 2))  # z (and m), z precision 1, m precision 0
    if kind == 5:
        body += varint(len(parts))
        if ids is not None:
            for i in ids:
                body += varint(zigzag(i))
    prev = [0] * dims
    scales = [10 ** precision, 10 ** precision, 10, 1][:dims]
    for part in parts:
        body += varint(len(part))
        for point in part:
            for d in range(dims):
                value = round(point[d] * scales[d])
                body += varint(zigzag(value - prev[d]))
                prev[d] = value
    return header + bytes([meta]) + bytes(body)


def test_decode_twkb_2d_linestring_with_negative_coordinates():
    pts = [(20.5, 39.1), (-0.25, -33.5), (179.99999, -89.5)]
    [line] = decode_twkb(twkb(2, [pts]))
    assert line == [(39.1, 20.5), (-33.5, -0.25), (-89.5, 179.99999)]


def test_decode_twkb_3d_and_4d_keep_lat_lon_only():
    pts = [(20.749124, 39.882184, 812.3, 1757834120), (20.749200, 39.882300, 815.0, 1757834130)]
    for dims in (3, 4):
        [line] = decode_twkb(twkb(2, [[p[:dims] for p in pts]], precision=6, dims=dims))
        assert line == [(39.882184, 20.749124), (39.8823, 20.7492)]


def test_decode_twkb_multilinestring_deltas_continue_across_parts():
    a = [(22.0, 38.0), (22.1, 38.1)]
    b = [(22.2, 38.2), (22.3, 38.25)]
    lines = decode_twkb(twkb(5, [a, b], ids=[7, 9]))
    assert lines == [[(38.0, 22.0), (38.1, 22.1)], [(38.2, 22.2), (38.25, 22.3)]]


def test_decode_twkb_skips_size_and_bbox():
    pts = [(22.0, 38.0), (22.5, 38.5)]
    plain = twkb(2, [pts])
    # bbox flag: per dimension a min and a delta varint, placed before the body
    bbox = varint(zigzag(2200000)) + varint(zigzag(50000)) + varint(zigzag(3800000)) + varint(zigzag(50000))
    body = plain[2:]
    with_bbox = plain[:1] + bytes([0x01 | 0x02]) + varint(len(bbox) + len(body)) + bbox + body
    assert decode_twkb(with_bbox) == [[(38.0, 22.0), (38.5, 22.5)]]


def test_decode_twkb_rejects_other_types_and_garbage():
    point = bytes([(zigzag(5) << 4) | 1, 0]) + varint(zigzag(100)) + varint(zigzag(200))
    assert decode_twkb(point) is None
    assert decode_twkb(b"") is None
    assert decode_twkb(twkb(2, [[(22.0, 38.0), (22.5, 38.5)]])[:-2]) is None  # truncated
    empty = bytes([(zigzag(5) << 4) | 2, 0x10])
    assert decode_twkb(empty) is None


def test_decode_real_wikiloc_geom():
    raw = base64.b64decode(FIXTURE["geom"] + "=" * (-len(FIXTURE["geom"]) % 4))
    [line] = decode_twkb(raw)
    assert len(line) == 2633
    # Wikiloc's own start/end metadata on the same page (blat/blng, elat/elng)
    assert line[0] == pytest.approx(tuple(FIXTURE["start"]), abs=1e-6)
    assert line[-1] == pytest.approx(tuple(FIXTURE["end"]), abs=1e-6)
    # all inside Zagori (the Vikos gorge)
    assert all(39.8 < lat < 40.0 and 20.6 < lon < 20.8 for lat, lon in line)


# --- page parsing -------------------------------------------------------------

def page(geom: str, var: str = "var mapData ={") -> str:
    data = json.dumps({"mapData": [{"spaId": 1, "nom": "x; y", "geom": geom, "loop": False}]})
    return f"<html><script>\n  var allow = false;\n  {var}{data[1:]};\n  var other = 1;\n</script></html>"


def test_parse_route_from_minimal_html():
    line = parse_route(page(FIXTURE["geom"]))
    assert len(line) == 2633 and line[0] == pytest.approx(tuple(FIXTURE["start"]), abs=1e-6)
    assert parse_route(page(FIXTURE["geom"], var="var mapData = {")) == line


def test_parse_route_joins_multilinestring_parts():
    geom = base64.b64encode(twkb(5, [[(22.0, 38.0), (22.1, 38.1)], [(22.2, 38.2), (22.3, 38.3)]])).decode()
    assert parse_route(page(geom.rstrip("="))) == [(38.0, 22.0), (38.1, 22.1), (38.2, 22.2), (38.3, 22.3)]


@pytest.mark.parametrize("html", ["", "<html>no map</html>", "var mapData ={broken", page("!!!"),
                                  page(base64.b64encode(b"\x01\x00\x02\x04").decode()),
                                  '<script>var mapData ={"mapData": []};\n</script>'])
def test_parse_route_none_without_a_track(html):
    assert parse_route(html) is None


# --- simplify + polyline ----------------------------------------------------

def test_polyline_known_value():
    # Google's documented example
    pts = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode_polyline(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == pts


def test_simplify_encode_decode_round_trip_on_the_real_track():
    raw = base64.b64decode(FIXTURE["geom"] + "=" * (-len(FIXTURE["geom"]) % 4))
    [line] = decode_twkb(raw)
    simple = simplify_route(line)
    assert 2 <= len(simple) <= MAX_ROUTE_POINTS
    assert simple[0] == line[0] and simple[-1] == line[-1]
    encoded = encode_polyline(simple)
    assert len(encoded) <= 2048
    back = decode_polyline(encoded)
    assert len(back) == len(simple)
    assert all(abs(a - c) <= 0.00001 and abs(b - d) <= 0.00001 for (a, b), (c, d) in zip(back, simple))


def test_simplify_caps_points_by_raising_the_tolerance():
    zigzag_line = [(38 + i * 0.001, 22 + (0.01 if i % 2 else 0)) for i in range(900)]
    simple = simplify_route(zigzag_line)
    assert len(simple) <= MAX_ROUTE_POINTS and simple[0] == zigzag_line[0] and simple[-1] == zigzag_line[-1]


def test_route_points_none_for_degenerate_lines():
    assert route_points(None) is None
    assert route_points([(38.0, 22.0)]) is None
    assert route_points([(38.0, 22.0), (38.0, 22.0)]) is None
    assert decode_polyline(route_points([(38.0, 22.0), (38.1, 22.1)])) == [(38.0, 22.0), (38.1, 22.1)]


def test_decode_polyline_rejects_garbage():
    assert decode_polyline("") == []
    with pytest.raises(ValueError):
        decode_polyline("_p~iF~ps|U_")  # truncated


@pytest.mark.parametrize("hike_id,number", [("wikiloc:231747769", "231747769"), ("231747769", "231747769"),
                                            ("airbnb:1", None), ("wikiloc:12a", None), ("wikiloc:", None),
                                            ("wikiloc:" + "9" * 13, None)])
def test_wikiloc_number(hike_id, number):
    assert wikiloc_number(hike_id) == number


# --- fetching ----------------------------------------------------------------

async def test_page_fetch_uses_the_user_agent_without_cookie_and_follows_redirects():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "view.do" in str(request.url):
            return httpx.Response(301, headers={"Location": "https://es.wikiloc.com/rutas-senderismo/x-231747769"})
        return httpx.Response(200, text=page(FIXTURE["geom"]))

    fetch = make_page_fetch(transport=httpx.MockTransport(handler))
    html = await fetch("https://es.wikiloc.com/wikiloc/view.do?id=231747769")
    assert "mapData" in html
    assert all(r.headers["User-Agent"] == "wikiloc-app/1.0" and "cookie" not in r.headers for r in seen)


async def test_page_fetch_retries_then_gives_up():
    calls, sleeps = [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    fetch = make_page_fetch(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(403)),
                            sleep=sleep, retries=2)
    assert await fetch("https://es.wikiloc.com/wikiloc/view.do?id=1") is None
    assert len(calls) == 3 and len(sleeps) == 2


async def test_page_fetch_does_not_retry_a_404():
    calls = []
    fetch = make_page_fetch(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(404)))
    assert await fetch("https://es.wikiloc.com/wikiloc/view.do?id=1") == ""  # no such trail: not a failure
    assert len(calls) == 1


class FakePages:
    def __init__(self, html=None, fail=False):
        self.urls = []
        self.html = html if html is not None else page(FIXTURE["geom"])
        self.fail = fail

    async def __call__(self, url):
        self.urls.append(url)
        return None if self.fail else self.html


async def test_route_fetcher_fetches_once_and_caches(tmp_path):
    pages = FakePages()
    cache = DiskCache(tmp_path)
    fetcher = RouteFetcher(cache=cache, fetch_page=pages)
    route = await fetcher.route("wikiloc:231747769")
    assert route and decode_polyline(route)[0] == pytest.approx(tuple(FIXTURE["start"]), abs=1e-5)
    assert pages.urls == ["https://es.wikiloc.com/wikiloc/view.do?id=231747769"]
    assert await fetcher.route("wikiloc:231747769") == route
    assert len(pages.urls) == 1
    assert cache.get("route:v1:231747769") == route
    assert ROUTE_TTL == 365 * 86400


async def test_route_fetcher_does_not_cache_failures(tmp_path):
    pages = FakePages(fail=True)
    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=pages, cooldown_s=0)
    assert await fetcher.route("wikiloc:5") is None
    assert await fetcher.route("wikiloc:5") is None
    assert len(pages.urls) == 2


async def test_route_fetcher_cools_down_after_a_failure(tmp_path):
    now = [1000.0]
    pages = FakePages(fail=True)
    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=pages, cooldown_s=600, clock=lambda: now[0])
    assert await fetcher.route("wikiloc:5") is None
    assert await fetcher.route("wikiloc:6") is None  # Wikiloc is blocking us: don't wait on it again
    assert len(pages.urls) == 1
    now[0] += 601
    pages.fail = False
    assert await fetcher.route("wikiloc:6")
    assert len(pages.urls) == 2


async def test_route_fetcher_page_without_track_is_not_a_failure(tmp_path):
    pages = FakePages(html="<html>no map</html>")
    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=pages, cooldown_s=600)
    assert await fetcher.route("wikiloc:5") is None
    pages.html = page(FIXTURE["geom"])
    assert await fetcher.route("wikiloc:6")  # no cooldown: the site answered


async def test_route_fetcher_offline_reads_the_cache_only(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("route:v1:7", "_p~iF~ps|U", ROUTE_TTL)
    pages = FakePages()
    fetcher = RouteFetcher(cache=cache, fetch_page=pages, online=False)
    assert await fetcher.route("wikiloc:7") == "_p~iF~ps|U"
    assert await fetcher.route("wikiloc:8") is None
    assert pages.urls == []


async def test_route_fetcher_ignores_non_wikiloc_ids(tmp_path):
    pages = FakePages()
    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=pages)
    assert await fetcher.route("airbnb:1") is None
    assert pages.urls == []


async def test_route_fetcher_deadline(tmp_path):
    import asyncio

    async def slow(url):
        await asyncio.sleep(5)
        return page(FIXTURE["geom"])

    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=slow, deadline_s=0.05, cooldown_s=0)
    assert await fetcher.route("wikiloc:5") is None


async def test_route_fetcher_limits_concurrency(tmp_path):
    import asyncio
    running, peak = [0], [0]

    async def tracked(url):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.01)
        running[0] -= 1
        return page(FIXTURE["geom"])

    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=tracked, concurrency=4)
    routes = await asyncio.gather(*(fetcher.route(f"wikiloc:{i}") for i in range(1, 11)))
    assert all(routes) and peak[0] == 4


async def test_route_fetcher_many_returns_a_map(tmp_path):
    fetcher = RouteFetcher(cache=DiskCache(tmp_path), fetch_page=FakePages())
    got = await fetcher.many(["wikiloc:1", "airbnb:2", "wikiloc:1"])
    assert set(got) == {"wikiloc:1", "airbnb:2"} and got["wikiloc:1"] and got["airbnb:2"] is None
