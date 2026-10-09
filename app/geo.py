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
