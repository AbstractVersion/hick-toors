from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    filters: StayFilters = Field(default_factory=StayFilters)

    @model_validator(mode="after")
    def _needs_center(self):
        if not self.hike_id and (self.lat is None or self.lon is None):
            raise ValueError("hike_id or lat/lon is required")
        return self


class TripQuery(TripDates):
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True
    area: DrawnArea | None = None  # when set, combos come from hikes inside the drawn shape
    hike_filters: HikeFilters = Field(default_factory=HikeFilters)
    stay_filters: StayFilters = Field(default_factory=StayFilters)


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


class HikeSearch(BaseModel):
    hikes: list[Hike] = []
    warnings: list[str] = []
    drive_limit_minutes: int | None = None


class StaySearch(BaseModel):
    stays: list[Stay] = []
    warnings: list[str] = []
    center: LatLon | None = None


class TripSearch(BaseModel):
    combos: list[Combo] = []
    warnings: list[str] = []
