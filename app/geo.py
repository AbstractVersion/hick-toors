import math

from app.models import BBox, LatLon

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT = 111.32
GREECE_BBOX = BBox(south=34.7, west=19.3, north=41.8, east=28.3)


def haversine_km(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bbox_around(center: LatLon, radius_km: float) -> BBox:
    dlat = radius_km / KM_PER_DEG_LAT
    dlon = radius_km / (KM_PER_DEG_LAT * math.cos(math.radians(center.lat)))
    return BBox(
        south=center.lat - dlat,
        west=center.lon - dlon,
        north=center.lat + dlat,
        east=center.lon + dlon,
    )


def clamp_to_greece(box: BBox) -> BBox:
    g = GREECE_BBOX
    return BBox(
        south=max(box.south, g.south),
        west=max(box.west, g.west),
        north=min(box.north, g.north),
        east=min(box.east, g.east),
    )


def center_of(box: BBox) -> LatLon:
    return LatLon(lat=(box.south + box.north) / 2, lon=(box.west + box.east) / 2)


def ensure_min_size(box: BBox, radius_km: float) -> BBox:
    m = bbox_around(center_of(box), radius_km)
    return BBox(
        south=min(box.south, m.south),
        west=min(box.west, m.west),
        north=max(box.north, m.north),
        east=max(box.east, m.east),
    )


def segment_distance_km(p: LatLon, a: LatLon, b: LatLon) -> float:
    """Distance from p to the segment a–b (flat projection around p: exact enough for a few tens of km)."""
    kx = KM_PER_DEG_LAT * math.cos(math.radians(p.lat))
    ax, ay = (a.lon - p.lon) * kx, (a.lat - p.lat) * KM_PER_DEG_LAT
    bx, by = (b.lon - p.lon) * kx, (b.lat - p.lat) * KM_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _in_ring(p: LatLon, ring: list[list[float]]) -> bool:
    """Even-odd ray casting (ray towards +lon) over a ring of [lat, lon] points."""
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(ring, ring[1:]):
        if (lat1 > p.lat) != (lat2 > p.lat):
            cross_lon = lon1 + (p.lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            if p.lon < cross_lon:
                inside = not inside
    return inside


def point_in_rings(p: LatLon, lines: list[list[list[float]]]) -> bool:
    """Is p inside any closed ring (first point == last, at least 4 points) of an area outline?

    Inner rings (holes) are not subtracted: a point in a hole still counts as inside. When no line is a
    closed ring (outlines clipped at tile edges or simplified open), inside the lines' bounding box counts.
    """
    rings = [line for line in lines if len(line) >= 4 and line[0] == line[-1]]
    if rings:
        return any(_in_ring(p, ring) for ring in rings)
    points = [q for line in lines for q in line]
    if not points:
        return False
    lats, lons = [q[0] for q in points], [q[1] for q in points]
    return min(lats) <= p.lat <= max(lats) and min(lons) <= p.lon <= max(lons)
