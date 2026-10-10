"""Render a saved trip as a Markdown document (pure, no I/O)."""

import math
import re
from datetime import date
from urllib.parse import quote

from app.models import SIGHT_EMOJI, SIGHT_LABELS, Hike, SavedTrip, Sight, Stay

BADGES = {
    "pets": "🐕 Pets allowed",
    "pets_garden": "🐕🌳 Pets allowed + garden",
    "garden": "🌳 Garden",
    "garden_unconfirmed": "⚠️ Garden – pets not confirmed",
}
SOURCES = {"airbnb": "Airbnb", "booking": "Booking.com"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
_WHITESPACE = re.compile(r"\s+")
_HREF_UNSAFE = re.compile(r"[\s()<>]")
_SAFE_URL = re.compile(r"^https?://", re.IGNORECASE)


def md_escape(text: str) -> str:
    return _SPECIAL.sub(r"\\\1", _WHITESPACE.sub(" ", text).strip())


def _link(name: str, url: str | None) -> str:
    label = md_escape(name)
    if not url or not _SAFE_URL.match(url):
        return label
    href = _HREF_UNSAFE.sub(lambda m: quote(m.group(), safe=""), url)
    return f"[{label}]({href})"


def _map(lat: float, lon: float) -> str:
    return f"[📍 Map](https://www.google.com/maps/search/?api=1&query={lat},{lon})"


def _day(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def _minutes(m: int, estimated: bool) -> str:
    h, mm = divmod(m, 60)
    return f"{'≈ ' if estimated else ''}{f'{h}h ' if h else ''}{mm}m"


def _money(value: float, currency: str | None) -> str:
    prefix = "€" if not currency or currency == "EUR" else f"{md_escape(currency)} "
    return f"{prefix}{math.floor(value + 0.5)}"  # half-up, like the UI's Math.round


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _nights(trip: SavedTrip) -> int:
    return (trip.end - trip.start).days if trip.start and trip.end else 0


def _summary(trip: SavedTrip, nights: int) -> str:
    if trip.start and trip.end:
        when = f"📅 {_day(trip.start)} → {_day(trip.end)}"
        if nights > 0:
            when += f" ({_plural(nights, 'night')})"
    elif trip.start:
        when = f"📅 From {_day(trip.start)}"
    elif trip.end:
        when = f"📅 Until {_day(trip.end)}"
    else:
        when = "📅 Dates not set"
    parts = [when, f"👥 {_plural(trip.guests, 'guest')}"]
    if trip.dog:
        parts.append("🐕 With dog")
    return " · ".join(parts)


def _hike_lines(n: int, h: Hike) -> list[str]:
    facts = []
    if h.distance_km is not None:
        facts.append(f"{h.distance_km:g} km")
    if h.elevation_gain_m is not None:
        facts.append(f"+{h.elevation_gain_m} m")
    if h.difficulty:
        facts.append(md_escape(h.difficulty))
    if h.trailrank is not None:
        facts.append(f"TrailRank {h.trailrank}")
    if h.ferry_needed:
        facts.append("🚗 ⛴️ ferry needed")
    elif h.drive_minutes is not None:
        facts.append(f"🚗 {_minutes(h.drive_minutes, h.drive_estimated)}")
    head = f"{n}. **{_link(h.name, h.url)}**" + (f" – {' · '.join(facts)}" if facts else "")
    second = [f"Near {md_escape(h.near)}"] if h.near else []
    return [head, "   " + " · ".join([*second, _map(h.lat, h.lon)])]


def _stay_lines(s: Stay, nights: int) -> list[str]:
    facts = [SOURCES.get(s.source, s.source)]
    if s.total_price is not None:
        price = f"{_money(s.total_price, s.currency)} total"
        if nights > 0:
            price += f" ({_money(s.total_price / nights, s.currency)}/night)"
        facts.append(price)
    if s.rating is not None:
        facts.append(f"★ {s.rating:g}")
    if s.badge:
        facts.append(BADGES[s.badge])
    return [f"- **{_link(s.name, s.url)}** – {' · '.join(facts)}", "  " + _map(s.lat, s.lon)]


def _sight_line(s: Sight) -> str:
    facts = [SIGHT_LABELS.get(s.kind, s.kind)]
    if s.elevation_m is not None:
        facts.append(f"{s.elevation_m} m")
    if s.wikipedia and _SAFE_URL.match(s.wikipedia):
        facts.append(_link("Wikipedia", s.wikipedia))
    facts.append(_map(s.lat, s.lon))
    return f"- {SIGHT_EMOJI.get(s.kind, '🌿')} **{_link(s.name, s.url)}** – {' · '.join(facts)}"


def trip_markdown(trip: SavedTrip, today: date) -> str:
    nights = _nights(trip)
    out = [f"# {md_escape(trip.name)}", "", _summary(trip, nights)]
    if trip.source == "suggested":
        score = f" – score {trip.score:g}/100" if trip.score is not None else ""
        out.append(f"⭐ Saved from Suggest trips{score}")
    out += ["", f"## 🥾 Hikes ({len(trip.hikes)})", ""]
    if trip.hikes:
        for n, h in enumerate(trip.hikes, 1):
            out += _hike_lines(n, h)
    else:
        out.append("_None yet._")
    out += ["", f"## 🏡 Stays ({len(trip.stays)})", ""]
    if trip.stays:
        for s in trip.stays:
            out += _stay_lines(s, nights)
    else:
        out.append("_None yet._")
    if trip.sights:
        out += ["", f"## 🌿 Nature ({len(trip.sights)})", ""]
        out += [_sight_line(s) for s in trip.sights]
    if trip.notes.strip():
        out += ["", "## 📝 Notes", "", trip.notes.strip()]
    out += ["", "---", f"Exported from hick-toors on {today.isoformat()}. Prices and ratings as seen when saved."]
    return "\n".join(out) + "\n"


def export_filename(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")
    return f"{slug or 'trip'}.md"


def content_disposition(name: str) -> str:
    return f"attachment; filename=\"{export_filename(name)}\"; filename*=UTF-8''{quote(name + '.md', safe='')}"
