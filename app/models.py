from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

Badge = Literal["pets", "pets_garden", "garden", "garden_unconfirmed"]
DriveLimit = Union[Annotated[int, Field(gt=0)], Literal["auto", "unlimited"]]


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


class HikeQuery(TripDates):
    mode: Literal["place", "near_home"] = "near_home"
    place: str | None = None
    drive_limit: DriveLimit = "auto"

    @model_validator(mode="after")
    def _place_required(self):
        if self.mode == "place" and not (self.place and self.place.strip()):
            raise ValueError("place is required in place mode")
        return self


class StaySearchQuery(TripDates):
    hike_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = Field(default=None, gt=0, le=100)
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True

    @model_validator(mode="after")
    def _needs_center(self):
        if not self.hike_id and (self.lat is None or self.lon is None):
            raise ValueError("hike_id or lat/lon is required")
        return self


class TripQuery(TripDates):
    drive_limit: DriveLimit = "auto"
    guests: int = Field(default=2, ge=1, le=16)
    dog: bool = True


class StayQuery(BaseModel):
    """What a single stay provider is asked for."""

    lat: float
    lon: float
    radius_km: float
    check_in: date
    check_out: date
    guests: int
    dog: bool


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
