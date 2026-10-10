"""Natural sights from OpenStreetMap through the Overpass API, one 0.25° tile per query."""

import json
import math
import re
import sys
from typing import Awaitable, Callable

import httpx

from app.models import SIGHT_LABELS, BBox, Sight

ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
USER_AGENT = "hick-toors/1.0 (personal hiking planner)"
TIMEOUT_S = 40.0
TILE_DEG = 0.25
MAX_TILES = 16
MAX_ELEMENTS = 600
TOLERANCE_DEG = 0.0005
MAX_LINES = 40
MAX_POINTS = 2000
MAX_NAME = 120
OSM_URL = "https://www.openstreetmap.org"

FetchText = Callable[[str, dict], Awaitable[str]]  # fetch(url, form data) -> response body text

ELE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:m)?\s*$")
WIKIPEDIA_RE = re.compile(r"^([a-z]{2,3}):(.+)$")


class NatureUnavailable(Exception):
    pass


def _idx(value: float, up: bool) -> int:
    x = value / TILE_DEG
    # Tolerate float noise so 37.75 / 0.25 is exactly 151, not 151.00000000000003.
    return math.ceil(x - 1e-9) if up else math.floor(x + 1e-9)


def tiles_for(bbox: BBox) -> list[tuple[float, float]]:
    """South-west corners of the 0.25° grid tiles covering bbox; ValueError when more than MAX_TILES."""
    s0, s1 = _idx(bbox.south, False), max(_idx(bbox.north, True), _idx(bbox.south, False) + 1)
    w0, w1 = _idx(bbox.west, False), max(_idx(bbox.east, True), _idx(bbox.west, False) + 1)
    if (s1 - s0) * (w1 - w0) > MAX_TILES:
        raise ValueError("Too many tiles")
    return [(round(i * TILE_DEG, 2), round(j * TILE_DEG, 2)) for i in range(s0, s1) for j in range(w0, w1)]


def _box(south: float, west: float) -> str:
    return (f"({round(south, 2)!r},{round(west, 2)!r},"
            f"{round(south + TILE_DEG, 2)!r},{round(west + TILE_DEG, 2)!r})")


NODE_FILTERS = [
    '["waterway"="waterfall"]',
    '["natural"~"^(spring|hot_spring|peak|cave_entrance|arch|beach)$"]',
    '["amenity"="public_bath"]["bath:type"="hot_spring"]',
    '["natural"="rock"]["name"]',
    '["tourism"="viewpoint"]',
]
AREA_FILTERS = [
    '["natural"="water"]["water"~"^(lake|reservoir)$"]["name"]',
    '["waterway"="river"]["name"]',
    '["natural"="gorge"]',
    '["natural"="valley"]["valley"~"^(gorge|canyon)$"]',
    '["natural"="beach"]',
    '["boundary"="protected_area"]["name"]',
    '["boundary"="national_park"]["name"]',
    '["leisure"="nature_reserve"]["name"]',
    '["landuse"="forest"]["name"]',
    '["natural"="wood"]["name"]',
]


def tile_query(south: float, west: float) -> str:
    """Overpass QL for every sight kind in one tile; way/relation geometry is clipped to the tile.

    `out body geom`, not the spec's `out tags center geom`: probed 2026-10-10, `tags` drops relation members
    (so no river/protected-area lines) and `geom` overrides `center` anyway (positions come from `bounds`).
    """
    box = _box(south, west)
    lines = [f"  node{f}{box};" for f in NODE_FILTERS]
    lines += [f"  {t}{f}{box};" for t in ("way", "relation") for f in AREA_FILTERS]
    return ("[out:json][timeout:25];\n(\n" + "\n".join(lines) + "\n);\n"
            f"out body geom{box} {MAX_ELEMENTS};\n")


def _kind(t: dict) -> str | None:
    """First match in the spec's table order; None for anything else."""
    natural, waterway, named = t.get("natural"), t.get("waterway"), bool(t.get("name"))
    if waterway == "waterfall":
        return "waterfall"
    if natural == "spring":
        return "spring"
    if natural == "hot_spring" or (t.get("amenity") == "public_bath" and t.get("bath:type") == "hot_spring"):
        return "hot_spring"
    if natural == "water" and t.get("water") in ("lake", "reservoir") and named:
        return "lake"
    if waterway == "river" and named:
        return "river"
    if natural == "peak":
        return "peak"
    if natural == "gorge" or (natural == "valley" and t.get("valley") in ("gorge", "canyon")):
        return "gorge"
    if natural == "cave_entrance":
        return "cave"
    if natural == "arch":
        return "arch"
    if natural == "rock" and named:
        return "rock"
    if t.get("tourism") == "viewpoint":
        return "viewpoint"
    if natural == "beach":
        return "beach"
    if (t.get("boundary") in ("protected_area", "national_park") or t.get("leisure") == "nature_reserve") and named:
        return "protected_area"
    if (t.get("landuse") == "forest" or natural == "wood") and named:
        return "forest"
    return None


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _segments(geometry) -> list[list[list[float]]]:
    """Split an Overpass geometry list into runs of valid points (clipping can leave gaps)."""
    out, run = [], []
    for p in geometry if isinstance(geometry, list) else []:
        if isinstance(p, dict) and _finite(p.get("lat"), p.get("lon")):
            run.append([float(p["lat"]), float(p["lon"])])
        else:
            if run:
                out.append(run)
            run = []
    if run:
        out.append(run)
    return out


