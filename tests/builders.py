"""Builders that mimic the real payload shapes of the scraped sites (probed 2026-10-09)."""


def wikiloc_spa(
    id,
    name,
    lat,
    lon,
    near="Portariá, Thessaly (Greece)",
    picto=1,
    distance="9,59",
    slope="563",
    skill=2,
    trailrank=80,
):
    return {
        "id": id,
        "prettyURL": f"/rutas-senderismo/route-{id}",
        "name": name,
        "picto": picto,
        "pictoText": "Senderismo" if picto == 1 else "Otro",
        "near": near,
        "trailrank": trailrank,
        "distance": distance,
        "slope": slope,
        "skill": skill,
        "lat": lat,
        "lon": lon,
        "author": "someone",
        "thumbs": [{"url": f"https://s0.wklcdn.com/{id}_tn.jpg", "id": 1}],
    }
