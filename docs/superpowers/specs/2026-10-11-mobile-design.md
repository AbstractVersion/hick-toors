# Mobile-friendly site — design

Date: 2026-10-11 · Status: UX agreed in chat (layout: full map + bottom tabs; priority: everything works)

## Goal

Friends open the site on their phones: they must be able to search, filter, draw an area, suggest trips,
ask Claude, use nature sights, edit saved trips, log in and vote — comfortably with one thumb. The desktop
layout stays exactly as it is.

## Decisions

| Topic | Decision |
|---|---|
| Breakpoint | `@media (max-width: 760px)` = phone layout; wider = today's desktop layout, unchanged |
| Layout | map fills the screen; a compact top bar; a bottom tab bar 🗺️ Map / 📋 List / 🧳 Trips / 🗳️ Vote |
| Top bar | `📍 [Go to place…] Go` and a `☰` button (opens the controls panel) |
| Controls panel (☰) | full-screen panel with everything else from today's header: login area, dates, home, drive, 🐕, mode + place, hike/stay filters, draw, 🌿 Nature, and the three search buttons (sticky at the bottom). Pressing a search button or a draw button closes the panel |
| List tab | today's sidebar (hikes, stays, trip picks, load more) full-screen over the map, plus the Ask Claude box at its bottom |
| Trips / Vote tabs | open the existing My trips / Vote dialogs, full-screen; closing returns to the previous tab |
| Dialogs | every `<dialog>` (trips, vote, login, filters) is full-screen on phones (100vw × 100dvh, no radius), header with ✕ sticky |
| Badges | the tab bar mirrors the counts: 📋 List shows the hike count, 🧳 Trips the trips count, 🗳️ Vote the "N left" dot/count |
| Implementation | one DOM, no duplicated controls: the header's controls (everything except brand and Go-to) are wrapped in `#controls-more`, which is `display: contents` on desktop (layout unchanged) and the ☰ panel on phones; tab state in a new `static/mobile.js` (body classes `tab-map` / `tab-list`, `menu-open`) |

**Base map (all screen sizes, user request):** the map starts on **Streets** (OpenStreetMap); Topo and
Satellite remain selectable in the layer control.

## Behaviour on phones

- **Tab Map** (default): map visible; list hidden. **Tab List**: sidebar visible full-screen; map hidden
  (Leaflet `invalidateSize()` when returning to the map).
- Tapping a hike/stay/pick in the list switches to the Map tab and runs today's click action (zoom/select).
- After a search finishes the current tab stays; the List badge updates. A search started from the panel
  shows the Map tab.
- Drawing: ✏️ Rectangle / Circle from the panel closes it and shows the draw hint on the map. Drawing must
  work with touch (Leaflet.draw 1.0.4 touch support; if a shape cannot be drawn by touch, a tap-tap
  fallback: rectangle = tap two corners, circle = tap centre then edge).
- Map popups fit the screen (max-width ≈ 85vw), buttons inside ≥ 40 px tall.
- Nature chips: one horizontally scrollable row at the top of the map; hint centred.
- Touch targets ≥ 40 px; inputs/selects font-size 16 px (no iOS zoom on focus); no hover-only
  information (counts/names already open on tap).
- Safe areas: tab bar padded with `env(safe-area-inset-bottom)`; heights use `100dvh`.
- Vote dialog rows, trip editor items and filters forms wrap to one column; the three rating buttons stay on
  one row, full width.
- `#trip-menu` (＋ Trip) opens as a bottom sheet above the tab bar.
- Landscape phones use the same layout (the breakpoint is width-based).

## Out of scope

Native app / PWA install, offline mode, gestures like swipe between tabs, a redesign of the desktop layout.

## Testing

- Existing desktop UI check stays green (layout unchanged at 1280 px).
- New mobile scenario in `tests/ui/check_ui.py` with an iPhone-sized context (390×844, `has_touch`,
  `is_mobile`, device scale 3): no horizontal page scroll; tab bar visible; ☰ opens/closes the panel; Search
  all from the panel → results, List badge; List → tap a hike → Map tab; draw a rectangle by touch (or the
  fallback) → drawn-area search; filters dialog full-screen and usable; ＋ Trip sheet; Trips tab → editor
  usable; login dialog; Vote tab → start a round and rate (buttons ≥ 40 px); 🌿 chips scroll; every tap target
  checked ≥ 40 px for the main controls.
