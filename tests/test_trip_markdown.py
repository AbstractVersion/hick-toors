from datetime import date, datetime, timezone

from app.models import Hike, SavedTrip, Stay
from app.trip_markdown import content_disposition, export_filename, md_escape, trip_markdown

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 10)
HIKE = Hike(id="wikiloc:1", name="Vikos Gorge", url="https://es.wikiloc.com/vikos", lat=39.9, lon=20.7,
            distance_km=12.0, elevation_gain_m=600, difficulty="Moderate", trailrank=70, near="Monodendri",
            drive_minutes=130)
STAY = Stay(id="airbnb:1", source="airbnb", name="Stone house", url="https://www.airbnb.com/rooms/1",
            lat=39.95, lon=20.75, total_price=190, currency="EUR", rating=4.8, badge="pets")


def trip(**kw):
    base = dict(id="t1", name="Zagori long weekend", created_at=NOW, updated_at=NOW)
    return SavedTrip(**{**base, **kw})


FULL = """# Zagori long weekend

📅 Fri 23 Oct 2026 → Sun 25 Oct 2026 (2 nights) · 👥 2 guests · 🐕 With dog
⭐ Saved from Suggest trips – score 77.5/100

## 🥾 Hikes (1)

1. **[Vikos Gorge](https://es.wikiloc.com/vikos)** – 12 km · +600 m · Moderate · TrailRank 70 · 🚗 2h 10m
   Near Monodendri · [📍 Map](https://www.google.com/maps/search/?api=1&query=39.9,20.7)

## 🏡 Stays (1)

- **[Stone house](https://www.airbnb.com/rooms/1)** – Airbnb · €190 total (€95/night) · ★ 4.8 · 🐕 Pets allowed
  [📍 Map](https://www.google.com/maps/search/?api=1&query=39.95,20.75)

## 📝 Notes

Bring the dog's water bowl.

---
Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved.
"""

EMPTY = """# Empty trip

📅 Dates not set · 👥 1 guest

## 🥾 Hikes (0)

_None yet._

## 🏡 Stays (0)

_None yet._

---
Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved.
"""


def test_full_trip():
    t = trip(start=date(2026, 10, 23), end=date(2026, 10, 25), guests=2, dog=True, source="suggested",
             score=77.5, hikes=[HIKE], stays=[STAY], notes="Bring the dog's water bowl.\n")
    assert trip_markdown(t, TODAY) == FULL


def test_empty_trip_no_dates_no_dog():
    t = trip(name="Empty trip", guests=1, dog=False)
    assert trip_markdown(t, TODAY) == EMPTY


def test_one_night_foreign_currency_and_missing_fields():
    bare = Hike(id="wikiloc:2", name="Bare", url="https://es.wikiloc.com/b", lat=1.5, lon=2.5)
    chf = STAY.model_copy(update={"currency": "CHF", "total_price": 300, "rating": None, "badge": None})
    md = trip_markdown(trip(start=date(2026, 10, 23), end=date(2026, 10, 24), hikes=[bare], stays=[chf]), TODAY)
    assert "(1 night)" in md
    assert "1. **[Bare](https://es.wikiloc.com/b)**\n   [📍 Map](https://www.google.com/maps/search/?api=1&query=1.5,2.5)" in md
    assert "– Airbnb · CHF 300 total (CHF 300/night)\n" in md


def test_same_day_dates_have_no_nights_or_per_night():
    md = trip_markdown(trip(start=date(2026, 10, 23), end=date(2026, 10, 23), stays=[STAY]), TODAY)
    assert "📅 Fri 23 Oct 2026 → Fri 23 Oct 2026 · " in md
    assert "€190 total ·" in md and "/night" not in md


def test_drive_variants_and_booking():
    ferry = HIKE.model_copy(update={"id": "wikiloc:3", "ferry_needed": True})
    est = HIKE.model_copy(update={"id": "wikiloc:4", "drive_minutes": 45, "drive_estimated": True})
    book = STAY.model_copy(update={"id": "booking:1", "source": "booking", "badge": "garden_unconfirmed"})
    md = trip_markdown(trip(hikes=[ferry, est], stays=[book]), TODAY)
    assert "· 🚗 ⛴️ ferry needed\n" in md
    assert "· 🚗 ≈ 45m\n" in md
    assert "2. **[Vikos Gorge]" in md
    assert "– Booking.com · €190 total · ★ 4.8 · ⚠️ Garden – pets not confirmed\n" in md


def test_escaping_and_unsafe_urls():
    odd = HIKE.model_copy(update={"name": "Mt *Olympus* [north]_trail", "url": "javascript:alert(1)"})
    paren = STAY.model_copy(update={"url": "https://x.example/a (b)"})
    md = trip_markdown(trip(name="<Hi> `there`", hikes=[odd], stays=[paren]), TODAY)
    assert md.startswith("# \\<Hi\\> \\`there\\`\n")
    assert "1. **Mt \\*Olympus\\* \\[north\\]\\_trail** – " in md
    assert "javascript" not in md
    assert "(https://x.example/a%20%28b%29)" in md
    assert md_escape("a\\b") == "a\\\\b"


def test_greek_name_kept_in_body():
    assert trip_markdown(trip(name="Ζαγόρι"), TODAY).startswith("# Ζαγόρι\n")


def test_multiline_name_cannot_inject_block_markdown():
    evil = HIKE.model_copy(update={"name": "A\n\n# injected", "near": "X\n- y"})
    md = trip_markdown(trip(hikes=[evil]), TODAY)
    assert not any(line.startswith("# injected") for line in md.splitlines())
    assert "1. **[A # injected](https://es.wikiloc.com/vikos)**" in md
    assert "   Near X - y · " in md


def test_href_encodes_whitespace_and_angle_brackets():
    odd = HIKE.model_copy(update={"url": "https://x.example/a\tb<c>\nd"})
    md = trip_markdown(trip(hikes=[odd]), TODAY)
    assert "(https://x.example/a%09b%3Cc%3E%0Ad)" in md


def test_uppercase_scheme_is_linked():
    up = HIKE.model_copy(update={"url": "HTTP://X.EXAMPLE/a"})
    assert "[Vikos Gorge](HTTP://X.EXAMPLE/a)" in trip_markdown(trip(hikes=[up]), TODAY)


def test_currency_is_escaped():
    odd = STAY.model_copy(update={"currency": "C*H"})
    assert "C\\*H 190 total" in trip_markdown(trip(stays=[odd]), TODAY)


def test_suggested_without_score():
    md = trip_markdown(trip(source="suggested", score=None), TODAY)
    assert "⭐ Saved from Suggest trips\n" in md
    assert "score" not in md


def test_export_filename():
    assert export_filename("Zagori long weekend") == "zagori-long-weekend.md"
    assert export_filename("  Pelion: 3 days!! ") == "pelion-3-days.md"
    assert export_filename("Ζαγόρι") == "trip.md"
    assert export_filename("a" * 100) == "a" * 60 + ".md"


def test_content_disposition():
    assert content_disposition("Ζαγόρι 2") == (
        "attachment; filename=\"2.md\"; filename*=UTF-8''%CE%96%CE%B1%CE%B3%CF%8C%CF%81%CE%B9%202.md")
