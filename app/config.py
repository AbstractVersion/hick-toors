from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    ors_api_key: str | None = None
    home_name: str = "Athens"
    home_lat: float = 37.9838
    home_lon: float = 23.7275
    day_trip_max_minutes: int = 90
    weekend_max_minutes: int = 240
    stay_radius_km: float = 15.0
    default_guests: int = 2
    airbnb_detail_top_n: int = 10
    airbnb_pages: int = 3  # result pages per Airbnb search in drawn areas (hike-centred searches: 1)
    booking_pages: int = 2  # result pages for Booking in drawn areas (hike-centred searches: 1)
    hike_pages: int = 20  # 25 routes per Wikiloc request
    hike_pages_filtered: int = 30  # anonymous + hike filters: fetch up to 750 routes, filter locally
    wikiloc_concurrency: int = 4  # Wikiloc pages fetched in parallel
    wikiloc_cookie: str | None = Field(default=None, repr=False)  # WIKILOC_COOKIE: logged-in session cookie -> server-side filters
    max_hikes: int = 500
    trip_top_hikes: int = 5
    trip_stays_per_hike: int = 3
    trip_max_combos: int = 10
    weight_hike: float = 40
    weight_stay: float = 25
    weight_proximity: float = 20
    weight_price: float = 15
    cache_dir: Path = BASE_DIR / ".cache"
    claude_bin: str = "claude"
    ask_timeout_seconds: int = 120

    @property
    def weights(self) -> dict[str, float]:
        return {
            "hike": self.weight_hike,
            "stay": self.weight_stay,
            "proximity": self.weight_proximity,
            "price": self.weight_price,
        }
