"""A Wikiloc hike's full track, for drawing it on the map.

Wikiloc's search returns only a start point; each trail page embeds `var mapData = {"mapData": [{..., "geom":
"<base64>"}]}` where `geom` is TWKB (Tiny Well-Known Binary, https://github.com/TWKB/Specification) – its own
map decodes it with `twkb.toGeoJSON(base64ToUint8Array(geom))` (probed 2026-10-11). The track is simplified and
stored as a Google-encoded polyline (precision 5) on the hike inside the trip.
"""

import asyncio
import base64
import binascii
import json
import re
import sys
import time
from typing import Awaitable, Callable

import httpx

from app.sources.nature import simplify
from app.sources.wikiloc import BASE_URL, USER_AGENT

ROUTE_TOLERANCE_DEG = 0.00005
MAX_ROUTE_POINTS = 400
ROUTE_TTL = 365 * 86400  # tracks don't change: a fetched route is kept for a year
ROUTE_DEADLINE_S = 15.0
ROUTE_CONCURRENCY = 4
ROUTE_COOLDOWN_S = 600.0  # after a blocked/failed page fetch, don't try Wikiloc again for 10 minutes
CACHE_PREFIX = "route:v1:"

LatLonPoint = tuple[float, float]
FetchPage = Callable[[str], Awaitable[str | None]]

_MAPDATA = re.compile(r"var\s+mapData\s*=\s*")
_NUMBER = re.compile(r"\d{1,12}")


# --- TWKB ------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("truncated TWKB")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def varint(self) -> int:
        result, shift = 0, 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
            if shift > 63:
                raise ValueError("varint too long")

    def svarint(self) -> int:
        n = self.varint()
        return (n >> 1) ^ -(n & 1)  # zigzag


def decode_twkb(data: bytes) -> list[list[LatLonPoint]] | None:
    """TWKB LineString (2) / MultiLineString (5) → lines of (lat, lon); extra dimensions (Z/M) are skipped.

    None for other geometry types, empty geometries and malformed input."""
    try:
        r = _Reader(data)
        type_byte = r.byte()
        kind = type_byte & 0x0F
        precision = (type_byte >> 4 >> 1) ^ -((type_byte >> 4) & 1)
        meta = r.byte()
        has_bbox, has_size, has_ids, has_ext, empty = (bool(meta & bit) for bit in (1, 2, 4, 8, 16))
        dims = 2
        if has_ext:
            ext = r.byte()
            dims += (ext & 1) + ((ext >> 1) & 1)
        if has_size:
            r.varint()
        if has_bbox:
            for _ in range(dims * 2):
                r.varint()
        if empty or kind not in (2, 5):
            return None
        scale = 10.0 ** precision
        prev = [0] * dims  # deltas run on across every part of the geometry
        parts = 1
        if kind == 5:
            parts = r.varint()
            if has_ids:
                for _ in range(parts):
                    r.varint()
        lines = []
        for _ in range(parts):
            count = r.varint()
            if count > len(data):  # each point takes at least `dims` bytes: a bigger count is garbage
                raise ValueError("bad point count")
            line = []
            for _ in range(count):
                for d in range(dims):
                    prev[d] += r.svarint()
                line.append((round(prev[1] / scale, 7), round(prev[0] / scale, 7)))
            lines.append(line)
        return lines if any(lines) else None
    except ValueError:
        return None


def parse_route(html: str) -> list[LatLonPoint] | None:
    """The first track on a Wikiloc trail page as (lat, lon) points (parts of a multi-line joined), or None."""
    match = _MAPDATA.search(html or "")
    if not match:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, match.end())
        geom = data["mapData"][0]["geom"]
        raw = base64.b64decode(geom + "=" * (-len(geom) % 4), validate=True)
    except (ValueError, KeyError, IndexError, TypeError, binascii.Error):
        return None
    lines = decode_twkb(raw)
    if not lines:
        return None
    points = [p for line in lines for p in line]
    return points if len(points) >= 2 else None


# --- simplify + polyline -----------------------------------------------------

