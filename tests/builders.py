"""Builders that mimic the real payload shapes of the scraped sites (probed 2026-10-09)."""

import base64
import json


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


def airbnb_result(listing_id, title, lat, lon, label="€202 total",
                  rating="4.85 out of 5 average rating,  26 reviews"):
    encoded = base64.b64encode(f"DemandStayListing:{listing_id}".encode()).decode()
    return {
        "__typename": "StaySearchResult",
        "avgRatingA11yLabel": rating,
        "title": "Cottage in Makrinitsa",
        "subtitle": title,
        "nameLocalized": {"localizedStringWithTranslationPreference": title},
        "structuredDisplayPrice": {"primaryLine": {"accessibilityLabel": label, "price": label.split()[0]}},
        "contextualPictures": [{"picture": f"https://a0.muscache.com/{listing_id}.jpg"}],
        "demandStayListing": {
            "id": encoded,
            "location": {"coordinate": {"latitude": lat, "longitude": lon}},
        },
    }


def airbnb_search_html(results, cursors=None):
    page = {"searchResults": results}
    if cursors is not None:
        page["paginationInfo"] = {"pageCursors": cursors}
    state = {"niobeClientData": [["StaysSearch:{}", {"data": {"presentation": {
        "staysSearch": {"results": page}}}}]]}
    return ('<html><body><script id="data-deferred-state-0" type="application/json">'
            f"{json.dumps(state)}</script></body></html>")


def airbnb_listing_html(titles):
    blob = json.dumps({"sections": [{"title": t} for t in titles]},
                      ensure_ascii=False, separators=(",", ":"))
    return f"<html><script>{blob}</script></html>"


def booking_property(pid, name, lat, lon, score=9.6, amount=354.0, page="anna-villa"):
    return {
        "__typename": "SearchResultProperty",
        "displayName": {"text": name},
        "basicPropertyData": {
            "id": pid,
            "pageName": page,
            "location": {"latitude": lat, "longitude": lon, "countryCode": "gr"},
            "reviews": {"totalScore": score},
            "photos": {"main": {"highResUrl": {"relativeUrl": f"/xdata/images/hotel/square600/{pid}.webp"}}},
        },
        "priceDisplayInfoIrene": {"displayPrice": {"amountPerStay": {"amountUnformatted": amount, "currency": "EUR"}}},
    }


def booking_search_html(props):
    store = {"ROOT_QUERY": {"searchQueries": {"search": {"results": props}}}}
    return ('<html><script type="application/json" data-capla-store-data="apollo">'
            f"{json.dumps(store)}</script></html>")


BOOKING_BLOCKED_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"


def airbnb_room_html(name="Bezeniko Amazing Arcadian Property!", lat=37.71914, lon=22.25123, rating=4.96,
                     titles=(), og_title="Cottage in Vlacherna · ★4.96", ld=True):
    """A room page as probed 2026-10-10: Product + VacationRental JSON-LD, og: meta, amenity titles."""
    rental = {"@context": "https://schema.org", "@type": "VacationRental", "name": name,
              "image": ["https://a0.muscache.com/im/pictures/room-1.jpeg", "https://a0.muscache.com/room-2.jpeg"],
              "address": {"addressLocality": "Vlacherna"},
              "aggregateRating": {"@type": "AggregateRating", "ratingValue": rating, "ratingCount": "24"}}
    if lat is not None:
        rental.update(latitude=lat, longitude=lon)
    product = {"@context": "https://schema.org", "@type": "Product", "name": name}
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>'
                      for o in (product, rental)) if ld else ""
    blob = json.dumps({"sections": [{"title": t} for t in titles]}, separators=(",", ":"))
    return (f'<html><head><meta property="og:title" content="{og_title}"/>'
            '<meta property="og:image" content="https://a0.muscache.com/og.jpeg?im_w=720&amp;width=720"/>'
            f"{scripts}</head><body><script>{blob}</script></body></html>")
