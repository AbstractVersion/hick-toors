"""Render a saved trip as a self-contained printable HTML page (pure, no I/O).

The page opens the browser's print dialog, where "Save as PDF" turns it into a PDF – no PDF dependency.
Facts and wording come from the Markdown export's helpers so both exports stay in step.
"""

from datetime import date
from html import escape

from app.models import Hike, SavedTrip, Sight, Stay
from app.trip_markdown import (FOOTER, hike_facts, map_url, safe_url, sight_emoji, sight_facts, stay_facts,
                               suggested_line, trip_nights, trip_summary)

CSP = "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'"

_CSS = """
@page { size: A4; margin: 16mm }
* { box-sizing: border-box }
body { font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif; color: #1d2a1f;
       background: #fff; max-width: 820px; margin: 0 auto; padding: 16px }
h1 { font-size: 24px; margin: 8px 0 4px }
h2 { font-size: 17px; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #cfd8cf }
a { color: #1f6f3a }
.meta { margin: 2px 0; color: #34463a }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 10px 12px; margin-bottom: 12px;
           background: #eef5ee; border: 1px solid #cfd8cf; border-radius: 8px }
.toolbar button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid #1f6f3a;
                  background: #1f6f3a; color: #fff; cursor: pointer }
.hint { color: #4a5c4f; font-size: 13px }
.items { list-style: none; padding: 0; margin: 0 }
.item { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px dashed #dde5dd; break-inside: avoid }
.item img { width: 120px; height: 80px; max-width: 120px; max-height: 80px; object-fit: cover;
            border-radius: 4px; flex: none }
.name { font-weight: 600 }
.facts { margin-top: 2px }
.url { font-size: 11px; color: #5b6b60; word-break: break-all }
.empty { font-style: italic; color: #5b6b60 }
.notes { white-space: pre-wrap; margin: 0 }
footer { margin-top: 24px; padding-top: 8px; border-top: 1px solid #cfd8cf; font-size: 12px; color: #5b6b60 }
@media print {
  .toolbar { display: none }
  body { padding: 0; max-width: none }
  a { text-decoration: none }
}
"""

_SCRIPT = """window.addEventListener("load", () => {
  if (!new URLSearchParams(location.search).has("noprint")) setTimeout(() => window.print(), 300);
});"""


def _e(text) -> str:
    return escape(str(text), quote=True)


def _link(label: str, url: str | None) -> str:
    if not safe_url(url):
        return _e(label)
    return f'<a href="{_e(url)}" rel="noopener">{_e(label)}</a>'


def _map_link(lat: float, lon: float) -> str:
    return _link("📍 Map", map_url(lat, lon))


def _img(url: str | None) -> str:
    return f'<img src="{_e(url)}" alt="" loading="eager">' if safe_url(url) else ""


def _item(emoji: str, name: str, url: str, facts: list[str], extra: list[str], image: str | None = None) -> str:
    """One hike/stay/sight: linked name, facts line, the URL printed underneath (so a PDF keeps it)."""
    facts_html = " · ".join([*(_e(f) for f in facts), *extra])
    return (f'<li class="item">{_img(image)}<div>'
            f'<div class="name">{emoji}{_link(name, url)}</div>'
            + (f'<div class="facts">{facts_html}</div>' if facts_html else "")
            + f'<div class="url">{_e(url)}</div></div></li>')


def _hike(h: Hike) -> str:
    extra = ([f"Near {_e(h.near)}"] if h.near else []) + [_map_link(h.lat, h.lon)]
    return _item("", h.name, h.url, hike_facts(h), extra, h.thumbnail)


def _stay(s: Stay, nights: int) -> str:
    return _item("", s.name, s.url, stay_facts(s, nights), [_map_link(s.lat, s.lon)], s.photo)


def _sight(s: Sight) -> str:
    extra = [_link("Wikipedia", s.wikipedia)] if safe_url(s.wikipedia) else []
    return _item(f"{sight_emoji(s)} ", s.name, s.url, sight_facts(s), [*extra, _map_link(s.lat, s.lon)])


def _section(title: str, items: list[str], empty: str | None = "None yet.") -> list[str]:
    out = [f"<h2>{title}</h2>"]
    if items:
        out += ['<ul class="items">', *items, "</ul>"]
    elif empty:
        out.append(f'<p class="empty">{empty}</p>')
    return out


def trip_print_html(trip: SavedTrip, today: date) -> str:
    nights = trip_nights(trip)
    body = [
        '<div class="toolbar"><button type="button" onclick="window.print()">🖨️ Print / Save as PDF</button>'
        '<span class="hint">Choose “Save as PDF” as the printer</span></div>',
        f"<h1>{_e(trip.name)}</h1>",
        f'<p class="meta">{_e(trip_summary(trip, nights))}</p>',
    ]
    if suggested := suggested_line(trip):
        body.append(f'<p class="meta">{_e(suggested)}</p>')
    body += _section(f"🥾 Hikes ({len(trip.hikes)})", [_hike(h) for h in trip.hikes])
    body += _section(f"🏡 Stays ({len(trip.stays)})", [_stay(s, nights) for s in trip.stays])
    if trip.sights:
        body += _section(f"🌿 Nature ({len(trip.sights)})", [_sight(s) for s in trip.sights])
    if trip.notes.strip():
        body += ["<h2>📝 Notes</h2>", f'<p class="notes">{_e(trip.notes.strip())}</p>']
    body.append(f"<footer>Exported from hick-toors on {today.isoformat()}. {FOOTER}</footer>")
    return ("<!doctype html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_e(trip.name)}</title>\n<style>{_CSS}</style>\n"
            f"<script>{_SCRIPT}</script>\n</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n")
