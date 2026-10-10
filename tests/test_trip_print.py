from datetime import date, datetime, timezone

from app.models import Hike, SavedTrip, Sight, Stay
from app.trip_print import trip_print_html

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 10)
HIKE = Hike(id="wikiloc:1", name="Vikos Gorge", url="https://es.wikiloc.com/vikos", lat=39.9, lon=20.7,
            distance_km=12.0, elevation_gain_m=600, difficulty="Moderate", trailrank=70, near="Monodendri",
            drive_minutes=130, thumbnail="https://img.example/t.jpg")
EVIL = Hike(id="wikiloc:2", name="Evil <b>", url="javascript:alert(1)", lat=1.5, lon=2.5,
            thumbnail="javascript:x")
STAY = Stay(id="airbnb:1", source="airbnb", name="Stone house", url="https://www.airbnb.com/rooms/1",
            lat=39.95, lon=20.75, total_price=190, currency="EUR", rating=4.8, badge="pets",
            photo="https://img.example/p.jpg")
SIGHT = Sight(id="osm:node/1", name="Drakolimni", kind="lake", lat=40.0, lon=20.8, elevation_m=2050,
              url="https://www.openstreetmap.org/node/1", wikipedia="https://el.wikipedia.org/wiki/X")


def trip(**kw):
    base = dict(id="t1", name="Ζαγόρι weekend", created_at=NOW, updated_at=NOW)
    return SavedTrip(**{**base, **kw})


def full():
    return trip_print_html(trip(start=date(2026, 10, 23), end=date(2026, 10, 25), source="suggested", score=77.5,
                                hikes=[HIKE, EVIL], stays=[STAY], sights=[SIGHT],
                                notes="<script>alert(1)</script>\nsecond line"), TODAY)


def test_document_shell():
    html = full()
    assert html.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert "<title>Ζαγόρι weekend</title>" in html
    assert "@page { size: A4; margin: 16mm }" in html
    assert "@media print" in html
    assert 'onclick="window.print()"' in html and "🖨️ Print / Save as PDF" in html
    assert "Choose “Save as PDF” as the printer" in html
    assert "noprint" in html and "setTimeout(() => window.print(), 300)" in html
    assert "<link" not in html and "<script src" not in html


def test_content_mirrors_markdown():
    html = full()
    assert "Fri 23 Oct 2026 → Sun 25 Oct 2026 (2 nights)" in html
    assert "👥 2 guests" in html and "🐕 With dog" in html
    assert "⭐ Saved from Suggest trips – score 77.5/100" in html
    assert "🥾 Hikes (2)" in html and "🏡 Stays (1)" in html
    assert "12 km · +600 m · Moderate · TrailRank 70 · 🚗 2h 10m" in html
    assert "Near Monodendri" in html
    assert "€190 total (€95/night)" in html
    assert "🐕 Pets allowed" in html and "★ 4.8" in html
    assert "🌿 Nature (1)" in html and "🏞️" in html and "Lake · 2050 m" in html
    assert 'href="https://el.wikipedia.org/wiki/X"' in html
    assert "https://www.google.com/maps/search/?api=1&amp;query=39.9,20.7" in html
    assert "Exported from hick-toors on 2026-10-10. Prices and ratings as seen when saved." in html


def test_links_and_urls_printed():
    html = full()
    assert '<a href="https://es.wikiloc.com/vikos" rel="noopener">Vikos Gorge</a>' in html
    assert html.count("https://es.wikiloc.com/vikos") >= 2  # link + printed URL
    assert 'href="javascript' not in html
    assert "javascript:alert(1)" in html  # shown as text only
    assert "Evil &lt;b&gt;" in html and "Evil <b>" not in html


def test_images_only_for_http():
    html = full()
    assert '<img src="https://img.example/t.jpg"' in html and '<img src="https://img.example/p.jpg"' in html
    assert 'loading="eager"' in html
    assert 'src="javascript' not in html


def test_notes_escaped_and_preserved():
    html = full()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\nsecond line" in html
    assert "<script>alert" not in html
    assert "📝 Notes" in html and "pre-wrap" in html


def test_empty_trip():
    html = trip_print_html(trip(name="Empty trip", guests=1, dog=False), TODAY)
    assert "Dates not set" in html and "👥 1 guest" in html and "With dog" not in html
    assert "Nature (" not in html and "📝 Notes" not in html
    assert "None yet." in html
    assert "⭐" not in html
