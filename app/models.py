from datetime import date, datetime
from math import isfinite
from typing import Annotated, Literal, Union

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator,
                      model_validator)

Badge = Literal["pets", "pets_garden", "garden", "garden_unconfirmed"]
DriveLimit = Union[Annotated[int, Field(gt=0)], Literal["auto", "unlimited"]]
Activity = Literal[1, 43, 48, 57, 66, 60]  # Wikiloc picto ids offered as hike activities
PropertyType = Literal["house", "apartment", "guesthouse", "hotel", "villa"]
Amenity = Literal["wifi", "kitchen", "parking", "pool", "hot_tub", "ac", "washer", "ev_charger",
                  "fireplace", "heating", "workspace", "bbq"]


def _check_range(low, high, name: str) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError(f"{name}: minimum must not be greater than maximum")


def _unique(values: list) -> list:
    return list(dict.fromkeys(values))


class LatLon(BaseModel):
    lat: float
    lon: float


class BBox(BaseModel):
    south: float
    west: float
    north: float
    east: float


class Hike(BaseModel):
    id: str
    name: str
    url: str
    lat: float
    lon: float
    distance_km: float | None = None
    elevation_gain_m: int | None = None
    difficulty: str | None = None
    trailrank: int | None = None
    near: str | None = None
    thumbnail: str | None = None
    drive_minutes: int | None = None
    drive_estimated: bool = False
    ferry_needed: bool = False
    activity: int | None = None  # Wikiloc picto id (1 = Hiking)
    skill: int | None = None  # Wikiloc difficulty 1-5


class Stay(BaseModel):
    id: str
    source: Literal["airbnb", "booking"]
    name: str
    url: str
    lat: float
    lon: float
    total_price: float | None = None
    currency: str | None = None
    rating: float | None = None  # 0-5 scale
    photo: str | None = None
    pets_allowed: bool | None = None
    has_garden: bool | None = None
    badge: Badge | None = None
    distance_km: float | None = None


class ScoreBreakdown(BaseModel):
    hike: float
    stay: float
    proximity: float
    price: float


class Combo(BaseModel):
    hike: Hike
    stay: Stay
    score: float
    breakdown: ScoreBreakdown


