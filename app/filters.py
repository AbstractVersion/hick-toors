"""Filter catalogue (served to the UI) and the filtering the engine does itself.

Wikiloc ignores filter params for anonymous callers, so every hike filter except `sort` is
applied here after fetching. Stay filters go to the providers as query params; price and rating
are re-checked here because providers apply them loosely.
"""

from app.models import Hike, HikeFilters, Stay, StayFilters

AIRBNB = "Airbnb"
BOOKING = "Booking.com"
BOTH = frozenset({AIRBNB, BOOKING})

ACTIVITY_OPTIONS = [(1, "Hiking"), (43, "Walking"), (48, "Trail running"), (57, "Nordic walking"),
                    (66, "Canicross (with dog)"), (60, "Via ferrata")]
DIFFICULTY_OPTIONS = [(1, "Easy"), (2, "Moderate"), (3, "Hard"), (4, "Very Hard"), (5, "Experts Only")]
PROPERTY_OPTIONS = [("house", "House"), ("apartment", "Apartment"), ("guesthouse", "Guesthouse / B&B"),
                    ("hotel", "Hotel"), ("villa", "Villa")]
RATING_OPTIONS = [(None, "Any"), (3.5, "3.5+"), (4.0, "4+"), (4.5, "4.5+"), (4.8, "4.8+")]
STAR_OPTIONS = [(None, "Any"), (1, "1+"), (2, "2+"), (3, "3+"), (4, "4+"), (5, "5")]

# amenity key -> (label, providers that can filter by it)
AMENITIES = {
    "wifi": ("Wifi", BOTH),
    "kitchen": ("Kitchen", BOTH),
    "parking": ("Free parking", BOTH),
    "pool": ("Pool", BOTH),
    "hot_tub": ("Hot tub", BOTH),
    "ac": ("Air conditioning", BOTH),
    "washer": ("Washer", BOTH),
    "ev_charger": ("EV charger", BOTH),
    "fireplace": ("Fireplace", frozenset({AIRBNB})),
    "heating": ("Heating", frozenset({AIRBNB})),
    "workspace": ("Workspace", frozenset({AIRBNB})),
    "bbq": ("BBQ grill", frozenset({AIRBNB})),
}

# stay field (or range group) -> (label, providers that honour it, server-side or by post-check)
STAY_FIELDS = {
    "price": ("Price per night", BOTH),
    "min_rating": ("Minimum rating", BOTH),
    "entire_place": ("Entire place", BOTH),
    "property_types": ("Property type", BOTH),
    "min_bedrooms": ("Bedrooms", BOTH),
    "min_beds": ("Beds", frozenset({AIRBNB})),
    "min_bathrooms": ("Bathrooms", BOTH),
    "free_cancellation": ("Free cancellation", BOTH),
    "instant_book": ("Instant book", frozenset({AIRBNB})),
    "guest_favorite": ("Guest favourite", frozenset({AIRBNB})),
    "breakfast": ("Breakfast included", frozenset({BOOKING})),
    "min_stars": ("Hotel stars", frozenset({BOOKING})),
    "beachfront": ("Beachfront", BOTH),
}

# Booking ORs repeated values of one nflt field, so only one value per field can be required.
BOOKING_HOTEL_FACILITY = {"parking", "wifi", "pool", "hot_tub", "ev_charger"}
BOOKING_ROOM_FACILITY = {"ac", "kitchen", "washer"}


def _source_tag(providers: frozenset) -> str:
    if providers == BOTH:
        return "Airbnb + Booking"
    return "Airbnb only" if AIRBNB in providers else "Booking only"


def _options(pairs, providers_by_value: dict | None = None) -> list[dict]:
    out = []
    for value, label in pairs:
        option = {"value": value, "label": label}
        if providers_by_value is not None:
            option["sources"] = _source_tag(providers_by_value[value])
        out.append(option)
    return out


def _stay(key: str, field: dict) -> dict:
    label, providers = STAY_FIELDS[key]
    return {"key": key, "label": label, "sources": _source_tag(providers), **field}


