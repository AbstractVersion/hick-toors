from app.dog_rule import badge_tier
from app.geo import haversine_km
from app.models import Combo, Hike, LatLon, ScoreBreakdown, Stay

NEUTRAL_HIKE = 0.5
NEUTRAL_STAY = 0.6
NEUTRAL_PRICE = 0.5


def rank_combos(
    pairs: list[tuple[Hike, Stay]], weights: dict[str, float], radius_km: float, dog: bool
) -> list[Combo]:
    prices = [s.total_price for _, s in pairs if s.total_price is not None]
    lo, hi = (min(prices), max(prices)) if prices else (0.0, 0.0)
    combos = []
    for hike, stay in pairs:
        hike_n = min(max(hike.trailrank / 100, 0.0), 1.0) if hike.trailrank is not None else NEUTRAL_HIKE
        stay_n = min(max(stay.rating / 5, 0.0), 1.0) if stay.rating is not None else NEUTRAL_STAY
        km = haversine_km(LatLon(lat=hike.lat, lon=hike.lon), LatLon(lat=stay.lat, lon=stay.lon))
        prox_n = max(0.0, 1 - km / radius_km) if radius_km > 0 else 0.0
        if stay.total_price is None:
            price_n = NEUTRAL_PRICE
        elif hi == lo:
            price_n = 1.0
        else:
            price_n = (hi - stay.total_price) / (hi - lo)
        breakdown = ScoreBreakdown(
            hike=round(weights["hike"] * hike_n, 1),
            stay=round(weights["stay"] * stay_n, 1),
            proximity=round(weights["proximity"] * prox_n, 1),
            price=round(weights["price"] * price_n, 1),
        )
        score = round(breakdown.hike + breakdown.stay + breakdown.proximity + breakdown.price, 1)
        combos.append(Combo(hike=hike, stay=stay, score=score, breakdown=breakdown))
    combos.sort(key=lambda c: (badge_tier(c.stay) if dog else 0, -c.score))
    return combos