class TripDates(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end < self.start:
            raise ValueError("end date must be on or after start date")
        return self


class HikeFilters(BaseModel):
    """Hike filters; the defaults reproduce v1 (hiking routes only, popular order)."""

    model_config = ConfigDict(extra="forbid")

    activities: list[Activity] = Field(default_factory=lambda: [1], min_length=1)
    difficulty_min: int = Field(default=1, ge=1, le=5)
    difficulty_max: int = Field(default=5, ge=1, le=5)
    distance_min_km: float | None = Field(default=None, ge=0)
    distance_max_km: float | None = Field(default=None, ge=0)
    elevation_min_m: int | None = Field(default=None, ge=0)
    elevation_max_m: int | None = Field(default=None, ge=0)
    min_trailrank: int | None = Field(default=None, ge=0, le=100)
    loop_only: bool = False  # circular routes; Wikiloc server-side only (needs WIKILOC_COOKIE)
    sort: Literal["popular", "top_rated"] = "popular"

    _dedupe = field_validator("activities")(_unique)

    @model_validator(mode="after")
    def _ranges(self):
        _check_range(self.difficulty_min, self.difficulty_max, "difficulty")
        _check_range(self.distance_min_km, self.distance_max_km, "distance")
        _check_range(self.elevation_min_m, self.elevation_max_m, "elevation gain")
        return self


class StayFilters(BaseModel):
    """Stay filters; every default means "no filter"."""

    model_config = ConfigDict(extra="forbid")

    price_min: int | None = Field(default=None, ge=0)  # EUR per night
    price_max: int | None = Field(default=None, ge=0)  # EUR per night
    min_rating: float | None = Field(default=None, ge=0, le=5)
    entire_place: bool = False
    property_types: list[PropertyType] = []
    min_bedrooms: int | None = Field(default=None, ge=1, le=10)
    min_beds: int | None = Field(default=None, ge=1, le=16)
    min_bathrooms: int | None = Field(default=None, ge=1, le=8)
    amenities: list[Amenity] = []
    free_cancellation: bool = False
    instant_book: bool = False
    guest_favorite: bool = False
    breakfast: bool = False
    min_stars: int | None = Field(default=None, ge=1, le=5)
    beachfront: bool = False

    _dedupe = field_validator("property_types", "amenities")(_unique)

    @model_validator(mode="after")
    def _ranges(self):
        _check_range(self.price_min, self.price_max, "price per night")
        return self


class RectangleArea(BaseModel):
    shape: Literal["rectangle"]
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _ordered(self):
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("drawn rectangle needs south < north and west < east")
        return self


class CircleArea(BaseModel):
    shape: Literal["circle"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(ge=200, le=300_000)


DrawnArea = Annotated[Union[RectangleArea, CircleArea], Field(discriminator="shape")]


class HikeQuery(TripDates):
    mode: Literal["place", "near_home", "drawn"] = "near_home"
    place: str | None = None
    drive_limit: DriveLimit = "auto"
    area: DrawnArea | None = None
    filters: HikeFilters = Field(default_factory=HikeFilters)
    page_offset: int = Field(0, ge=0, le=400)  # Load more: Wikiloc pages (25 routes each) to skip

    @model_validator(mode="after")
    def _mode_inputs(self):
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        if self.mode == "drawn" and self.area is None:
            raise ValueError("Draw a rectangle or circle first")
        return self


class StaySearchQuery(TripDates):
    hike_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = Field(default=None, gt=0, le=100)
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    area: DrawnArea | None = None  # stays inside a drawn shape; hike_id > lat/lon > area
    providers: list[Literal["airbnb", "booking"]] | None = Field(default=None, min_length=1)  # None = all
    filters: StayFilters = Field(default_factory=StayFilters)

    @model_validator(mode="after")
    def _needs_center(self):
        if not self.hike_id and self.area is None and (self.lat is None or self.lon is None):
            raise ValueError("hike_id or lat/lon (or a drawn area) is required")
        return self


class TripQuery(TripDates):
    mode: Literal["near_home", "place", "drawn"] = "near_home"  # WHERE the hikes come from
    place: str | None = None
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    area: DrawnArea | None = None  # drawn mode: combos come from hikes inside the shape
    hike_filters: HikeFilters = Field(default_factory=HikeFilters)
    stay_filters: StayFilters = Field(default_factory=StayFilters)

    @model_validator(mode="after")
    def _mode_inputs(self):
        if "mode" not in self.model_fields_set and self.area is not None:
            self.mode = "drawn"  # backward compatible: an area alone meant a drawn search
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        if self.mode == "drawn" and self.area is None:
            raise ValueError("Draw a rectangle or circle first")
        return self


class StayQuery(BaseModel):
    """What a single stay provider is asked for."""

    lat: float
    lon: float
    radius_km: float
    check_in: date
    check_out: date
    guests: int
    dog: bool
    filters: StayFilters = Field(default_factory=StayFilters)
    bbox: BBox | None = None  # exact search box for drawn-area searches (None = hike/point searches)


class HikeSearch(BaseModel):
    hikes: list[Hike] = []
    warnings: list[str] = []
    drive_limit_minutes: int | None = None
    total_available: int | None = None  # Wikiloc's route count for the whole area (None = unknown)
    next_page_offset: int | None = None  # page_offset for Load more; None = nothing more to load


class StaySearch(BaseModel):
    stays: list[Stay] = []
    warnings: list[str] = []
    center: LatLon | None = None


class TripSearch(BaseModel):
    combos: list[Combo] = []
    warnings: list[str] = []


class StayLookup(BaseModel):
    """Body of POST /api/stays/lookup: a pasted Airbnb or Booking.com link plus the trip's dates/guests/dog."""

    url: str = Field(min_length=1, max_length=2000)
    start: date | None = None
    end: date | None = None
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start and self.end and self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


class StayLookupResult(BaseModel):
    stay: Stay
    warnings: list[str] = []


SightKind = Literal["waterfall", "spring", "hot_spring", "lake", "river", "peak", "gorge", "cave", "arch", "rock",
                    "viewpoint", "beach", "protected_area", "forest"]
SightGroup = Literal["water", "landforms", "views", "protected"]
SIGHT_GROUPS: dict[str, list[str]] = {
    "water": ["waterfall", "spring", "hot_spring", "lake", "river"],
    "landforms": ["peak", "gorge", "cave", "arch", "rock"],
    "views": ["viewpoint", "beach"],
    "protected": ["protected_area", "forest"],
}
SIGHT_LABELS: dict[str, str] = {
    "waterfall": "Waterfall", "spring": "Spring", "hot_spring": "Hot spring", "lake": "Lake", "river": "River",
    "peak": "Peak", "gorge": "Gorge", "cave": "Cave", "arch": "Arch", "rock": "Rock", "viewpoint": "Viewpoint",
    "beach": "Beach", "protected_area": "Protected area", "forest": "Forest",
}
SIGHT_EMOJI: dict[str, str] = {
    "waterfall": "💧", "spring": "🚰", "hot_spring": "♨️", "lake": "🏞️", "river": "🌊", "peak": "⛰️", "gorge": "🏔️",
    "cave": "🕳️", "arch": "🪨", "rock": "🪨", "viewpoint": "👁️", "beach": "🏖️", "protected_area": "🌲",
    "forest": "🌳",
}


class Sight(BaseModel):
    """A natural sight from OpenStreetMap (id "osm:<type>/<id>")."""

    id: str
    name: str
    kind: SightKind
    lat: float
    lon: float
    elevation_m: int | None = None
    url: str
    wikipedia: str | None = None
    lines: list[list[list[float]]] = []  # rivers / area outlines: [[[lat, lon], …], …], clipped + simplified


class NatureSearch(BaseModel):
    sights: list[Sight] = []
    warnings: list[str] = []


MAX_TRIP_ITEMS = 50
MAX_TRIP_SIGHTS = 100
MAX_TRIP_BYTES = 256 * 1024  # serialized SavedTripIn fields of one trip
TRIP_TOO_LARGE = "Trip is too large (max 256 KB)"


def _unique_by_id(items: list) -> list:
    seen: set[str] = set()
    out = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            out.append(item)
    return out


def _check_item_numbers(item: "Hike | Stay | Sight", where: str) -> None:
    # Hike/Stay accept anything float (scrapers build them); saved trips must be JSON-safe:
    # NaN/Infinity would be stored as null and break every later load.
    if not (isfinite(item.lat) and isfinite(item.lon) and -90 <= item.lat <= 90 and -180 <= item.lon <= 180):
        raise ValueError(f"{where}: lat/lon must be finite coordinates")
    for name in type(item).model_fields:
        value = getattr(item, name)
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"{where}: {name} must be a finite number")
    if isinstance(item, Stay) and item.total_price is not None and item.total_price < 0:
        raise ValueError(f"{where}: total_price must not be negative")  # prices can be typed in by hand


def _finite_items(items: list, info: ValidationInfo) -> list:
    for i, item in enumerate(items):
        _check_item_numbers(item, f"{info.field_name}.{i}")
    return _unique_by_id(items)


def trip_size(trip: "SavedTripIn") -> int:
    """Bytes of the trip's SavedTripIn fields as JSON (id/timestamps excluded, so POST and load agree)."""
    return len(trip.model_dump_json(include=set(SavedTripIn.model_fields)).encode())


# Stored documents are re-validated against SavedTrip (this model + id/timestamps) on every load, and rows
# that fail are skipped. Keep field changes backward-compatible: only add optional fields; don't narrow
# Literals or limits without a migration (bump PRAGMA user_version in TripStore).
class SavedTripIn(BaseModel):
    """Body of POST /api/saved-trips: a named bucket of hikes and stays (snapshots as seen)."""

    name: str = Field(min_length=1, max_length=120)
    start: date | None = None
    end: date | None = None
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    notes: str = Field(default="", max_length=5000)
    source: Literal["manual", "suggested"] = "manual"
    score: float | None = Field(default=None, ge=0, le=100)
    hikes: list[Hike] = Field(default_factory=list, max_length=MAX_TRIP_ITEMS)
    stays: list[Stay] = Field(default_factory=list, max_length=MAX_TRIP_ITEMS)
    sights: list[Sight] = Field(default_factory=list, max_length=MAX_TRIP_SIGHTS)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    _check_items = field_validator("hikes", "stays", "sights")(_finite_items)  # also dedupes by id

    @field_validator("sights")
    @classmethod
    def _drop_lines(cls, sights):
        # Outlines are for the map layer only; keeping them out of trips keeps documents small.
        return [s.model_copy(update={"lines": []}) for s in sights]

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start and self.end and self.end < self.start:
            raise ValueError("end must be on or after start")
        return self

    @model_validator(mode="after")
    def _check_size(self):
        if trip_size(self) > MAX_TRIP_BYTES:
            raise ValueError(TRIP_TOO_LARGE)
        return self


class SavedTripUpdate(SavedTripIn):
    """Body of PUT /api/saved-trips/{id}: optionally the updated_at the editor started from."""

    expected_updated_at: datetime | None = None


class SavedTrip(SavedTripIn):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None  # username; server-set on create, never taken from a request body


class SavedTripSummary(BaseModel):
    id: str
    name: str
    start: date | None = None
    end: date | None = None
    source: Literal["manual", "suggested"] = "manual"
    hike_count: int
    stay_count: int
    sight_count: int = 0
    updated_at: datetime
    created_by: str | None = None


class NearbySightsIn(BaseModel):
    """Body of POST /api/saved-trips/{id}/nearby-sights (optional)."""

    radius_km: float = Field(default=5, ge=1, le=10)


class NearbySightsResult(BaseModel):
    trip: SavedTrip
    added: int
    found: int
    warnings: list[str] = []


class TripItemAdd(BaseModel):
    """Body of POST /api/saved-trips/{id}/items: exactly one hike, stay or sight."""

    hike: Hike | None = None
    stay: Stay | None = None
    sight: Sight | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [(name, v) for name, v in (("hike", self.hike), ("stay", self.stay), ("sight", self.sight))
                 if v is not None]
        if len(given) != 1:
            raise ValueError("Give exactly one of hike, stay or sight")
        _check_item_numbers(given[0][1], given[0][0])
        return self


# --- group voting rounds ---------------------------------------------------

Rating = Literal["love", "ok", "nogo"]
RoundStatus = Literal["open", "closed", "cancelled"]
MAX_ROUND_TRIPS = 50


class VoteIn(BaseModel):
    """Body of PUT …/vote: a rating and an optional short comment (trimmed, empty → None)."""

    rating: Rating
    comment: str | None = Field(default=None, max_length=140)

    @field_validator("comment", mode="before")
    @classmethod
    def _trim_comment(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class RoundIn(BaseModel):
    """Body of POST /api/rounds. Deadline bounds and trip existence are checked by the store."""

    title: str = Field(min_length=1, max_length=80)
    closes_at: AwareDatetime
    trip_ids: list[str] = Field(min_length=1, max_length=MAX_ROUND_TRIPS)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    _dedupe = field_validator("trip_ids")(_unique)


class RoundPatch(BaseModel):
    """Body of PATCH /api/rounds/{id} (opener only)."""

    closes_at: AwareDatetime | None = None
    add_trip_ids: list[str] = Field(default_factory=list, max_length=MAX_ROUND_TRIPS)


class VoteOut(BaseModel):
    username: str
    rating: Rating
    comment: str | None = None


class Counts(BaseModel):
    love: int = 0
    ok: int = 0
    nogo: int = 0


class StayStanding(BaseModel):
    stay_id: str
    name: str
    rank: int
    score: int
    counts: Counts
    votes: list[VoteOut]
    my_rating: Rating | None = None


class TripStanding(BaseModel):
    trip_id: str
    name: str
    rank: int
    score: int
    counts: Counts
    votes: list[VoteOut]
    my_rating: Rating | None = None
    stays: list[StayStanding]
    position: int = 0  # the trip's place in the round (fixed: the guide walks everyone through the same order)


class Me(BaseModel):
    rated: int
    total: int
    love_trip_id: str | None = None
    joined: bool = False


class Winner(BaseModel):
    trip_id: str
    trip_name: str
    stay_id: str | None = None
    stay_name: str | None = None


class RoundView(BaseModel):
    id: str
    title: str
    opener: str
    status: RoundStatus
    created_at: datetime
    closes_at: datetime
    closed_at: datetime | None = None
    trips: list[TripStanding]
    waiting_for: list[str]
    participants: list[str] = []
    me: Me | None = None
    winner: Winner | None = None


class RoundSummary(BaseModel):
    id: str
    title: str
    status: RoundStatus
    opener: str
    closes_at: datetime
    closed_at: datetime | None = None
    winner_name: str | None = None


class VoteResult(BaseModel):
    round: RoundView
    moved_love_from: str | None = None