def catalogue() -> dict:
    """Everything the filter popups render: fields, options, defaults and source tags."""
    hike_fields = [
        {"key": "activities", "type": "multi", "label": "Activities", "sources": None,
         "options": _options(ACTIVITY_OPTIONS)},
        {"key": "difficulty", "type": "range", "label": "Difficulty", "sources": None,
         "min_key": "difficulty_min", "max_key": "difficulty_max", "options": _options(DIFFICULTY_OPTIONS)},
        {"key": "distance", "type": "range", "label": "Distance", "sources": None, "unit": "km",
         "min_key": "distance_min_km", "max_key": "distance_max_km", "min": 0, "max": 200, "step": 0.1},
        {"key": "elevation", "type": "range", "label": "Elevation gain", "sources": None, "unit": "m",
         "min_key": "elevation_min_m", "max_key": "elevation_max_m", "min": 0, "max": 5000, "step": 1},
        {"key": "min_trailrank", "type": "number", "label": "Minimum TrailRank", "sources": None,
         "min": 0, "max": 100, "step": 1},
        {"key": "sort", "type": "select", "label": "Wikiloc order", "sources": None,
         "options": _options([("popular", "Most popular"), ("top_rated", "Top rated (TrailRank)")])},
    ]
    stay_fields = [
        _stay("price", {"type": "range", "unit": "€/night", "min_key": "price_min", "max_key": "price_max",
                        "min": 0, "max": 2000, "step": 1}),
        _stay("min_rating", {"type": "select", "options": _options(RATING_OPTIONS)}),
        _stay("entire_place", {"type": "bool"}),
        _stay("property_types", {"type": "multi", "options": _options(PROPERTY_OPTIONS)}),
        _stay("min_bedrooms", {"type": "number", "min": 1, "max": 10, "step": 1}),
        _stay("min_beds", {"type": "number", "min": 1, "max": 16, "step": 1}),
        _stay("min_bathrooms", {"type": "number", "min": 1, "max": 8, "step": 1}),
        {"key": "amenities", "type": "multi", "label": "Amenities", "sources": None,
         "options": _options([(k, v[0]) for k, v in AMENITIES.items()],
                             {k: v[1] for k, v in AMENITIES.items()})},
        _stay("free_cancellation", {"type": "bool"}),
        _stay("instant_book", {"type": "bool"}),
        _stay("guest_favorite", {"type": "bool"}),
        _stay("breakfast", {"type": "bool"}),
        _stay("min_stars", {"type": "select", "options": _options(STAR_OPTIONS)}),
        _stay("beachfront", {"type": "bool"}),
    ]
    return {
        "hike": {"fields": hike_fields, "defaults": HikeFilters().model_dump()},
        "stay": {"fields": stay_fields, "defaults": StayFilters().model_dump()},
    }


def hike_filters_active(f: HikeFilters) -> bool:
    """True when a post-fetch hike filter narrows results (sort is applied by Wikiloc itself)."""
    return f.model_copy(update={"sort": "popular"}) != HikeFilters()


def stay_filters_active(f: StayFilters) -> bool:
    return f != StayFilters()


def _within(value, low, high) -> bool:
    if low is None and high is None:
        return True
    if value is None:  # the user asked for a range; an unknown value can't be shown as matching
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def apply_hike_filters(hikes: list[Hike], f: HikeFilters) -> list[Hike]:
    difficulty_filtered = (f.difficulty_min, f.difficulty_max) != (1, 5)
    out = []
    for h in hikes:
        if h.activity not in f.activities:
            continue
        if difficulty_filtered and not _within(h.skill, f.difficulty_min, f.difficulty_max):
            continue
        if not _within(h.distance_km, f.distance_min_km, f.distance_max_km):
            continue
        if not _within(h.elevation_gain_m, f.elevation_min_m, f.elevation_max_m):
            continue
        if not _within(h.trailrank, f.min_trailrank, None):
            continue
        out.append(h)
    return out


def stay_post_check(stays: list[Stay], f: StayFilters, nights: int) -> list[Stay]:
    """Enforce price per night and minimum rating; a stay with the value missing is kept."""
    nights = max(nights, 1)
    out = []
    for s in stays:
        if s.total_price is not None:
            per_night = s.total_price / nights
            if f.price_min is not None and per_night < f.price_min:
                continue
            if f.price_max is not None and per_night > f.price_max:
                continue
        if f.min_rating is not None and s.rating is not None and s.rating < f.min_rating:
            continue
        out.append(s)
    return out


def _active_fields(f: StayFilters) -> list[str]:
    defaults = StayFilters()
    keys = []
    for key in STAY_FIELDS:
        if key == "price":
            if f.price_min is not None or f.price_max is not None:
                keys.append(key)
        elif getattr(f, key) != getattr(defaults, key):
            keys.append(key)
    return keys


def unsupported_filters(provider_name: str, f: StayFilters, dog: bool = False) -> list[str]:
    """Labels of the active filters this provider can't honour (empty list = provider can run)."""
    labels = [STAY_FIELDS[k][0] for k in _active_fields(f) if provider_name not in STAY_FIELDS[k][1]]
    labels += [AMENITIES[a][0] for a in f.amenities if provider_name not in AMENITIES[a][1]]
    if provider_name == BOOKING:
        hotel = [AMENITIES[a][0] for a in f.amenities if a in BOOKING_HOTEL_FACILITY]
        if f.beachfront:
            hotel.append("Beachfront")
        if dog and hotel:
            hotel.insert(0, "Pets")
        room = [AMENITIES[a][0] for a in f.amenities if a in BOOKING_ROOM_FACILITY]
        for group in (hotel, room):
            if len(group) > 1:
                labels.append(" + ".join(group) + " together")
    return labels


def skip_notice(provider_name: str, labels: list[str]) -> str:
    return f"{provider_name} skipped – it can't filter by {', '.join(labels)}"
