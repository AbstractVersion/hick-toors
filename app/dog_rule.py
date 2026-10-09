from app.models import Badge, Stay

DOG_OK = ("pets", "pets_garden", "garden_unconfirmed")


def assign_badge(stay: Stay, dog: bool) -> Badge | None:
    if stay.pets_allowed is True:
        return "pets_garden" if stay.has_garden else "pets"
    if stay.has_garden:
        if not dog:
            return "garden"
        # A garden counts for the dog only if the listing hasn't explicitly refused pets.
        return "garden_unconfirmed" if stay.pets_allowed is None else None
    return None


def badge_tier(stay: Stay) -> int:
    return 1 if stay.badge == "garden_unconfirmed" else 0


def apply_dog_rule(stays: list[Stay], dog: bool) -> list[Stay]:
    out = []
    for stay in stays:
        badged = stay.model_copy(update={"badge": assign_badge(stay, dog)})
        if dog and badged.badge not in DOG_OK:
            continue
        out.append(badged)
    if dog:
        out.sort(key=badge_tier)  # stable: keeps provider order inside each tier
    return out