def simplify_route(points: list[LatLonPoint]) -> list[LatLonPoint]:
    """Douglas–Peucker at ROUTE_TOLERANCE_DEG; the tolerance doubles until at most MAX_ROUTE_POINTS remain."""
    tol = ROUTE_TOLERANCE_DEG
    out = simplify(list(points), tol)
    while len(out) > MAX_ROUTE_POINTS and tol < 0.002:
        tol *= 2
        out = simplify(out, tol)
    if len(out) > MAX_ROUTE_POINTS:  # pathological zig-zag: keep every n-th point (and the last)
        step = -(-len(out) // (MAX_ROUTE_POINTS - 1))
        out = out[::step] + ([out[-1]] if (len(out) - 1) % step else [])
    return out


def encode_polyline(points: list[LatLonPoint]) -> str:
    """Google's encoded polyline algorithm, precision 5."""
    out = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def decode_polyline(text: str) -> list[LatLonPoint]:
    """Inverse of encode_polyline; ValueError on a truncated / invalid string."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        deltas = []
        for _ in range(2):
            result, shift = 0, 0
            while True:
                if index >= len(text):
                    raise ValueError("truncated polyline")
                b = ord(text[index]) - 63
                index += 1
                if b < 0 or b > 63:
                    raise ValueError("invalid polyline character")
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / 1e5, lon / 1e5))
    return points


def route_points(points: list[LatLonPoint] | None) -> str | None:
    """Raw track → stored polyline (None when there is no usable line)."""
    if not points:
        return None
    simple = simplify_route(points)
    if len(simple) < 2 or all(p == simple[0] for p in simple):
        return None
    return encode_polyline(simple)


def wikiloc_number(hike_id: str) -> str | None:
    """'wikiloc:231747769' (or the bare number) → '231747769'; None for other ids."""
    number = hike_id.removeprefix("wikiloc:")
    return number if _NUMBER.fullmatch(number) else None


# --- fetching ------------------------------------------------------------------

def make_page_fetch(transport: httpx.AsyncBaseTransport | None = None,
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, retries: int = 2) -> FetchPage:
    """HTML fetcher with Wikiloc's User-Agent (anonymous: never the session cookie), retrying its intermittent 403s.

    A 404 / 410 is final and answers "" (no track there – not a failure). Returns None when every attempt failed."""

    async def fetch(url: str) -> str | None:
        async with httpx.AsyncClient(transport=transport, headers={"User-Agent": USER_AGENT},
                                     follow_redirects=True) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.get(url, timeout=12.0)
                    if response.status_code in (404, 410):
                        return ""
                    response.raise_for_status()
                    return response.text
                except httpx.HTTPError as exc:
                    print(f"wikiloc route request failed: {exc}", file=sys.stderr)
                    if attempt < retries:
                        await sleep(1.5 * (attempt + 1))
        return None

    return fetch


class RouteFetcher:
    """Routes by hike id: disk cache first (ROUTE_TTL), then the trail page (if `online`).

    At most `concurrency` pages at a time, each within `deadline_s`. A failed fetch is not cached, but after one
    Wikiloc is left alone for `cooldown_s` (from the VM every page answers 403: don't make each save wait)."""

    def __init__(self, cache=None, fetch_page: FetchPage | None = None, online: bool = True,
                 concurrency: int = ROUTE_CONCURRENCY, deadline_s: float = ROUTE_DEADLINE_S,
                 cooldown_s: float = ROUTE_COOLDOWN_S, clock: Callable[[], float] = time.monotonic):
        self.cache = cache
        self._fetch_page = fetch_page or make_page_fetch()
        self.online = online
        self._concurrency = max(1, concurrency)
        self._semaphore: asyncio.Semaphore | None = None
        self._deadline = deadline_s
        self._cooldown = cooldown_s
        self._clock = clock
        self._blocked_until = 0.0

    def cached(self, hike_id: str) -> str | None:
        number = wikiloc_number(hike_id)
        if number is None or self.cache is None:
            return None
        value = self.cache.get(CACHE_PREFIX + number)
        return value if isinstance(value, str) and value else None

    async def route(self, hike_id: str) -> str | None:
        number = wikiloc_number(hike_id)
        if number is None:
            return None
        if self.cache is not None:
            hit = await asyncio.to_thread(self.cached, hike_id)
            if hit:
                return hit
        if not self.online or self._clock() < self._blocked_until:
            return None
        if self._semaphore is None:  # created lazily: it belongs to the running event loop
            self._semaphore = asyncio.Semaphore(self._concurrency)
        async with self._semaphore:
            if self._clock() < self._blocked_until:
                return None
            try:
                html = await asyncio.wait_for(self._fetch_page(f"{BASE_URL}/wikiloc/view.do?id={number}"),
                                              self._deadline)
            except asyncio.TimeoutError:
                html = None
            if html is None:
                self._blocked_until = self._clock() + self._cooldown
                return None
            route = route_points(await asyncio.to_thread(parse_route, html))
        if route and self.cache is not None:
            await asyncio.to_thread(self.cache.set, CACHE_PREFIX + number, route, ROUTE_TTL)
        return route

    async def many(self, hike_ids: list[str]) -> dict[str, str | None]:
        ids = list(dict.fromkeys(hike_ids))
        routes = await asyncio.gather(*(self.route(i) for i in ids))
        return dict(zip(ids, routes))