def _seg_dist(p, a, b) -> float:
    (py, px), (ay, ax), (by, bx) = p, a, b
    dy, dx = by - ay, bx - ax
    if dx == 0 and dy == 0:
        return math.hypot(py - ay, px - ax)
    t = max(0.0, min(1.0, ((py - ay) * dy + (px - ax) * dx) / (dy * dy + dx * dx)))
    return math.hypot(py - (ay + t * dy), px - (ax + t * dx))


def simplify(points: list, tol: float) -> list:
    """Douglas–Peucker with an explicit stack (long rivers would overflow recursion)."""
    if len(points) <= 2:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = -1.0, -1
        for i in range(first + 1, last):
            d = _seg_dist(points[i], points[first], points[last])
            if d > best:
                best, index = d, i
        if index != -1 and best > tol:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def cap_lines(lines: list) -> list:
    """At most MAX_LINES lines and MAX_POINTS points per sight; lines that don't fit are skipped."""
    out, total = [], 0
    for line in lines:
        if len(out) >= MAX_LINES:
            break
        if total + len(line) > MAX_POINTS:
            continue
        out.append(line)
        total += len(line)
    return out


def _lines(el: dict) -> list:
    if el["type"] == "way":
        raw = _segments(el.get("geometry"))
    elif el["type"] == "relation":
        raw = [seg for m in el.get("members") or [] if isinstance(m, dict) and m.get("type") == "way"
               for seg in _segments(m.get("geometry"))]
    else:
        raw = []
    simplified = (simplify(line, TOLERANCE_DEG) for line in raw)
    return cap_lines([line for line in simplified if len(line) >= 2])


def _position(el: dict, lines: list) -> tuple[float, float] | None:
    if _finite(el.get("lat"), el.get("lon")):
        return float(el["lat"]), float(el["lon"])
    c = el.get("center")
    if isinstance(c, dict) and _finite(c.get("lat"), c.get("lon")):
        return float(c["lat"]), float(c["lon"])
    b = el.get("bounds")
    if isinstance(b, dict) and _finite(b.get("minlat"), b.get("minlon"), b.get("maxlat"), b.get("maxlon")):
        return (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
    pts = [p for line in lines for p in line]
    if pts:
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    return None


def _elevation(value) -> int | None:
    m = ELE_RE.match(str(value)) if value is not None else None
    return int(round(float(m.group(1)))) if m else None


def _wikipedia(value) -> str | None:
    m = WIKIPEDIA_RE.match(str(value).strip()) if value else None
    if not m:
        return None
    title = m.group(2).strip().replace("%", "%25").replace("?", "%3F").replace("#", "%23").replace(" ", "_")
    return f"https://{m.group(1)}.wikipedia.org/wiki/{title}" if title else None


def parse_overpass(data: dict) -> list[Sight]:
    sights = []
    for el in data.get("elements") or []:
        if not isinstance(el, dict) or el.get("type") not in ("node", "way", "relation"):
            continue
        if not isinstance(el.get("id"), int) or not isinstance(el.get("tags"), dict):
            continue
        tags = el["tags"]
        kind = _kind(tags)
        if kind is None:
            continue
        lines = _lines(el)
        pos = _position(el, lines)
        if pos is None:
            continue
        name = (str(tags.get("name:en") or "").strip() or str(tags.get("name") or "").strip()
                or SIGHT_LABELS[kind])
        sights.append(Sight(
            id=f"osm:{el['type']}/{el['id']}", name=name[:MAX_NAME], kind=kind, lat=pos[0], lon=pos[1],
            elevation_m=_elevation(tags.get("ele")), url=f"{OSM_URL}/{el['type']}/{el['id']}",
            wikipedia=_wikipedia(tags.get("wikipedia")), lines=lines))
    return sights


def make_fetch(transport: httpx.AsyncBaseTransport | None = None) -> FetchText:
    async def fetch(url: str, data: dict) -> str:
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT},
                                     timeout=TIMEOUT_S) as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            return response.text

    return fetch


class NatureSource:
    def __init__(self, fetch: FetchText | None = None):
        self._fetch = fetch or make_fetch()

    async def fetch_tile(self, south: float, west: float) -> list[Sight]:
        """All sights of one tile; tries each endpoint in turn, NatureUnavailable when none answers."""
        query = tile_query(south, west)
        for url in ENDPOINTS:
            try:
                data = json.loads(await self._fetch(url, {"data": query}))
            except Exception as exc:
                print(f"overpass {url} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
                print(f"overpass {url}: unexpected answer", file=sys.stderr)
                continue
            remark = str(data.get("remark") or "")
            if "error" in remark.lower():  # e.g. a timeout mid-query: partial results must not be cached
                print(f"overpass {url}: {remark[:200]}", file=sys.stderr)
                continue
            return parse_overpass(data)
        raise NatureUnavailable("Overpass did not answer")
