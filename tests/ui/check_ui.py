"""Headless-Chrome check of the filter popups, results, Trip picks, log in / sign up, saved trips and the
🌿 Nature layer (not collected by pytest).

Start the app first with a throwaway trips database, then run the check against it:
    TRIPS_DB=$(mktemp -d)/trips.db env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765
    env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765

The script talks only to the local server. Search endpoints are answered by Playwright with
canned JSON, so no Wikiloc/Airbnb/Booking/Overpass request is made; the captured request bodies are
asserted instead. The saved-trips API (/api/saved-trips...) is NOT mocked: the trips scenario
creates, edits, exports and deletes a real trip, so it refuses to run unless the trips list is empty.
The auth API is real too: the check signs up `nikos` (a fresh TRIPS_DB has no accounts).
The 🗳️ voting scenario at the end uses the real rounds API with two browser contexts (`anna` opens a round,
logged-out `bob` watches, signs up from a vote click, votes; anna closes the round) – it needs no open round.
The 📱 phone scenarios (390×844 and landscape 844×390, touch) run last; set UI_SHOTS=<folder> to save screenshots there.
Every UI sign-up picks its emoji in the sign-up picker; the 😀 scenarios check taken emojis, the 409 race, Change emoji
and the first-login prompt (mocked /api/auth/me for an account from before emojis).
"""

import json
import os
import re
import sys
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HIKE = {"id": "wikiloc:1", "name": "Test hike", "url": "https://es.wikiloc.com/x", "lat": 38.5, "lon": 22.6,
        "distance_km": 9.5, "elevation_gain_m": 600, "difficulty": "Moderate", "trailrank": 70}
HIKE2 = {**HIKE, "id": "wikiloc:2", "name": "Second hike", "lat": 38.6}
STAY = {"id": "airbnb:1", "source": "airbnb", "name": "Area house", "url": "https://www.airbnb.com/rooms/1",
        "lat": 38.55, "lon": 22.65, "total_price": 180, "currency": "EUR", "rating": 4.8, "badge": "pets",
        "distance_km": 3.2}
BSTAY = {**STAY, "id": "booking:9", "source": "booking", "name": "Booking house", "lat": 38.56}
COMBO = {"hike": HIKE, "stay": STAY, "score": 77.5, "note": None,
         "breakdown": {"hike": 30, "stay": 20, "proximity": 15, "price": 12.5}}
PLACE = {"place": {"name": "Μακρινίτσα, Δήμος Βόλου", "lat": 39.39, "lon": 22.99,
                   "bbox": {"south": 39.38, "west": 22.98, "north": 39.40, "east": 23.00}}, "warnings": []}
BOOKING_DOWN = "Booking.com unavailable (blocked by the site) – showing other sources"
AIRBNB_LINK = "https://www.airbnb.com/rooms/576031322358400592?adults=9&check_in=2027-01-01&check_out=2027-01-05"
LINKED_STAY = {"id": "airbnb:576031322358400592", "source": "airbnb", "name": "Bezeniko Amazing Arcadian Property!",
               "url": "https://www.airbnb.com/rooms/576031322358400592?check_in=2026-10-23&check_out=2026-10-25&adults=4",
               "lat": 37.71914, "lon": 22.25123, "total_price": None, "currency": None, "rating": 4.96,
               "photo": None, "pets_allowed": False, "has_garden": True, "badge": None, "distance_km": None}
PRICE_NOT_FOUND = "Price not found for these dates – you can type it in"
LOCATE_RE = re.compile(r".*/api/locate(\?.*)?$")
WATERFALL = {"id": "osm:node/1", "name": "Test falls", "kind": "waterfall", "lat": 38.48, "lon": 22.72,
             "elevation_m": 420, "url": "https://www.openstreetmap.org/node/1",
             "wikipedia": "https://en.wikipedia.org/wiki/Test_falls", "lines": []}
RIVER = {"id": "osm:way/2", "name": "Test river", "kind": "river", "lat": 38.56, "lon": 22.55, "elevation_m": None,
         "url": "https://www.openstreetmap.org/way/2", "wikipedia": None,
         "lines": [[[38.55, 22.50], [38.56, 22.55], [38.57, 22.60]]]}
NATURE_DOWN = "Nature sights unavailable for part of the map – try again in a minute"
# Set by the Load more step: hikes answers then carry a total and a next page.
paging = {"on": False}
EMOJI_COUNT = 110  # app/accounts.py EMOJIS


def pick_emoji(pg, emoji: str, tap: bool = False) -> None:
    """Pick an emoji in the sign-up tab's picker (waits for the grid)."""
    button = pg.locator(f'#auth-emoji-grid .emoji-btn[data-emoji="{emoji}"]')
    expect(button).not_to_have_attribute("aria-disabled", "true")
    button.tap() if tap else button.click()
    expect(button).to_have_attribute("aria-pressed", "true")
    expect(pg.locator("#auth-submit")).to_be_enabled()


def canned_hikes(body: dict) -> dict:
    data = {"hikes": [HIKE], "warnings": [], "drive_limit_minutes": None}
    if paging["on"]:
        offset = body.get("page_offset", 0)
        data.update(hikes=[HIKE] if offset == 0 else [HIKE, HIKE2], total_available=17000,
                    next_page_offset=20 if offset == 0 else None)
    return data


CANNED = {
    "/api/hikes": canned_hikes,
    # Stays inside a drawn area come back with one Airbnb house; hike-centred stays stay empty.
    "/api/stays": lambda body: {"stays": [STAY] if "area" in body and body.get("providers") == ["airbnb"] else [],
                                "warnings": [], "center": None},
    "/api/trips": lambda body: {"combos": [COMBO], "warnings": []},
    "/api/locate": lambda body: PLACE,
    "/api/stays/lookup": lambda body: {"stay": LINKED_STAY, "warnings": [PRICE_NOT_FOUND]},
}


def main() -> None:
    requests: list[tuple[str, dict]] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        held: list = []  # (route, path, body) of requests kept in flight on purpose
        hold: set = set()

        def answer(route):
            path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
            body = route.request.post_data_json if route.request.post_data else {}
            requests.append((path, body))
            if path in hold:
                held.append((route, path, body))
                return
            route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path](body)))

        def release(index: int = 0, payload=None, status: int = 200) -> dict:
            route, path, body = held.pop(index)
            route.fulfill(status=status, content_type="application/json",
                          body=json.dumps(CANNED[path](body) if payload is None else payload))
            return body

        def wait_held(count: int) -> None:
            for _ in range(80):
                if len(held) >= count:
                    return
                page.wait_for_timeout(100)
            raise SystemExit(f"FAIL expected {count} held requests, have {len(held)}")

        def wait_request(path: str, before: int) -> dict:
            for _ in range(60):
                hits = [r for r in requests[before:] if r[0] == path]
                if hits:
                    return hits[-1][1]
                page.wait_for_timeout(100)
            raise SystemExit(f"FAIL no {path} request")

        for path in CANNED:
            if path != "/api/locate":
                page.route(f"**{path}", answer)
        page.route(LOCATE_RE, answer)

        # 🌿 Nature: GET /api/nature answered with canned sights; query params recorded; can be held.
        nature_reqs: list[dict] = []
        nature_held: list = []
        nature_cfg = {"hold": False, "payload": None, "status": 200}

        def nature_answer(route):
            query = {k: v[0] for k, v in parse_qs(urlsplit(route.request.url).query).items()}
            nature_reqs.append(query)
            if nature_cfg["hold"]:
                nature_held.append(route)
                return
            payload = nature_cfg["payload"] or {"sights": [WATERFALL, RIVER], "warnings": []}
            route.fulfill(status=nature_cfg["status"], content_type="application/json", body=json.dumps(payload))

        page.route("**/api/nature**", nature_answer)

        def load(clear: bool = False) -> None:
            page.goto(BASE)
            if clear:
                page.evaluate("localStorage.clear()")
                page.reload()
            expect(page.locator("#hike-filters-btn")).to_be_enabled(timeout=15000)

        def last_request(wanted: str) -> tuple[str, dict]:
            return [r for r in requests if r[0] == wanted][-1]

        def search_and_capture() -> tuple[str, dict]:
            before = len(requests)
            page.click("#search")
            for _ in range(50):
                if len(requests) > before:
                    return requests[-1]
                page.wait_for_timeout(100)
            raise SystemExit("FAIL search sent no request")

        load(clear=True)
        if page.evaluate("fetch('/api/saved-trips').then((r) => r.json())"):
            raise SystemExit("FAIL start the server with a throwaway TRIPS_DB")  # never touch real trips
        expect(page.locator("#hike-filter-count")).to_be_hidden()
        expect(page.locator("#stay-filter-count")).to_be_hidden()
        print("PASS buttons enabled, no badges on defaults")
        if not page.evaluate("map.hasLayer(tiles.Streets) && !map.hasLayer(tiles.Topo) && !map.hasLayer(tiles.Satellite)"):
            raise SystemExit("FAIL the map should start on the Streets layer")
        expect(page.locator("#tabbar")).to_be_hidden()
        expect(page.locator("#menu-btn")).to_be_hidden()
        expect(page.locator("#search-all")).to_be_visible()
        print("PASS Streets base map on load; desktop has no tab bar / ☰")

        # Hike popup: generated from the catalogue, Apply stores and shows the badge.
        page.click("#hike-filters-btn")
        dialog = page.locator("#hike-dialog")
        expect(dialog).to_be_visible()
        expect(dialog.locator('[data-key="activities"] .chip')).to_have_count(6)
        login = page.evaluate("state.catalogue.wikiloc_login")  # depends on WIKILOC_COOKIE in .env
        loop_box = expect(dialog.locator('input[name="loop_only"]'))
        loop_box.to_be_enabled() if login else loop_box.to_be_disabled()
        dialog.locator('[name="min_trailrank"]').fill("50")
        dialog.locator('[data-act="apply"]').click()
        expect(dialog).to_be_hidden()
        expect(page.locator("#hike-filter-count")).to_have_text("•1")
        print("PASS hike popup apply + badge")

        # Cancel discards edits.
        page.click("#hike-filters-btn")
        dialog.locator('[name="min_trailrank"]').fill("80")
        dialog.locator('[data-act="cancel"]').click()
        page.click("#hike-filters-btn")
        expect(dialog.locator('[name="min_trailrank"]')).to_have_value("50")
        dialog.locator('[data-act="cancel"]').click()
        print("PASS cancel keeps stored values")

        # Stay popup: source tags, min > max blocks Apply, shared settings sync to the header.
        page.click("#stay-filters-btn")
        stay = page.locator("#stay-dialog")
        expect(stay.locator('.chip:has(input[value="\\"fireplace\\""]) .src-tag')).to_have_text("Airbnb only")
        stay.locator('[name="price_min"]').fill("200")
        stay.locator('[name="price_max"]').fill("100")
        expect(stay.locator('[data-act="apply"]')).to_be_disabled()
        expect(stay.locator(".form-error")).to_contain_text("minimum is above maximum")
        stay.locator('[name="price_max"]').fill("300")
        expect(stay.locator('[data-act="apply"]')).to_be_enabled()
        stay.locator('input[name="amenities"][value="\\"parking\\""]').check()
        stay.locator('[name="shared_guests"]').fill("3")
        stay.locator('[name="shared_dog"]').uncheck()
        stay.locator('[data-act="apply"]').click()
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        expect(page.locator("#dog")).not_to_be_checked()
        print("PASS stay popup validation, tags, header sync")

        # Filters reach the API.
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        path, body = requests[-1]
        assert path == "/api/hikes" and body["filters"]["min_trailrank"] == 50, body
        page.locator("#hike-list li").first.click()
        page.wait_for_function("document.querySelector('#stay-count').textContent === '(0)'")
        path, body = requests[-1]
        assert path == "/api/stays", path
        assert body["filters"]["amenities"] == ["parking"] and body["filters"]["price_max"] == 300, body
        assert body["guests"] == 3 and body["dog"] is False, body
        print("PASS filters sent with hike and stay searches")

        # Persisted across reloads; corrupt storage falls back to defaults without errors.
        load()
        expect(page.locator("#hike-filter-count")).to_have_text("•1")
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        page.evaluate("localStorage.setItem('hicktoors.filters.v1', '{not json')")
        load()
        expect(page.locator("#hike-filter-count")).to_be_hidden()
        print("PASS localStorage persistence + corrupt storage")

        # Reset to defaults.
        page.evaluate("""localStorage.setItem('hicktoors.filters.v1',
            JSON.stringify({stay: {price_max: 90, beachfront: true}}))""")
        load()
        expect(page.locator("#stay-filter-count")).to_have_text("•2")
        page.click("#stay-filters-btn")
        stay.locator('[data-act="reset"]').click()
        expect(stay.locator('[name="price_max"]')).to_have_value("")
        stay.locator('[data-act="apply"]').click()
        expect(page.locator("#stay-filter-count")).to_be_hidden()
        print("PASS reset to defaults")

        # Drawn area mode with no shape: notice, no request.
        sent = len(requests)
        page.check('input[name="mode"][value="drawn"]')
        page.click("#search")
        expect(page.locator("#notices")).to_contain_text("Draw a rectangle or circle first")
        assert len(requests) == sent, requests[sent:]
        print("PASS drawn mode without a shape")

        # Empty activities selection: Apply is blocked with a readable message.
        page.click("#hike-filters-btn")
        for box_ in dialog.locator('input[name="activities"]:checked').all():
            box_.uncheck()
        expect(dialog.locator('[data-act="apply"]')).to_be_disabled()
        expect(dialog.locator(".form-error")).to_contain_text("Pick at least one")
        dialog.locator('[data-act="cancel"]').click()
        print("PASS empty activities blocks Apply")

        # API 422 detail keeps the field name.
        page.unroute("**/api/hikes")
        page.route("**/api/hikes", lambda route: route.fulfill(status=422, content_type="application/json",
            body=json.dumps({"detail": [{"loc": ["body", "filters", "activities"],
                                         "msg": "Value error, bad list"}]})))
        page.check('input[name="mode"][value="near_home"]')
        page.click("#search")
        expect(page.locator("#notices")).to_contain_text("filters.activities : bad list")
        page.unroute("**/api/hikes")
        page.route("**/api/hikes", answer)
        print("PASS 422 message keeps the field")

        # Real mouse drags with the Draw buttons (Leaflet.draw handlers).
        def drag(dx: int, dy: int) -> None:
            mbox = page.locator("#map").bounding_box()
            x, y = mbox["x"] + mbox["width"] / 2, mbox["y"] + mbox["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + dx, y + dy, steps=8)
            page.mouse.up()

        page.check('input[name="mode"][value="near_home"]')
        page.click("#draw-circle-btn")
        expect(page.locator("#draw-hint")).to_be_visible()
        expect(page.locator("#draw-hint")).to_contain_text("circle")
        expect(page.locator("#draw-circle-btn")).to_have_class("tool-btn active")
        drag(90, 0)
        expect(page.locator('input[name="mode"][value="drawn"]')).to_be_checked()
        expect(page.locator("#draw-hint")).to_be_hidden()
        assert page.evaluate("drawnItems.getLayers().length") == 1
        assert page.evaluate("drawnItems.getLayers()[0] instanceof L.Circle") is True
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        path, body = last_request("/api/hikes")
        assert path == "/api/hikes" and body["mode"] == "drawn" and body["area"]["shape"] == "circle", body
        print("PASS circle drawn with the mouse and searched")

        page.click("#draw-rect-btn")
        expect(page.locator("#draw-hint")).to_contain_text("rectangle")
        page.keyboard.press("Escape")
        expect(page.locator("#draw-hint")).to_be_hidden()
        page.click("#draw-rect-btn")
        drag(120, 80)
        expect(page.locator("#draw-hint")).to_be_hidden()
        assert page.evaluate("drawnItems.getLayers().length") == 1
        assert page.evaluate("drawnItems.getLayers()[0] instanceof L.Circle") is False
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        path, body = last_request("/api/hikes")
        assert path == "/api/hikes" and body["mode"] == "drawn" and body["area"]["shape"] == "rectangle", body
        assert body["area"]["south"] < body["area"]["north"] and body["area"]["west"] < body["area"]["east"], body
        print("PASS rectangle replaces circle, Esc cancels the hint")

        # Part A: 🔍 Search all in a drawn area also loads the stays inside the shape, no hike click needed.
        expect(page.locator("#search-all")).to_be_enabled()
        page.click("#search-all")
        expect(page.locator("#stay-list li")).to_have_count(1)
        expect(page.locator("#stay-note")).to_have_text("in drawn area")
        stay_body = last_request("/api/stays")[1]
        assert "area" in stay_body and "hike_id" not in stay_body, stay_body
        assert stay_body["area"]["shape"] == "rectangle", stay_body
        expect(page.locator("#stay-count")).to_have_text("(1)")
        page.locator("#hike-list li").first.click()  # a hike click replaces the stays with that hike's
        expect(page.locator("#stay-count")).to_have_text("(0)")
        expect(page.locator("#stay-note")).to_have_text("near Test hike")
        print("PASS drawn search loads stays inside the area; hike click replaces them")

        # 🔍 Search hikes replaces only the hikes: stays already listed stay, no stay request is made.
        page.click("#search-all")
        expect(page.locator("#stay-list li")).to_have_count(1)
        expect(page.locator("#search")).to_be_enabled()
        stays_before = len([r for r in requests if r[0] == "/api/stays"])
        page.click("#search")
        expect(page.locator("#search")).to_be_enabled()
        expect(page.locator("#hike-list li")).to_have_count(1)
        expect(page.locator("#stay-list li")).to_have_count(1)
        expect(page.locator("#stay-note")).to_have_text("in drawn area")
        # The stay keeps its pin in the stay layer. (Not a DOM check: the fit zooms in on the lone hike and the
        # cluster layer drops the off-screen pin once the zoom animation ends – a DOM check only passed mid-animation.)
        page.wait_for_function(f"stayLayer.hasLayer(state.markers.get({json.dumps(STAY['id'])}))", timeout=5000)
        page.wait_for_timeout(300)
        assert len([r for r in requests if r[0] == "/api/stays"]) == stays_before, "Search hikes fetched stays"
        print("PASS 🔍 Search hikes keeps the stays on the map; 🔍 Search all reloads them")

        # Part B: total count header and Load more (append, dedupe, button hides at the end).
        paging["on"] = True
        page.click("#search")
        expect(page.locator("#hike-total")).to_have_text("Showing 1 of 17,000 routes in this area")
        expect(page.locator("#load-more")).to_be_visible()
        page.click("#load-more")
        expect(page.locator("#hike-list li")).to_have_count(2)  # HIKE deduped, HIKE2 appended
        body = last_request("/api/hikes")[1]
        assert body["page_offset"] == 20 and body["mode"] == "drawn" and "area" in body, body
        expect(page.locator("#hike-total")).to_have_text("Showing 2 of 17,000 routes in this area")
        expect(page.locator("#load-more")).to_be_hidden()
        assert page.evaluate("hikeLayer.getLayers().length") == 2
        paging["on"] = False
        expect(page.locator("#search")).to_be_enabled()
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        expect(page.locator("#hike-total")).to_have_text("")
        expect(page.locator("#load-more")).to_be_hidden()
        print("PASS total header, Load more appends and dedupes, new search resets")

        # Deleting the shape in Leaflet.draw's toolbar returns to Near home.
        expect(page.locator("#search")).to_be_enabled()  # earlier search finished
        page.evaluate("drawnItems.clearLayers(); map.fire(L.Draw.Event.DELETED, {layers: L.layerGroup()})")
        expect(page.locator('input[name="mode"][value="near_home"]')).to_be_checked()
        expect(page.locator("#notices")).to_contain_text("Drawn area removed")
        print("PASS deleting the shape returns to Near home")

        # No "trip" radio any more: WHERE is near home / place / drawn area.
        expect(page.locator('input[name="mode"][value="trip"]')).to_have_count(0)
        expect(page.locator('input[name="mode"]')).to_have_count(3)
        expect(page.locator("#combo-section")).to_be_hidden()
        print("PASS no trip radio, Trip picks hidden at start")

        # Suggest trips with WHERE = Drawn area sends the shape; Search hikes hides the picks again.
        page.evaluate("""map.fire(L.Draw.Event.CREATED,
            {layer: L.circle([38.5, 22.6], {radius: 8000}), layerType: 'circle'})""")
        assert page.evaluate("drawnItems.getLayers().length") == 1
        expect(page.locator('input[name="mode"][value="drawn"]')).to_be_checked()
        hold.add("/api/trips")
        page.click("#suggest-trips")
        wait_held(1)
        expect(page.locator("#combo-section")).to_be_visible()
        expect(page.locator('[data-spin="combos"]')).to_be_visible()
        expect(page.locator("#combo-section")).to_have_attribute("aria-busy", "true")
        body = release()
        hold.discard("/api/trips")
        assert body["mode"] == "drawn" and body["area"] == {
            "shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000}, body
        expect(page.locator('[data-spin="combos"]')).to_be_hidden()
        expect(page.locator("#combo-section")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#combo-list li")).to_have_count(1)
        expect(page.locator("#combo-list li")).to_contain_text("Test hike + Area house")
        expect(page.locator("#combo-list li")).to_contain_text("77.5/100")
        expect(page.locator("#combo-list li")).to_contain_text("hike 30 · stay 20 · near 15 (3.2 km) · price 12.5")
        expect(page.locator("#combo-section h2")).to_contain_text("Trip picks (hike + stay)")
        print("PASS Suggest trips in the drawn area shows Trip picks, spinner while loading")
        expect(page.locator("#suggest-trips")).to_be_enabled()
        page.click("#search")
        expect(page.locator("#combo-section")).to_be_hidden()
        expect(page.locator("#combo-list li")).to_have_count(0)
        print("PASS Search hikes hides the Trip picks")

        # Drawn mode without a shape -> notice, no request; place and near-home bodies.
        expect(page.locator("#search")).to_be_enabled()
        page.evaluate("drawnItems.clearLayers()")
        before = len(requests)
        page.click("#suggest-trips")
        expect(page.locator("#notices")).to_contain_text("Draw a rectangle or circle first")
        assert len(requests) == before
        page.check('input[name="mode"][value="place"]')
        page.fill("#place", "Zagori")
        before = len(requests)
        page.click("#suggest-trips")
        body = wait_request("/api/trips", before)
        assert body["mode"] == "place" and body["place"] == "Zagori" and "area" not in body, body
        expect(page.locator("#suggest-trips")).to_be_enabled()
        page.check('input[name="mode"][value="near_home"]')
        before = len(requests)
        page.click("#suggest-trips")
        body = wait_request("/api/trips", before)
        assert body["mode"] == "near_home" and "area" not in body and "place" not in body, body
        print("PASS Suggest trips: drawn needs a shape; place and near-home bodies")

        # ---- spinners ----
        expect(page.locator("#suggest-trips")).to_be_enabled()
        hold.add("/api/hikes")
        page.click("#search")
        wait_held(1)
        expect(page.locator('[data-spin="hikes"]')).to_be_visible()
        expect(page.locator("#hike-section")).to_have_attribute("aria-busy", "true")
        release()
        expect(page.locator('[data-spin="hikes"]')).to_be_hidden()
        expect(page.locator("#hike-section")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#hike-list li")).to_have_count(1)
        expect(page.locator("#search")).to_be_enabled()
        page.click("#search")  # a failing request clears the spinner too
        wait_held(1)
        expect(page.locator('[data-spin="hikes"]')).to_be_visible()
        release(payload={"detail": "boom"}, status=500)
        expect(page.locator('[data-spin="hikes"]')).to_be_hidden()
        expect(page.locator("#notices")).to_contain_text("boom")
        hold.discard("/api/hikes")
        print("PASS hikes spinner while loading, cleared on success and on failure")

        # Stays: Airbnb and Booking.com load independently; Booking answers slowly with the unavailable notice.
        expect(page.locator("#search")).to_be_enabled()
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        hold.add("/api/stays")
        page.locator("#hike-list li").first.click()
        wait_held(2)
        assert sorted(h[2]["providers"][0] for h in held) == ["airbnb", "booking"], held
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_visible()
        expect(page.locator('[data-spin="stay:booking"]')).to_be_visible()
        expect(page.locator("#stay-section")).to_have_attribute("aria-busy", "true")
        release([h[2]["providers"][0] for h in held].index("airbnb"),
                payload={"stays": [STAY], "warnings": [], "center": None})
        expect(page.locator("#stay-list li")).to_have_count(1)  # Airbnb stays are visible first
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_hidden()
        expect(page.locator('[data-spin="stay:booking"]')).to_be_visible()
        expect(page.locator("#retry-booking")).to_be_hidden()
        release(0, payload={"stays": [], "warnings": [BOOKING_DOWN], "center": None})
        expect(page.locator('[data-spin="stay:booking"]')).to_be_hidden()
        expect(page.locator("#retry-booking")).to_be_visible()
        expect(page.locator("#notices")).to_contain_text("Booking.com unavailable")
        page.click("#retry-booking")  # re-requests only Booking.com for the same query, merges the answer
        wait_held(1)
        expect(page.locator('[data-spin="stay:retry"]')).to_be_visible()
        expect(page.locator("#retry-booking")).to_be_hidden()
        retry_body = release(0, payload={"stays": [BSTAY], "warnings": [], "center": None})
        assert retry_body["providers"] == ["booking"] and retry_body["hike_id"] == "wikiloc:1", retry_body
        expect(page.locator('[data-spin="stay:retry"]')).to_be_hidden()
        expect(page.locator("#stay-list li")).to_have_count(2)
        expect(page.locator("#stay-count")).to_have_text("(2)")
        expect(page.locator("#retry-booking")).to_be_hidden()
        expect(page.locator("#notices")).not_to_contain_text("Booking.com unavailable")
        expect(page.locator("#stay-section")).to_have_attribute("aria-busy", "false")
        print("PASS stays: Airbnb first, Booking spinner, Retry Booking merges")

        # A failing Booking request (HTTP 500) clears its spinner and offers the retry link.
        page.locator("#hike-list li").first.click()
        wait_held(2)
        booking_at = [h[2]["providers"] for h in held].index(["booking"])
        release(booking_at, payload={"detail": "boom"}, status=500)
        expect(page.locator('[data-spin="stay:booking"]')).to_be_hidden()
        expect(page.locator("#retry-booking")).to_be_visible()
        release(0, payload={"stays": [], "warnings": [], "center": None})
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_hidden()
        hold.discard("/api/stays")
        print("PASS failing Booking request clears the spinner")

        # 🔍 Search all while stays are loading drops their spinners at once; late answers change nothing.
        expect(page.locator("#search-all")).to_be_enabled()
        hold.add("/api/stays")
        page.locator("#hike-list li").first.click()
        wait_held(2)
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_visible()
        expect(page.locator('[data-spin="stay:booking"]')).to_be_visible()
        expect(page.locator("#stay-section")).to_have_attribute("aria-busy", "true")
        page.click("#search-all")
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_hidden()
        expect(page.locator('[data-spin="stay:booking"]')).to_be_hidden()
        expect(page.locator('[data-spin="stay:retry"]')).to_be_hidden()
        expect(page.locator("#stay-section")).to_have_attribute("aria-busy", "false")
        while held:
            release(0, payload={"stays": [STAY], "warnings": [], "center": None})
        page.wait_for_timeout(300)
        expect(page.locator("#stay-list li")).to_have_count(0)
        expect(page.locator('[data-spin="stay:booking"]')).to_be_hidden()
        hold.discard("/api/stays")
        print("PASS a new search clears stay spinners at once; stale answers are ignored")

        # Provider warnings are merged once both answers are in.
        def stays_with(airbnb: dict, booking: dict) -> None:
            expect(page.locator("#search")).to_be_enabled()
            page.click("#search")
            expect(page.locator("#hike-list li")).to_have_count(1)
            hold.add("/api/stays")
            page.locator("#hike-list li").first.click()
            wait_held(2)
            for provider, payload in (("airbnb", airbnb), ("booking", booking)):
                release([h[2]["providers"][0] for h in held].index(provider),
                        payload={"stays": [], "center": None, **payload})
            hold.discard("/api/stays")

        NO_MATCH = "No stays match your filters"
        stays_with({"stays": [STAY], "warnings": []}, {"warnings": [NO_MATCH]})
        expect(page.locator("#stay-list li")).to_have_count(1)
        expect(page.locator("#notices .notice")).to_have_count(0)  # Airbnb filled the list: no stale "no stays"
        stays_with({"warnings": [NO_MATCH]}, {"warnings": [NO_MATCH]})
        expect(page.locator("#notices .notice")).to_have_count(1)  # both empty: one combined notice
        expect(page.locator("#notices")).to_contain_text(NO_MATCH)
        stays_with({"warnings": ["Airbnb skipped – it can't filter by Breakfast included"]},
                   {"warnings": ["Booking.com skipped – it can't filter by Beds"]})
        expect(page.locator("#notices")).to_contain_text("No source supports this combination of filters")
        expect(page.locator("#notices")).to_contain_text("Airbnb skipped")
        expect(page.locator("#notices")).to_contain_text("Booking.com skipped")
        print("PASS provider warnings merge: stale no-stays dropped, combined notice, no-source notice")

        # ---- Go to place (navigation only) ----
        sent = len(requests)
        hold.add("/api/locate")
        page.fill("#goto-input", "Μακρινίτσα")
        page.press("#goto-input", "Enter")
        wait_held(1)
        expect(page.locator('[data-spin="goto"]')).to_be_visible()
        expect(page.locator("#goto-btn")).to_be_disabled()
        release()
        hold.discard("/api/locate")
        expect(page.locator('[data-spin="goto"]')).to_be_hidden()
        page.wait_for_function("placeLayer.getLayers().length === 1")
        page.wait_for_function(
            "Math.abs(map.getCenter().lat - 39.39) < 0.05 && Math.abs(map.getCenter().lng - 22.99) < 0.05",
            timeout=15000)
        assert page.evaluate("placeLayer.getLayers()[0].getPopup().getContent().textContent") == "Μακρινίτσα, Δήμος Βόλου"
        assert [r[0] for r in requests[sent:]] == ["/api/locate"], requests[sent:]  # no hike or stay search
        expect(page.locator("#hike-list li")).to_have_count(1)  # results untouched
        expect(page.locator("#goto-btn")).to_be_enabled()
        page.unroute(LOCATE_RE)  # not found: warning, the previous pin stays
        page.route(LOCATE_RE, lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"place": None, "warnings": ["Couldn't find 'Zzz' in Greece"]})))
        page.fill("#goto-input", "Zzz")
        page.click("#goto-btn")
        expect(page.locator("#notices")).to_contain_text("Couldn't find 'Zzz' in Greece")
        assert page.evaluate("placeLayer.getLayers().length") == 1
        page.click("#search")  # a new search clears results but not the navigation pin
        expect(page.locator("#hike-list li")).to_have_count(1)
        assert page.evaluate("placeLayer.getLayers().length") == 1
        print("PASS Go to place: pin + fly, no search fired, warning, pin survives a new search")

        # ---- log in / sign up (real API): logged out, ＋ Trip asks to log in first, then carries on ----
        auth_dlg = page.locator("#auth-dialog")
        auth_area = page.locator("#auth-area")
        menu = page.locator("#trip-menu")
        expect(auth_area).to_have_text("👤 Log in", use_inner_text=True)
        page.locator("#hike-list li", has_text="Test hike").locator(".add-trip").click()
        expect(auth_dlg).to_be_visible()
        expect(menu).to_be_hidden()
        expect(page.locator("#auth-username")).to_be_focused()
        page.click("#auth-tab-signup")
        expect(page.locator("#auth-repeat")).to_be_visible()
        expect(page.locator("#auth-emoji-grid .emoji-btn")).to_have_count(EMOJI_COUNT)
        expect(page.locator("#auth-submit")).to_be_disabled()  # Sign up waits for an emoji
        page.fill("#auth-username", "nikos")
        page.fill("#auth-password", "password1")
        page.fill("#auth-repeat", "password2")
        page.press("#auth-repeat", "Enter")  # no emoji yet: Sign up is disabled, Enter sends nothing
        page.wait_for_timeout(200)
        expect(auth_dlg).to_be_visible()
        expect(page.locator("#auth-error")).to_have_text("")
        pick_emoji(page, "🤡")
        expect(page.locator("#auth-emoji-picked")).to_have_text(": 🤡")
        page.press("#auth-repeat", "Enter")
        expect(page.locator("#auth-error")).to_have_text("The passwords don't match")
        page.fill("#auth-repeat", "password1")
        if os.environ.get("UI_SHOTS"):
            page.locator("#auth-dialog").screenshot(path=os.path.join(os.environ["UI_SHOTS"], "signup-picker-desktop.png"))
        page.press("#auth-repeat", "Enter")  # Enter submits
        expect(auth_dlg).to_be_hidden()
        expect(menu).to_be_visible()  # the ＋ Trip action continued after the sign-up
        expect(menu.locator("button")).to_have_count(1)
        expect(menu.locator("button").first).to_have_text("➕ New trip")
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(auth_area).to_have_text("🤡 nikos · Log out", use_inner_text=True)
        assert page.evaluate("fetch('/api/auth/me').then((r) => r.json())") == {"username": "nikos", "emoji": "🤡"}
        expect(page.locator("#emoji-dialog")).to_be_hidden()  # has an emoji: no prompt
        print("PASS logged out ＋ Trip opens the login dialog; Sign up (emoji picked, Enter) continues to the trip menu")

        page.click("#auth-logout")
        expect(auth_area).to_have_text("👤 Log in", use_inner_text=True)
        assert page.evaluate("fetch('/api/auth/me').then((r) => r.json())") == {"username": None, "emoji": None}
        page.click("#auth-login-btn")
        expect(auth_dlg).to_be_visible()
        expect(page.locator("#auth-repeat")).to_be_hidden()
        page.fill("#auth-username", "nikos")
        page.fill("#auth-password", "wrong-password")
        page.click("#auth-submit")
        expect(page.locator("#auth-error")).to_have_text("Wrong username or password")
        expect(auth_dlg).to_be_visible()
        page.click("#auth-tab-signup")  # a taken name and a body-rule error are shown inline too
        page.fill("#auth-password", "password1")
        page.fill("#auth-repeat", "password1")
        expect(page.locator("#auth-submit")).to_be_disabled()  # the last sign-up's pick is not kept
        clown = page.locator('#auth-emoji-grid .emoji-btn[data-emoji="🤡"]')
        expect(clown).to_have_attribute("aria-disabled", "true")
        expect(clown).to_have_attribute("title", "taken by nikos")
        clown.click(force=True)  # aria-disabled: Playwright would wait for it to be enabled
        expect(page.locator("#auth-error")).to_have_text("🤡 is taken by nikos")
        expect(page.locator("#auth-submit")).to_be_disabled()
        pick_emoji(page, "🐸")
        page.click("#auth-submit")
        expect(page.locator("#auth-error")).to_have_text("That username is taken")
        page.fill("#auth-username", "x")
        page.click("#auth-submit")
        expect(page.locator("#auth-error")).not_to_have_text("")
        page.click("#auth-close")
        expect(auth_dlg).to_be_hidden()
        page.locator("#hike-list li", has_text="Test hike").locator(".add-trip").click()
        expect(auth_dlg).to_be_visible()
        page.keyboard.press("Escape")  # closed without logging in: the ＋ Trip action is dropped
        expect(auth_dlg).to_be_hidden()
        page.click("#auth-login-btn")
        page.fill("#auth-username", "nikos")
        page.fill("#auth-password", "password1")
        page.press("#auth-password", "Enter")
        expect(auth_dlg).to_be_hidden()
        expect(menu).to_be_hidden()
        expect(auth_area).to_have_text("🤡 nikos · Log out", use_inner_text=True)
        expect(page.locator("#emoji-dialog")).to_be_hidden()
        print("PASS Log out; wrong password / taken name / taken emoji shown inline; closing the dialog drops the action; "
              "Log in")

        # The cookie expires while the page still shows nikos: the 401 opens the dialog, no replay after login.
        page.context.clear_cookies()
        page.locator("#hike-list li", has_text="Test hike").locator(".add-trip").click()
        expect(menu).to_be_visible()  # the page still thinks we are logged in
        menu.locator("button", has_text="New trip").click()
        expect(auth_dlg).to_be_visible()
        expect(auth_area).to_have_text("👤 Log in", use_inner_text=True)
        expect(page.locator("#notices")).to_contain_text("Log in first")
        page.fill("#auth-username", "nikos")
        page.fill("#auth-password", "password1")
        page.press("#auth-password", "Enter")
        expect(auth_dlg).to_be_hidden()
        expect(auth_area).to_have_text("🤡 nikos · Log out", use_inner_text=True)
        page.wait_for_timeout(300)
        assert page.evaluate("fetch('/api/saved-trips').then((r) => r.json())") == []  # nothing replayed
        print("PASS an expired cookie (401) opens the login dialog; the user repeats the click")

        # ---- saved trips (real API, throwaway TRIPS_DB) ----
        saved_name = "Test hike + Area house"
        expect(page.locator("#search")).to_be_enabled()
        page.check('input[name="mode"][value="near_home"]')
        page.click("#suggest-trips")
        expect(page.locator("#combo-list li")).to_have_count(1)
        save_btn = page.locator("#combo-list .save-combo")
        save_btn.click()
        expect(save_btn).to_have_text("✓ Saved")
        expect(page.locator("#trips-count")).to_have_text("(1)")
        expect(page.locator("#notices")).to_contain_text(f"Saved “{saved_name}”")
        print("PASS 💾 Save on a Trip pick saves it; My trips count (1)")

        # Search hikes (pick view off) with paging so HIKE2 is listed too.
        expect(page.locator("#suggest-trips")).to_be_enabled()
        paging["on"] = True
        page.click("#search")
        expect(page.locator("#load-more")).to_be_visible()
        page.click("#load-more")
        expect(page.locator("#hike-list li")).to_have_count(2)
        paging["on"] = False
        before = len(requests)
        page.locator("#hike-list li", has_text="Test hike").locator(".add-trip").click()
        expect(menu).to_be_visible()
        expect(menu.locator("button")).to_have_count(2)
        expect(menu.locator("button").first).to_have_text("➕ New trip")
        menu.locator("button", has_text=saved_name).click()
        expect(menu).to_be_hidden()
        expect(page.locator("#notices")).to_contain_text(f"Already in “{saved_name}”")
        page.wait_for_timeout(300)
        assert not [r for r in requests[before:] if r[0] == "/api/stays"], requests[before:]
        expect(page.locator("#hike-list li.selected")).to_have_count(0)
        print("PASS ＋ Trip on a hike: menu, already-in notice, the hike is not selected")

        # ↗ Wikiloc opens the hike in a new tab and does not select it (no stay search).
        link = page.locator("#hike-list li", has_text="Test hike").locator("a.open-link")
        expect(link).to_have_text("↗ Wikiloc")
        expect(link).to_have_attribute("href", HIKE["url"])
        expect(link).to_have_attribute("target", "_blank")
        page.context.route(HIKE["url"], lambda route: route.fulfill(status=200, body="wikiloc"))  # stay offline
        before = len(requests)
        with page.context.expect_page() as new_tab:
            link.click()
        new_tab.value.close()
        page.wait_for_timeout(300)
        assert not [r for r in requests[before:] if r[0] == "/api/stays"], requests[before:]
        expect(page.locator("#hike-list li.selected")).to_have_count(0)
        print("PASS ↗ Wikiloc opens the hike in a new tab without selecting it")

        second_add = page.locator("#hike-list li", has_text="Second hike").locator(".add-trip")
        second_add.evaluate("(b) => { b.style.position = 'fixed'; b.style.bottom = '2px'; b.style.left = '40px'; }")
        second_add.click()
        expect(menu).to_be_visible()
        placed = page.evaluate("""() => { const m = document.querySelector('#trip-menu').getBoundingClientRect();
            const b = document.querySelector('#hike-list li .add-trip[style]').getBoundingClientRect();
            return { menuTop: m.top, menuBottom: m.bottom, buttonTop: b.top, inner: window.innerHeight }; }""")
        assert placed["menuBottom"] <= placed["inner"] and placed["menuBottom"] <= placed["buttonTop"], placed
        second_add.evaluate("(b) => b.removeAttribute('style')")
        print("PASS ＋ Trip menu near the bottom edge opens above the button")
        menu.locator("button", has_text=saved_name).click()
        expect(page.locator("#notices")).to_contain_text(f"Added to “{saved_name}”")
        print("PASS ＋ Trip adds a second hike to the saved trip")

        dlg = page.locator("#trips-dialog")
        page.click("#trips-btn")
        expect(dlg).to_be_visible()
        rows = page.locator("#trips-list li")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("2 hikes · 1 stay · ⭐ · by 🤡 nikos")
        rows.first.click()
        expect(page.locator("#trip-editor")).to_be_visible()
        expect(page.locator("#trip-created-by")).to_have_text("Created by 🤡 nikos")
        hikes_ul = page.locator("#trip-hikes li")
        expect(hikes_ul).to_have_count(2)
        expect(hikes_ul.nth(1)).to_contain_text("Second hike")
        hikes_ul.nth(1).locator('button[title="Move up"]').click()
        expect(hikes_ul.nth(0)).to_contain_text("Second hike")
        page.fill("#trip-name", "Renamed trip")
        page.click("#trip-save")
        expect(page.locator("#trip-save")).to_have_text("✓ Saved")
        trip_id = page.evaluate("fetch('/api/saved-trips').then((r) => r.json())")[0]["id"]
        stored = page.evaluate("(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`).then((r) => r.json())",
                               trip_id)
        assert stored["name"] == "Renamed trip", stored
        assert [h["name"] for h in stored["hikes"]] == ["Second hike", "Test hike"], stored["hikes"]
        page.click("#trip-back")
        expect(rows.first).to_contain_text("Renamed trip")
        rows.first.click()
        expect(page.locator("#trip-name")).to_have_value("Renamed trip")
        expect(hikes_ul.nth(0)).to_contain_text("Second hike")
        print("PASS My trips: reorder + rename saved and persisted")

        with page.expect_download() as dl_info:
            page.click("#trip-export")
        download = dl_info.value
        # Chrome prefers the UTF-8 filename* ("Renamed trip.md"); the ASCII fallback is the slug.
        assert download.suggested_filename in ("Renamed trip.md", "renamed-trip.md"), download.suggested_filename
        disposition = page.evaluate("(url) => fetch(url).then((r) => r.headers.get('content-disposition'))",
                                    download.url)
        assert 'filename="renamed-trip.md"' in disposition, disposition
        with open(download.path(), encoding="utf-8") as fh:
            md = fh.read()
        assert md.startswith("# Renamed trip"), md[:80]
        assert "## 🥾 Hikes (2)" in md, md
        print("PASS ⬇ Export .md downloads renamed-trip.md")

        # 🖨️ PDF saves first (PUT), then opens the printable page in a new tab. The tab's /print is stubbed so
        # no print dialog fires; the real page is then loaded with ?noprint=1.
        puts: list[str] = []
        on_request = lambda req: puts.append(req.url) if req.method == "PUT" else None  # noqa: E731
        page.on("request", on_request)
        print_re = re.compile(r"/api/saved-trips/[^/?]+/print$")
        page.context.route(print_re, lambda route: route.fulfill(status=200, content_type="text/html",
                                                                  body="<p>stub</p>"))
        page.fill("#trip-notes", "pdf <b>notes</b>")
        with page.context.expect_page() as pdf_tab:
            page.click("#trip-pdf")
        tab = pdf_tab.value  # opened blank in the click (Safari pop-up rule), navigated after the save
        tab.wait_for_url(re.compile(rf"/api/saved-trips/{re.escape(trip_id)}/print$"))
        assert any(u.endswith(f"/api/saved-trips/{trip_id}") for u in puts), puts
        page.remove_listener("request", on_request)
        page.context.unroute(print_re)
        tab.goto(tab.url + "?noprint=1")
        expect(tab.locator("h1")).to_have_text("Renamed trip")
        expect(tab.locator(".notes")).to_have_text("pdf <b>notes</b>")
        expect(tab.locator("h2", has_text="🥾 Hikes (2)")).to_be_visible()
        tab.close()
        print("PASS 🖨️ PDF saves first, then opens the printable page in a new tab")

        page.click("#trip-show")
        expect(dlg).to_be_hidden()
        expect(page.locator("#hike-list li")).to_have_count(2)
        expect(page.locator("#notices")).to_contain_text("Showing trip “Renamed trip”")
        print("PASS 🗺️ Show on map closes the dialog and lists the trip")

        page.click("#trips-btn")
        rows.first.click()
        page.fill("#trip-notes", "first save")
        page.click("#trip-save")
        expect(page.locator("#trip-save")).to_have_text("✓ Saved")
        page.fill("#trip-notes", "second save")
        page.click("#trip-save")
        expect(page.locator("#trip-save")).to_have_text("✓ Saved")
        expect(page.locator("#trips-error")).to_be_hidden()
        # Another tab saves in between (start only, no end) → this editor's next save is refused.
        status = page.evaluate("""async (id) => {
            const path = `/api/saved-trips/${encodeURIComponent(id)}`;
            const t = await (await fetch(path)).json();
            const res = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ...t, name: "Changed elsewhere", start: "2026-10-23", end: null,
                                     expected_updated_at: t.updated_at }) });
            return res.status; }""", trip_id)
        assert status == 200, status
        page.fill("#trip-notes", "stale edit")
        page.click("#trip-save")
        expect(page.locator("#trips-error")).to_contain_text("Trip changed elsewhere – reopen it")
        stored = page.evaluate("(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`).then((r) => r.json())",
                               trip_id)
        assert stored["name"] == "Changed elsewhere" and stored["notes"] == "second save", stored
        page.click("#trip-back")
        expect(rows.first).to_contain_text("Changed elsewhere")
        expect(rows.first).to_contain_text("From 2026-10-23 · ")
        print("PASS consecutive saves work; a stale save is refused (409); start-only shows From …")

        # Paste an Airbnb link: dates/guests/dog come from the editor (not the link), the stay is saved at once.
        get_trip = "(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`).then((r) => r.json())"
        rows.first.click()
        stays_ul = page.locator("#trip-stays li")
        expect(stays_ul).to_have_count(1)
        page.fill("#trip-end", "2026-10-25")
        page.fill("#trip-guests", "4")
        before = len(requests)
        page.fill("#trip-airbnb-url", AIRBNB_LINK)
        page.press("#trip-airbnb-url", "Enter")
        body = wait_request("/api/stays/lookup", before)
        assert body == {"url": AIRBNB_LINK, "start": "2026-10-23", "end": "2026-10-25", "guests": 4,
                        "dog": True}, body
        linked = page.locator("#trip-stays li", has_text="Bezeniko")
        expect(linked).to_have_count(1)
        expect(page.locator("#trip-airbnb-info")).to_have_text(PRICE_NOT_FOUND)
        expect(page.locator("#trip-airbnb-url")).to_have_value("")
        expect(page.locator("#trip-airbnb-spin")).to_be_hidden()
        price = linked.locator(".price-edit input")
        expect(price).to_be_visible()
        expect(page.locator("#trip-stays li", has_text="Area house").locator(".price-edit input")).to_have_value("180")
        expect(page.locator("#trip-save")).to_have_text("Save changes")  # Enter did not submit the form
        for _ in range(30):  # persisted right away, together with the unsaved end date
            stored = page.evaluate(get_trip, trip_id)
            if len(stored["stays"]) == 2:
                break
            page.wait_for_timeout(100)
        assert [s["id"] for s in stored["stays"]] == ["airbnb:1", "airbnb:576031322358400592"], stored["stays"]
        assert stored["stays"][1]["total_price"] is None and stored["end"] == "2026-10-25", stored
        print("PASS Airbnb link: editor dates/guests sent, stay listed with a price field, saved at once")

        page.fill("#trip-airbnb-url", "576031322358400592")
        page.click("#trip-airbnb-add")
        expect(page.locator("#trip-airbnb-info")).to_have_text("Already in this trip")
        expect(stays_ul).to_have_count(2)
        hold.add("/api/stays/lookup")
        page.fill("#trip-airbnb-url", "https://evil.com/rooms/1")
        page.click("#trip-airbnb-add")
        wait_held(1)
        expect(page.locator("#trip-airbnb-spin")).to_be_visible()
        for busy in ("#trip-save", "#trip-export", "#trip-pdf", "#trip-airbnb-add"):  # nothing races the lookup
            expect(page.locator(busy)).to_be_disabled()
        release(payload={"detail": "Paste an Airbnb room link (airbnb.com/rooms/…)"}, status=422)
        hold.discard("/api/stays/lookup")
        expect(page.locator("#trips-error")).to_have_text("Paste an Airbnb room link (airbnb.com/rooms/…)")
        expect(page.locator("#trip-airbnb-spin")).to_be_hidden()
        expect(stays_ul).to_have_count(2)
        for busy in ("#trip-save", "#trip-export", "#trip-pdf", "#trip-airbnb-add"):
            expect(page.locator(busy)).to_be_enabled()
        print("PASS Airbnb link: duplicate says Already in this trip; API error shown; buttons disabled while waiting")

        # The editor is closed while Airbnb answers: the stay is not added to any trip.
        hold.add("/api/stays/lookup")
        page.fill("#trip-airbnb-url", "42")
        page.click("#trip-airbnb-add")
        wait_held(1)
        page.click("#trip-back")
        release(payload={"stay": {**LINKED_STAY, "id": "airbnb:42", "name": "Late house"}, "warnings": []})
        hold.discard("/api/stays/lookup")
        page.wait_for_timeout(300)
        assert len(page.evaluate(get_trip, trip_id)["stays"]) == 2
        rows.first.click()
        expect(stays_ul).to_have_count(2)
        expect(page.locator("#trip-stays li", has_text="Late house")).to_have_count(0)
        print("PASS a lookup answering after the editor closed adds nothing")

        price.fill("-5")
        expect(price).to_have_class(re.compile(r"\binvalid\b"))
        page.click("#trip-save")
        page.wait_for_timeout(300)
        expect(page.locator("#trip-save")).to_have_text("Save changes")  # invalid price blocks the save
        assert page.evaluate(get_trip, trip_id)["stays"][1]["total_price"] is None
        print("PASS a negative price is marked invalid and not saved")

        price.fill("350")
        page.click("#trip-save")
        expect(page.locator("#trip-save")).to_have_text("✓ Saved")
        expect(linked.locator(".price-edit input")).to_have_value("350")
        stored = page.evaluate(get_trip, trip_id)
        assert (stored["stays"][1]["total_price"], stored["stays"][1]["currency"]) == (350, "EUR"), stored["stays"]
        page.click("#trip-back")
        print("PASS typed total price saved with Save changes (EUR)")

        # 🌿 Find nearby sights: saves first (PUT), then POSTs nearby-sights (answered here with canned JSON built
        # from the stored trip, so no Overpass request); buttons are disabled while it runs.
        nearby_re = re.compile(r"/api/saved-trips/[^/?]+/nearby-sights$")
        nearby_held: list = []
        sent: list[tuple[str, str]] = []
        def on_sent(req) -> None:
            if "/api/saved-trips/" in req.url:
                sent.append((req.method, req.url))

        page.route(nearby_re, lambda route: nearby_held.append(route))
        page.on("request", on_sent)
        busy_ids = ("#trip-save", "#trip-export", "#trip-pdf", "#trip-airbnb-add", "#trip-find-sights")

        def nearby_click() -> None:
            nearby_held.clear()
            page.click("#trip-find-sights")
            for _ in range(60):
                if nearby_held:
                    return
                page.wait_for_timeout(100)
            raise SystemExit("FAIL no nearby-sights request")

        def nearby_answer(payload, status: int = 200) -> None:
            nearby_held.pop().fulfill(status=status, content_type="application/json", body=json.dumps(payload))

        rows.first.click()
        sights_ul = page.locator("#trip-sights li:not(.list-note)")
        expect(sights_ul).to_have_count(0)
        page.fill("#trip-notes", "before sights")  # unsaved edit: must be saved before the search
        sent.clear()
        nearby_click()
        expect(page.locator("#trip-sights-spin")).to_be_visible()
        for busy in busy_ids:
            expect(page.locator(busy)).to_be_disabled()
        # The editor is locked while the search runs: item buttons, prices and fields can't change.
        expect(page.locator("#trip-hikes li").first.get_by_role("button", name=re.compile("^Remove"))).to_be_disabled()
        expect(page.locator("#trip-stays .price-edit input").first).to_be_disabled()
        expect(page.locator("#trip-notes")).not_to_be_editable()
        expect(page.locator("#trip-name")).not_to_be_editable()
        expect(page.locator("#trip-dog")).to_be_disabled()
        trip_url = f"{BASE}/api/saved-trips/{trip_id}"
        assert sent.index(("PUT", trip_url)) < sent.index(("POST", f"{trip_url}/nearby-sights")), sent
        assert nearby_held[0].request.post_data_json == {"radius_km": 5}, nearby_held[0].request.post_data
        stored = page.evaluate(get_trip, trip_id)
        assert stored["notes"] == "before sights", stored
        spread = "Trip is spread out – searched near the first places only"
        nearby_answer({"trip": {**stored, "sights": [WATERFALL, {**RIVER, "lines": []}]}, "added": 2, "found": 3,
                       "warnings": [spread]})
        expect(sights_ul).to_have_count(2)
        expect(sights_ul.nth(0)).to_contain_text("💧 Test falls")
        expect(sights_ul.nth(1)).to_contain_text("Test river")
        expect(page.locator("#trip-sights-info")).to_have_text(f"Added 2 new sights (3 found within 5 km) · {spread}")
        expect(page.locator("#trip-sights-spin")).to_be_hidden()
        for busy in busy_ids:
            expect(page.locator(busy)).to_be_enabled()
        expect(page.locator("#trip-hikes li").first.get_by_role("button", name=re.compile("^Remove"))).to_be_enabled()
        expect(page.locator("#trip-hikes li").first.get_by_role("button", name=re.compile("^Move up"))).to_be_disabled()
        expect(page.locator("#trip-notes")).to_be_editable()
        expect(page.locator("#trip-dog")).to_be_enabled()
        print("PASS 🌿 Find nearby sights saves first, disables buttons, lists the added sights with the info line")

        nearby_click()
        expect(page.locator("#trip-sights-info")).to_be_hidden()
        nearby_answer({"trip": page.evaluate(get_trip, trip_id), "added": 0, "found": 0, "warnings": []})
        expect(page.locator("#trip-sights-info")).to_have_text("No new named sights within 5 km")
        expect(sights_ul).to_have_count(2)  # saved by this click's PUT, so the stored trip now holds them
        nearby_click()
        nearby_answer({"detail": "Wait a moment before searching this trip again"}, status=429)
        expect(page.locator("#trips-error")).to_have_text("Wait a moment before searching this trip again")
        expect(page.locator("#trip-find-sights")).to_be_enabled()
        nearby_click()  # stale: the editor closes before the answer
        page.click("#trip-back")
        nearby_answer({"trip": {**page.evaluate(get_trip, trip_id), "name": "Stale answer"}, "added": 1, "found": 1,
                       "warnings": []})
        page.wait_for_timeout(300)
        expect(page.locator("#trip-editor")).to_be_hidden()
        rows.first.click()
        expect(page.locator("#trip-name")).not_to_have_value("Stale answer")
        expect(page.locator("#trip-sights-info")).to_be_hidden()
        page.click("#trip-back")
        page.remove_listener("request", on_sent)
        page.unroute(nearby_re)
        print("PASS 🌿 Find nearby sights: nothing new, API error and a stale answer are handled")

        # Logged out: ⬇ Export and 🖨️ PDF of the stored trip work with no login dialog and no save (PUT).
        page.click("#trips-close")
        page.click("#auth-logout")
        expect(auth_area).to_have_text("👤 Log in", use_inner_text=True)
        page.click("#trips-btn")
        rows.first.click()
        expect(page.locator("#trip-editor")).to_be_visible()
        writes: list[str] = []

        def on_write(req) -> None:
            if req.method in ("PUT", "POST", "DELETE") and "/api/saved-trips" in req.url:
                writes.append(f"{req.method} {req.url}")

        page.on("request", on_write)
        with page.expect_download() as download_info:
            page.click("#trip-export")
        assert download_info.value.suggested_filename.endswith(".md"), download_info.value.suggested_filename
        with page.context.expect_page() as pdf_info:
            page.click("#trip-pdf")
        pdf_tab = pdf_info.value
        pdf_tab.wait_for_url(re.compile(r"/api/saved-trips/[^/?]+/print$"))
        expect(pdf_tab.locator("h1")).to_be_visible()
        pdf_tab.close()
        expect(auth_dlg).to_be_hidden()
        expect(page.locator("#trips-error")).to_be_hidden()
        page.remove_listener("request", on_write)
        assert not writes, writes
        print("PASS logged out: ⬇ Export downloads the .md and 🖨️ PDF opens …/print, no login dialog, no save")

        # Logged-out Delete asks to log in first, then carries on (arms the two-click confirm).
        page.click("#trip-delete")
        expect(auth_dlg).to_be_visible()
        page.fill("#auth-username", "nikos")
        page.fill("#auth-password", "password1")
        page.press("#auth-password", "Enter")
        expect(auth_dlg).to_be_hidden()
        expect(page.locator("#trip-delete")).to_have_text("Confirm delete")
        page.click("#trip-delete")
        expect(rows).to_have_count(0)
        expect(page.locator("#trips-empty")).to_be_visible()
        expect(page.locator("#trips-count")).to_have_text("(0)")
        page.click("#trips-close")
        expect(dlg).to_be_hidden()
        print("PASS logged-out Delete asks to log in, then needs two clicks; list empty, count (0)")

        # ---- 🌿 Nature layer (canned /api/nature; the trip part uses the real saved-trips API) ----
        def nature_settle(ms: int = 900) -> None:
            page.wait_for_timeout(ms)  # longer than the 600 ms debounce

        def wait_nature(count: int) -> dict:
            for _ in range(60):
                if len(nature_reqs) >= count:
                    return nature_reqs[count - 1]
                page.wait_for_timeout(100)
            raise SystemExit(f"FAIL expected {count} nature requests, have {len(nature_reqs)}")

        def wait_nature_held(count: int) -> None:
            for _ in range(40):
                if len(nature_held) >= count:
                    return
                page.wait_for_timeout(100)
            raise SystemExit(f"FAIL expected {count} held nature requests, have {len(nature_held)}")

        sight_pins = page.locator(".leaflet-marker-icon.pin.sight:not(.trip-sight)")
        page.evaluate("map.setView([38.5, 22.6], 7, {animate: false})")
        nature_settle()
        assert not nature_reqs, nature_reqs  # toggle off: no request
        expect(page.locator("#nature-chips")).to_be_hidden()
        page.click("#nature-btn")
        expect(page.locator("#nature-btn")).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator("#nature-chips")).to_be_visible()
        expect(page.locator("#nature-hint")).to_be_visible()
        expect(page.locator("#nature-hint")).to_have_text("Zoom in to see nature sights")
        nature_settle()
        assert not nature_reqs, nature_reqs
        print("PASS 🌿 Nature on at low zoom: hint, chips, no request")

        for zoom in (9, 10):  # a 1040 px wide map at zoom 9/10 spans far more than 16 tiles: no request
            page.evaluate(f"map.setView([38.52, 22.62], {zoom}, {{animate: false}})")
            nature_settle()
            expect(page.locator("#nature-hint")).to_be_visible()
            assert not nature_reqs, (zoom, nature_reqs)
        print("PASS a view wider than 16 tiles: hint, no request")

        page.evaluate("map.setView([38.52, 22.62], 12, {animate: false})")
        q = wait_nature(1)
        expect(sight_pins).to_have_count(2)
        expect(page.locator("#nature-hint")).to_be_hidden()
        assert q["groups"] == "water,landforms,views,protected", q
        view = page.evaluate("(() => { const b = map.getBounds(); return {s: b.getSouth(), w: b.getWest(), "
                             "n: b.getNorth(), e: b.getEast()}; })()")
        s_, w_, n_, e_ = (float(q[k]) for k in ("south", "west", "north", "east"))
        pad_lat, pad_lon = (view["n"] - view["s"]) * 0.1, (view["e"] - view["w"]) * 0.1
        assert abs(s_ - (view["s"] - pad_lat)) < 1e-3 and abs(n_ - (view["n"] + pad_lat)) < 1e-3, (q, view)
        assert abs(w_ - (view["w"] - pad_lon)) < 1e-3 and abs(e_ - (view["e"] + pad_lon)) < 1e-3, (q, view)
        assert page.evaluate("nature.layer.getLayers().filter((l) => l instanceof L.Polyline).length") == 1
        assert page.evaluate("""nature.layer.getLayers().filter((l) => l instanceof L.Polyline)
            .every((l) => l.options.renderer === nature.canvas && l.options.color === '#3b7dd8')""")
        nature_settle()
        assert len(nature_reqs) == 1, nature_reqs  # moveend + zoomend debounced into one request
        expect(page.locator(".leaflet-control-attribution")).to_contain_text("Nature sights © OpenStreetMap")
        print("PASS zoom 12: one request with all groups and the padded bbox; pins + river line drawn")

        page.click('.nature-chip[data-group="views"]')
        expect(page.locator('.nature-chip[data-group="views"]')).to_have_attribute("aria-pressed", "false")
        q = wait_nature(2)
        assert q["groups"] == "water,landforms,protected", q
        print("PASS chip off: request without that group")

        # The padded box would need 6 x 6 tiles, the bare view 4 x 4: the request uses the bare view.
        before = len(nature_reqs)
        page.evaluate("""map.getBounds = () => L.latLngBounds([[38.05, 22.05], [38.95, 22.95]]);
            map.fire('moveend')""")
        q = wait_nature(before + 1)
        page.evaluate("delete map.getBounds")  # back to Leaflet's own
        assert (q["south"], q["west"], q["north"], q["east"]) == ("38.05", "22.05", "38.95", "22.95"), q
        expect(page.locator("#nature-hint")).to_be_hidden()
        page.evaluate("map.setView([38.52, 22.62], 12, {animate: false})")
        wait_nature(before + 2)
        nature_settle()
        print("PASS padded box over 16 tiles: the request uses the unpadded view")

        # An open popup survives a refresh that returns the same sight (incremental render).
        sight_pins.filter(has_text="💧").click()
        expect(page.locator(".leaflet-popup")).to_contain_text("Test falls")
        page.evaluate("window._falls = nature.rendered.get('osm:node/1').layers.at(-1)")
        before = len(nature_reqs)
        page.evaluate("map.panBy([5, 0], {animate: false})")
        wait_nature(before + 1)
        nature_settle()
        expect(page.locator(".leaflet-popup")).to_contain_text("Test falls")
        assert page.evaluate("nature.rendered.get('osm:node/1').layers.at(-1) === window._falls")
        page.evaluate("map.closePopup()")
        print("PASS an open sight popup survives a refresh (same marker kept)")

        # Lines ignore the mouse while a draw tool is active.
        page.click("#draw-rect-btn")
        assert page.evaluate("nature.layer.getLayers().filter((l) => l instanceof L.Polyline)"
                             ".every((l) => !l.options.interactive)")
        page.keyboard.press("Escape")
        assert page.evaluate("nature.layer.getLayers().filter((l) => l instanceof L.Polyline)"
                             ".every((l) => l.options.interactive)")
        nature_settle()
        print("PASS nature lines are not interactive while drawing")

        before = len(nature_reqs)
        for _ in range(5):
            page.evaluate("map.panBy([40, 0], {animate: false})")
            page.wait_for_timeout(100)
        nature_settle()
        assert len(nature_reqs) == before + 1, nature_reqs[before:]
        print("PASS panning quickly makes one request (debounce)")

        # A stale answer is ignored: the newer request answers first, the older one afterwards.
        nature_cfg["hold"] = True
        page.evaluate("map.panBy([-100, 0], {animate: false})")
        wait_nature_held(1)
        expect(page.locator('[data-spin="nature"]')).to_be_visible()
        page.evaluate("map.panBy([-100, 0], {animate: false})")
        wait_nature_held(2)
        nature_cfg["hold"] = False
        nature_held.pop(1).fulfill(status=200, content_type="application/json",
                                   body=json.dumps({"sights": [WATERFALL], "warnings": []}))
        expect(sight_pins).to_have_count(1)
        expect(page.locator('[data-spin="nature"]')).to_be_hidden()
        nature_held.pop(0).fulfill(status=200, content_type="application/json",
                                   body=json.dumps({"sights": [WATERFALL, RIVER], "warnings": []}))
        page.wait_for_timeout(300)
        expect(sight_pins).to_have_count(1)
        print("PASS a stale nature answer is ignored; spinner chip while loading")

        # Warnings: shown once, not repeated while panning; "Zoom in" (422) shows the hint.
        nature_cfg["payload"] = {"sights": [WATERFALL, RIVER], "warnings": [NATURE_DOWN]}
        before = len(nature_reqs)
        page.evaluate("map.panBy([60, 0], {animate: false})")
        wait_nature(before + 1)
        expect(page.locator("#notices")).to_contain_text(NATURE_DOWN)
        page.evaluate("notices(['other notice'])")
        page.evaluate("map.panBy([60, 0], {animate: false})")
        wait_nature(before + 2)
        page.wait_for_timeout(300)
        expect(page.locator("#notices")).to_have_text("other notice")
        nature_cfg.update(payload={"detail": "Zoom in to see nature sights"}, status=422)
        page.evaluate("map.panBy([60, 0], {animate: false})")
        wait_nature(before + 3)
        expect(page.locator("#nature-hint")).to_be_visible()
        expect(sight_pins).to_have_count(0)
        expect(page.locator("#notices")).not_to_contain_text("Zoom in")
        nature_cfg.update(payload=None, status=200)
        print("PASS nature warning shown once (no spam); 422 Zoom in shows the hint")

        page.evaluate("map.setView([38.52, 22.62], 12, {animate: false})")
        expect(sight_pins).to_have_count(2)
        expect(page.locator("#search")).to_be_enabled()
        page.check('input[name="mode"][value="near_home"]')
        page.click("#search")
        expect(page.locator("#hike-list li")).to_have_count(1)
        expect(page.locator("#search-all")).to_be_enabled()
        page.click("#search-all")
        expect(page.locator("#hike-list li")).to_have_count(1)
        expect(page.locator("#search-all")).to_be_enabled()
        page.evaluate("map.setView([38.52, 22.62], 12, {animate: false})")
        nature_settle()
        expect(sight_pins).to_have_count(2)
        assert page.evaluate("nature.layer.getLayers().length") == 3
        print("PASS Search hikes / Search all keep the nature pins")

        # ＋ Trip on the waterfall popup → New trip; again → Already in.
        get_trip = "(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`).then((r) => r.json())"
        sight_pins.filter(has_text="💧").click()
        popup = page.locator(".leaflet-popup")
        expect(popup).to_contain_text("Test falls")
        expect(popup).to_contain_text("Waterfall · 420 m")
        expect(popup.locator("a", has_text="↗ OpenStreetMap")).to_have_attribute("href", WATERFALL["url"])
        expect(popup.locator("a", has_text="↗ Wikipedia")).to_have_attribute("href", WATERFALL["wikipedia"])
        expect(popup).to_contain_text("© OpenStreetMap contributors")
        popup.locator(".add-trip").click()
        expect(menu).to_be_visible()
        expect(menu.locator("button")).to_have_count(1)  # no trips left: only ➕ New trip
        menu.locator("button").first.click()
        expect(page.locator("#notices")).to_contain_text("Added to “Test falls”")
        expect(page.locator("#trips-count")).to_have_text("(1)")
        sight_pins.filter(has_text="💧").click()
        popup.locator(".add-trip").click()
        menu.locator("button", has_text="Test falls").click()
        expect(page.locator("#notices")).to_contain_text("Already in “Test falls”")
        print("PASS ＋ Trip on a sight popup: new trip, then Already in")

        page.click("#trips-btn")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("0 hikes · 0 stays · 1 sight")
        rows.first.click()
        sights_ul = page.locator("#trip-sights li")
        expect(sights_ul).to_have_count(1)
        expect(sights_ul.first).to_contain_text("💧 Test falls")
        expect(sights_ul.first.locator("a")).to_have_attribute("href", WATERFALL["url"])
        expect(sights_ul.first.locator('button[title="Remove"]')).to_be_visible()
        sight_trip_id = page.evaluate("fetch('/api/saved-trips').then((r) => r.json())")[0]["id"]
        stored = page.evaluate(get_trip, sight_trip_id)
        assert [x["id"] for x in stored["sights"]] == ["osm:node/1"] and stored["sights"][0]["lines"] == [], stored
        with page.expect_download() as dl_info:
            page.click("#trip-export")
        with open(dl_info.value.path(), encoding="utf-8") as fh:
            md = fh.read()
        assert "## 🌿 Nature (1)" in md and "Test falls" in md, md
        print("PASS editor lists the sight under 🌿 Nature; export has ## 🌿 Nature (1)")

        page.click("#trip-show")
        expect(dlg).to_be_hidden()
        expect(page.locator(".leaflet-marker-icon.pin.trip-sight")).to_have_count(1)
        nature_settle()
        assert page.evaluate("nature.layer.getLayers().length") > 0  # Show on map leaves the nature layer
        # The trip's sight is pinned once (the distinct trip pin), not again by the nature layer.
        expect(sight_pins.filter(has_text="💧")).to_have_count(0)
        assert page.evaluate("nature.rendered.get('osm:node/1')?.pinned ?? null") is False  # drawn, no 2nd pin
        page.click("#nature-btn")  # off: layer, chips, attribution go; the trip's sight stays
        expect(page.locator("#nature-chips")).to_be_hidden()
        assert page.evaluate("nature.layer.getLayers().length") == 0
        expect(page.locator(".leaflet-marker-icon.pin.trip-sight")).to_have_count(1)
        expect(page.locator(".leaflet-control-attribution")).not_to_contain_text("Nature sights")
        expect(page.locator("#search-all")).to_be_enabled()
        page.click("#search-all")  # clearAll empties the trip sights
        expect(page.locator(".leaflet-marker-icon.pin.trip-sight")).to_have_count(0)
        print("PASS Show on map pins trip sights; toggle off clears the layer; clearAll drops trip sights")

        page.click("#nature-btn")  # on again: toggle + chips persist across reloads
        load()
        expect(page.locator("#nature-btn")).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator('.nature-chip[data-group="views"]')).to_have_attribute("aria-pressed", "false")
        expect(page.locator('.nature-chip[data-group="water"]')).to_have_attribute("aria-pressed", "true")
        page.evaluate("localStorage.setItem('hick-toors:nature-groups', '{bad')")
        load()
        expect(page.locator('.nature-chip[data-group="views"]')).to_have_attribute("aria-pressed", "true")
        page.click("#nature-btn")
        status = page.evaluate("(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`, {method: 'DELETE'})"
                               ".then((r) => r.status)", sight_trip_id)
        assert status in (200, 204), status
        print("PASS nature toggle + chips persisted; corrupt storage falls back to all groups")

        check_voting(browser, errors)
        check_emojis(browser, errors)
        check_overview(browser, errors)  # leaves its round closed: the phone scenario starts a new one
        check_mobile(browser, errors)
        check_mobile_landscape(browser, errors)
        browser.close()
    if errors:
        raise SystemExit(f"FAIL page errors: {errors}")
    print("ALL UI CHECKS PASSED")


def check_voting(browser, errors: list[str]) -> None:
    """🗳️ Voting round with two people in two browser contexts (real rounds API, no mocks)."""
    def new_page():
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.goto(BASE)
        pg.wait_for_function("typeof openVote === 'function' && auth.ready")
        return ctx, pg

    def sign_up(pg, name: str, emoji: str, taken: dict | None = None) -> None:
        if not pg.locator("#auth-dialog").is_visible():
            pg.click("#auth-login-btn")
        pg.click("#auth-tab-signup")
        for other, owner in (taken or {}).items():  # someone else's emoji: disabled, "taken by <name>"
            button = pg.locator(f'#auth-emoji-grid .emoji-btn[data-emoji="{other}"]')
            expect(button).to_have_attribute("aria-disabled", "true")
            expect(button).to_have_attribute("title", f"taken by {owner}")
        pg.fill("#auth-username", name)
        pg.fill("#auth-password", "password1")
        pg.fill("#auth-repeat", "password1")
        pick_emoji(pg, emoji)
        pg.press("#auth-repeat", "Enter")
        expect(pg.locator("#auth-dialog")).to_be_hidden()
        expect(pg.locator("#auth-area")).to_have_text(f"{emoji} {name} · Log out", use_inner_text=True)

    def row(pg, trip_id: str):
        return pg.locator(f'#vote-trips .vote-trip[data-trip="{trip_id}"]')

    def rate(scope, rating: str):
        return scope.locator(".vote-buttons").first.locator(f'[data-rating="{rating}"]')

    ctx_a, a = new_page()
    sign_up(a, "anna", "🍕", {"🤡": "nikos"})
    seed = """(body) => fetch('/api/saved-trips', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)}).then((r) => r.json())"""
    t1 = a.evaluate(seed, {"name": "Vote trip one", "hikes": [HIKE], "stays": [STAY, BSTAY]})["id"]
    t2 = a.evaluate(seed, {"name": "Vote trip two", "hikes": [HIKE2], "stays": [STAY]})["id"]
    a.reload()
    a.wait_for_function("typeof openVote === 'function' && auth.ready")
    expect(a.locator("#vote-left")).to_be_hidden()

    # No round yet: the Start a round form, closing default ≈ now + 3 days, every trip ticked.
    a.click("#vote-btn")
    dlg_a = a.locator("#vote-dialog")
    expect(a.locator("#vote-start")).to_be_visible()
    expect(a.locator("#vote-start-trips input:checked")).to_have_count(2)
    in_days = a.evaluate("(new Date(document.getElementById('vote-start-at').value) - Date.now()) / 86400000")
    assert 2.9 < in_days <= 3.01, in_days
    a.fill("#vote-start-title", "November 🥾")
    a.click("#vote-start-submit")
    expect(a.locator("#vote-round-view")).to_be_visible()
    expect(a.locator("#vote-status")).to_contain_text("November 🥾")
    expect(a.locator("#vote-status")).to_contain_text("closes in 2 d 23 h")
    # Only participants (opener, joiners, voters) are waited for: just anna so far.
    expect(a.locator("#vote-status div", has_text="Waiting for")).to_have_text("Waiting for: 🍕 anna")
    expect(a.locator("#vote-status")).to_contain_text("opened by 🍕 anna")
    expect(a.locator("#vote-status")).to_contain_text("You: 0 of 2 trips rated")
    expect(a.locator("#vote-left")).to_have_text(" · 2 left")
    expect(a.locator("#vote-btn")).to_have_class(re.compile(r"\bhas-left\b"))
    print("PASS Start a round: form defaults, round view, badge · 2 left")

    trip_order = """() => [...document.querySelectorAll('#vote-trips > .vote-trip')].map((li) => li.dataset.trip)"""
    start_order = a.evaluate(trip_order)
    rate(row(a, t1), "love").click()
    expect(rate(row(a, t1), "love")).to_have_attribute("aria-pressed", "true")
    expect(a.locator("#vote-status")).to_contain_text("😍 Your Love: Vote trip one")
    rate(row(a, t2), "love").click()
    expect(a.locator("#vote-toast")).to_have_text("Moved your Love from Vote trip one to Vote trip two")
    expect(rate(row(a, t1), "ok")).to_have_attribute("aria-pressed", "true")
    expect(rate(row(a, t2), "love")).to_have_attribute("aria-pressed", "true")
    # A stay: ▸ Stays (1) on trip two stays open across the re-render the vote causes.
    row(a, t2).locator("button", has_text="Stays (1)").click()
    stay_a = row(a, t2).locator(f'.vote-stay[data-stay="{STAY["id"]}"]')
    expect(stay_a).to_be_visible()
    rate(stay_a, "love").click()
    expect(rate(stay_a, "love")).to_have_attribute("aria-pressed", "true")
    expect(stay_a).to_be_visible()
    expect(row(a, t2).locator("button", has_text="▾ Stays (1)")).to_be_visible()
    trips_a = a.locator("#vote-trips > .vote-trip")
    # Rows never move by themselves while the dialog is open; ↻ Sort by rank shows when they are out of rank order.
    assert a.evaluate(trip_order) == start_order, (a.evaluate(trip_order), start_order)
    sort_a = a.locator("#vote-sort")
    if start_order[0] != t2:
        expect(sort_a).to_be_visible()
        sort_a.click()
    expect(sort_a).to_be_hidden()
    expect(trips_a.nth(0)).to_have_attribute("data-trip", t2)
    expect(trips_a.nth(0).locator(".vote-head").first).to_have_text("#1 Vote trip two · 2 pts")
    expect(trips_a.nth(1).locator(".vote-head").first).to_have_text("#2 Vote trip one · 1 pts")
    expect(a.locator("#vote-left")).to_have_text(" · 0 left")
    expect(a.locator("#vote-btn")).not_to_have_class(re.compile(r"\bhas-left\b"))
    # 💬 comment: saved on Enter/blur with the current rating.
    comment_a = row(a, t2).locator(".vote-comment").first
    expect(comment_a).to_be_enabled()
    comment_a.fill("can't wait 🥾")
    comment_a.press("Enter")
    expect(row(a, t2).locator(".count-btn").first).to_have_attribute("title", "Love it: 🍕 anna")
    get_round = "fetch('/api/rounds/current').then((r) => r.json())"
    for _ in range(30):
        trip_two = next(t for t in a.evaluate(get_round)["trips"] if t["trip_id"] == t2)
        mine = [v for v in trip_two["votes"] if v["username"] == "anna"]
        if mine and mine[0]["comment"] == "can't wait 🥾":
            break
        a.wait_for_timeout(100)
    else:
        raise SystemExit(f"FAIL comment not saved: {mine}")
    print("PASS votes: Love moves with a toast, stay vote, rank order, badge · 0 left, comment saved on Enter")

    # B (logged out) watches; A's next vote shows up within one poll (10 s) without any click.
    ctx_b, b = new_page()
    b.click("#vote-btn")
    expect(b.locator("#vote-round-view")).to_be_visible()
    expect(b.locator("#vote-left")).to_be_hidden()
    expect(row(b, t2).locator(".count-btn").first).to_have_text("😍1")
    expect(b.locator("#vote-status")).to_contain_text("log in to vote")
    bstay_count = row(b, t1).locator(f'.vote-stay[data-stay="{BSTAY["id"]}"] .count-btn').nth(1)
    expect(bstay_count).to_have_text("🙂0")
    row(a, t1).locator("button", has_text="Stays (2)").click()
    rate(row(a, t1).locator(f'.vote-stay[data-stay="{BSTAY["id"]}"]'), "ok").click()
    expect(bstay_count).to_have_text("🙂1", timeout=12000)
    print("PASS logged-out B sees the ranking; A's new vote appears in B's dialog within one poll")

    # B's No-go asks to log in first (sign up), then is cast; the comment is shown as text.
    rate(row(b, t1), "nogo").click()
    expect(b.locator("#auth-dialog")).to_be_visible()
    sign_up(b, "bob", "🦖", {"🤡": "nikos", "🍕": "anna"})
    expect(rate(row(b, t1), "nogo")).to_have_attribute("aria-pressed", "true")
    comment_b = row(b, t1).locator(".vote-comment").first
    comment_b.fill("<b>no</b>")
    comment_b.press("Enter")
    nogo_line = row(b, t1).locator(".vote-nogo")
    expect(nogo_line).to_have_text("⚠ 🦖 bob: No-go – “<b>no</b>”")
    expect(b.locator("#vote-dialog b")).to_have_count(0)
    rate(row(b, t2), "ok").click()
    expect(rate(row(b, t2), "ok")).to_have_attribute("aria-pressed", "true")
    expect(b.locator("#vote-left")).to_have_text(" · 0 left")
    # Participants only (anna opened, bob voted – nikos never took part): nobody left to wait for.
    expect(b.locator("#vote-status")).to_contain_text("Everyone has rated every trip ✓")
    names_btn = row(b, t1).locator(".vote-counts").first.locator(".count-btn").nth(2)  # 🚫 count: the names box opens
    names_btn.click()
    expect(row(b, t1).locator(".vote-names").first).to_contain_text("🚫 No-go: 🦖 bob (“<b>no</b>”)")
    if os.environ.get("UI_SHOTS"):
        b.locator("#vote-dialog").screenshot(path=os.path.join(os.environ["UI_SHOTS"], "vote-names-emojis.png"))
    names_btn.click()
    expect(b.locator("#vote-tools")).to_be_hidden()  # not the opener
    print("PASS B: No-go after sign-up carries on, comment <b>no</b> rendered as text, badge · 0 left")

    # Trip editor line → Vote opens the dialog at that trip.
    b.click("#vote-close")
    b.click("#trips-btn")
    b.locator("#trips-list li", has_text="Vote trip one").click()
    expect(b.locator("#trip-round")).to_have_text("🗳️ #2 in 'November 🥾' · 😍0 🙂1 🚫1 → Vote")
    b.locator("#trip-round button", has_text="Vote").click()
    expect(b.locator("#trips-dialog")).to_be_hidden()
    expect(b.locator("#vote-dialog")).to_be_visible()
    expect(row(b, t1)).to_have_class(re.compile(r"\bfocus\b"))
    print("PASS trip editor shows the round line; → Vote opens the dialog at the trip")

    # A sees B's votes, closes the round with two clicks → 🏆 banner, buttons disabled, Start a new round.
    a.evaluate("refreshVote()")
    expect(row(a, t1).locator(".vote-nogo")).to_have_text("⚠ 🦖 bob: No-go – “<b>no</b>”")

    # Changing a rating while typing a comment, the desktop Safari / macOS Firefox way: the button never takes the
    # focus (the press just blurs the comment) and the release comes later. One PUT (on the click), new rating + the
    # typed text – the comment's blur must not save the old rating.
    a.evaluate("""() => document.addEventListener('mousedown', (e) => {
        if (e.target.closest('.vote-btn-rate')) { e.preventDefault(); document.activeElement.blur(); }
    }, true)""")
    puts_a: list[dict] = []
    a.on("request", lambda req: puts_a.append(req.post_data_json)
         if req.method == "PUT" and req.url.endswith(f"/trips/{t1}/vote") else None)
    comment_t1 = row(a, t1).locator(".vote-comment").first
    comment_t1.click()
    comment_t1.fill("too far")
    box = rate(row(a, t1), "nogo").bounding_box()
    a.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    a.mouse.down()
    a.wait_for_timeout(400)  # a slow release
    a.mouse.up()
    expect(rate(row(a, t1), "nogo")).to_have_attribute("aria-pressed", "true")
    a.wait_for_timeout(300)
    assert puts_a == [{"rating": "nogo", "comment": "too far"}], puts_a
    trip_one = next(t for t in a.evaluate(get_round)["trips"] if t["trip_id"] == t1)
    assert [(v["rating"], v["comment"]) for v in trip_one["votes"] if v["username"] == "anna"] == [("nogo", "too far")]
    print("PASS rating changed while typing a comment (button never focused): one PUT with the new rating + text")

    # The ranking changes under the user: a click or tap must rate the trip/stay that was clicked, and a quick second
    # click on the same spot must not land on another trip that moved there (the reported "wrong option updated").
    round_id = a.evaluate(get_round)["id"]
    all_puts: list[tuple[str, dict]] = []
    a.on("request", lambda req: all_puts.append((req.url.split(f"/rounds/{round_id}/")[1], req.post_data_json))
         if req.method == "PUT" else None)
    put_js = """([path, rating, comment]) => fetch(path, {method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({rating, comment: comment || null})}).then((r) => r.status)"""

    def bob_votes(*votes) -> None:  # (trip_id, rating[, comment]) or ((trip_id, stay_id), rating)
        for target, rating, *comment in votes:
            path = (f"trips/{target[0]}/stays/{quote(target[1], safe='')}" if isinstance(target, tuple)
                    else f"trips/{target}")
            assert b.evaluate(put_js, [f"/api/rounds/{round_id}/{path}/vote", rating, *comment]) == 200

    def anna_votes() -> dict:
        out = {}
        for t in a.evaluate(get_round)["trips"]:
            out[t["trip_id"]] = [(v["rating"], v["comment"]) for v in t["votes"] if v["username"] == "anna"]
            for s in t["stays"]:
                out[(t["trip_id"], s["stay_id"])] = [v["rating"] for v in s["votes"] if v["username"] == "anna"]
        return out

    def others_same(before: dict, after: dict, key) -> None:
        assert {k: v for k, v in after.items() if k != key} == {k: v for k, v in before.items() if k != key}, after

    def puts_settled(pg, count: int, seen: list) -> None:
        for _ in range(50):
            if len(seen) >= count and pg.evaluate("vote.writing") == 0:
                break
            pg.wait_for_timeout(100)
        pg.wait_for_timeout(300)

    def sort_rows(pg) -> None:
        if pg.locator("#vote-sort").is_visible():
            pg.locator("#vote-sort").click()
        expect(pg.locator("#vote-sort")).to_be_hidden()

    def centre(locator) -> tuple[float, float]:
        box = locator.bounding_box()
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    # (1) quick second click: bob's votes leave trip one second (-2 pts vs -1); anna's 🙂 on trip one makes it #1.
    bob_votes((t1, "ok"), (t2, "nogo"))
    a.evaluate("refreshVote()")
    sort_rows(a)
    expect(trips_a.nth(1)).to_have_attribute("data-trip", t1)
    before = anna_votes()
    all_puts.clear()
    x, y = centre(rate(row(a, t1), "ok"))
    a.mouse.click(x, y)
    a.wait_for_timeout(250)  # the answer is in: trip one is #1 now
    a.mouse.click(x, y)
    puts_settled(a, 2, all_puts)
    assert [(path, body["rating"]) for path, body in all_puts] == [(f"trips/{t1}/vote", "ok")] * 2, all_puts
    others_same(before, anna_votes(), t1)
    expect(trips_a.nth(1)).to_have_attribute("data-trip", t1)  # kept in its place, with its new rank
    expect(trips_a.nth(1).locator(".vote-head").first).to_contain_text("#1 Vote trip one")
    expect(a.locator("#vote-sort")).to_be_visible()
    print("PASS a quick second click after a rank-changing vote rates the same trip; rows keep their places, "
          "↻ Sort by rank shown")

    # (2) slow press on a trip while others' votes change the ranking and a poll arrives: only the pressed trip.
    before = anna_votes()
    all_puts.clear()
    x, y = centre(rate(row(a, t1), "nogo"))
    a.mouse.move(x, y)
    a.mouse.down()
    bob_votes((t2, "love"))  # trip two back to #1
    a.evaluate("refreshVote()")
    a.wait_for_timeout(400)
    a.mouse.up()
    puts_settled(a, 1, all_puts)
    assert [(path, body["rating"]) for path, body in all_puts] == [(f"trips/{t1}/vote", "nogo")], all_puts
    after = anna_votes()
    assert after[t1] == [("nogo", "too far")], after[t1]
    others_same(before, after, t1)
    expect(trips_a.nth(1)).to_have_attribute("data-trip", t1)
    expect(rate(row(a, t1), "nogo")).to_have_attribute("aria-pressed", "true")
    sort_rows(a)
    ranked = [t["trip_id"] for t in a.evaluate(get_round)["trips"]]
    expect(trips_a.nth(0)).to_have_attribute("data-trip", ranked[0])
    print("PASS slow press on a trip while the ranking changes + a poll: one PUT, only the pressed trip rated")

    # (3) the same for a stay: anna presses 😍 on #2 Area house of trip one while bob's 🙂 puts it first.
    all_puts.clear()
    stays_t1 = row(a, t1).locator(".vote-stay")
    expect(stays_t1.nth(0)).to_have_attribute("data-stay", BSTAY["id"])
    stay_area = row(a, t1).locator(f'.vote-stay[data-stay="{STAY["id"]}"]')
    before = anna_votes()
    x, y = centre(rate(stay_area, "love"))
    a.mouse.move(x, y)
    a.mouse.down()
    bob_votes(((t1, STAY["id"]), "ok"))
    a.evaluate("refreshVote()")
    a.wait_for_timeout(400)
    a.mouse.up()
    puts_settled(a, 1, all_puts)
    assert [(path, body["rating"]) for path, body in all_puts] == [
        (f"trips/{t1}/stays/{quote(STAY['id'], safe='')}/vote", "love")], all_puts
    after = anna_votes()
    assert after[(t1, STAY["id"])] == ["love"], after
    others_same(before, after, (t1, STAY["id"]))
    expect(stays_t1.nth(1)).to_have_attribute("data-stay", STAY["id"])  # kept in place
    sort_rows(a)
    expect(stays_t1.nth(0)).to_have_attribute("data-stay", STAY["id"])
    print("PASS slow press on a stay while the stay ranking changes + a poll: one PUT, only that stay rated")

    # (4) a press on a rating button that slides off (no click) still saves the typed comment.
    all_puts.clear()
    comment_t1 = row(a, t1).locator(".vote-comment").first
    comment_t1.click()
    comment_t1.fill("way too far")
    x, y = centre(rate(row(a, t1), "ok"))
    a.mouse.move(x, y)
    a.mouse.down()
    a.mouse.move(2, 2, steps=4)
    a.mouse.up()
    puts_settled(a, 1, all_puts)
    assert all_puts == [(f"trips/{t1}/vote", {"rating": "nogo", "comment": "way too far"})], all_puts
    print("PASS a rating press that slides off without a click saves the typed comment with the current rating")

    # (5) a right-click in the list does not hold the redraws back.
    a.mouse.move(*centre(row(a, t2).locator(".vote-head").first))
    a.mouse.down(button="right")  # held: a context menu may swallow its release
    bob_votes((t2, "ok"))
    a.evaluate("refreshVote()")
    expect(row(a, t2).locator(".count-btn").first).to_have_text("😍1")
    assert not a.evaluate("vote.pressed")
    a.mouse.up(button="right")
    print("PASS a right-click in the list leaves redraws running")

    # (6) phone-style touch (own context, small screen): a tap whose answer is slow, others' votes + polls change
    # the ranking meanwhile and well afterwards, then a second tap on the same spot → still the same trip.
    ctx_d = browser.new_context(viewport={"width": 900, "height": 560}, has_touch=True)
    d = ctx_d.new_page()
    d.on("pageerror", lambda exc: errors.append(str(exc)))
    d.goto(BASE)
    d.wait_for_function("typeof openVote === 'function' && auth.ready")
    assert d.evaluate("""() => fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: 'anna', password: 'password1'})}).then((r) => r.status)""") == 200
    d.reload()
    d.wait_for_function("typeof openVote === 'function' && auth.ready && Boolean(auth.user)")
    d.click("#vote-btn")
    trips_d = d.locator("#vote-trips > .vote-trip")
    expect(trips_d).to_have_count(2)
    # No ⚠ No-go lines that could appear/vanish and shift the buttons: trip two 3 pts, trip one 2.
    a.evaluate(put_js, [f"/api/rounds/{round_id}/trips/{t1}/vote", "ok", None])
    a.evaluate(put_js, [f"/api/rounds/{round_id}/trips/{t2}/vote", "love", None])
    bob_votes((t1, "ok"), (t2, "ok"))
    d.evaluate("refreshVote()")
    sort_rows(d)
    expect(trips_d.nth(1)).to_have_attribute("data-trip", t1)
    d_puts: list[str] = []
    held_puts: list = []
    d.on("request", lambda req: d_puts.append(req.url.split(f"/rounds/{round_id}/")[1]) if req.method == "PUT" else None)
    d.route("**/vote", lambda route: held_puts.append(route))
    rate(row(d, t1), "love").scroll_into_view_if_needed()
    x, y = centre(rate(row(d, t1), "love"))
    d.touchscreen.tap(x, y)  # 😍 trip one: its answer waits
    for _ in range(30):
        if held_puts:
            break
        d.wait_for_timeout(100)
    assert len(held_puts) == 1, held_puts
    bob_votes((t1, "love"))
    d.evaluate("refreshVote()")
    d.wait_for_timeout(1700)
    held_puts.pop().continue_()
    for _ in range(30):
        if d.evaluate("vote.writing") == 0:
            break
        d.wait_for_timeout(100)
    d.unroute("**/vote")
    d.wait_for_timeout(1700)
    d.evaluate("refreshVote()")  # a poll well after the tap: trip one is #1 by now
    d.wait_for_timeout(500)
    expect(trips_d.nth(1)).to_have_attribute("data-trip", t1)
    expect(trips_d.nth(1).locator(".vote-head").first).to_contain_text("#1 Vote trip one")
    d.touchscreen.tap(x, y)  # the same spot again: 😍 trip one again
    puts_settled(d, 2, d_puts)
    assert d_puts == [f"trips/{t1}/vote"] * 2, d_puts
    after = anna_votes()
    assert after[t1][0][0] == "love" and after[t2][0][0] == "ok", after  # anna's Love moved off trip two
    print("PASS touch: a slow tap answer + polls changing the ranking, then a second tap on the same spot → same trip")

    # (7) keyboard: Enter on a rating button keeps the focus on that button across the redraw.
    d_puts.clear()
    nogo_t1 = rate(row(d, t1), "nogo")
    rate(row(d, t1), "ok").focus()
    d.keyboard.press("Tab")
    expect(nogo_t1).to_be_focused()
    d.keyboard.press("Enter")
    puts_settled(d, 1, d_puts)
    expect(nogo_t1).to_have_attribute("aria-pressed", "true")
    expect(nogo_t1).to_be_focused()
    d.keyboard.press("Shift+Tab")
    d.keyboard.press("Enter")
    puts_settled(d, 2, d_puts)
    expect(rate(row(d, t1), "ok")).to_have_attribute("aria-pressed", "true")
    assert d_puts == [f"trips/{t1}/vote"] * 2, d_puts
    print("PASS keyboard: Enter rates, the focus stays on the same button across redraws, Shift+Tab + Enter re-rates")

    # (8) a poll does not scroll the dialog back to the last clicked button.
    row(d, t1).locator("button", has_text="Stays (2)").click()  # a taller list
    row(d, t2).locator("button", has_text="Stays (1)").click()
    for count in d.locator("#vote-trips .vote-counts").all():  # every names box open: taller still
        count.locator(".count-btn").first.click()
    top_ok = rate(trips_d.nth(0), "ok")
    top_ok.click()  # the top row's button; a mouse click focuses it in Chrome
    puts_settled(d, 3, d_puts)
    scroller = """(() => { let el = document.getElementById('vote-trips');
        while (el && !(el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)))
            el = el.parentElement;
        return el; })()"""
    assert d.evaluate(f"{scroller} !== null"), "the vote dialog should scroll at 900×560"
    d.evaluate(f"{scroller}.scrollTop = 1e6")
    top = d.evaluate(f"{scroller}.scrollTop")
    assert top > 0, top
    scroller_top = d.evaluate(f"{scroller}.getBoundingClientRect().top")
    assert top_ok.bounding_box()["y"] + top_ok.bounding_box()["height"] < scroller_top, ("scrolled out of view", top_ok.bounding_box(), scroller_top,
        d.evaluate(f"[{scroller}.scrollHeight, {scroller}.clientHeight, {scroller}.scrollTop, {scroller}.id]"))
    bob_votes((t2, "nogo"))
    d.evaluate("refreshVote()")
    expect(row(d, t2).locator(".count-btn").nth(2)).to_have_text("🚫1")
    d.wait_for_timeout(300)
    assert d.evaluate(f"{scroller}.scrollTop") == top, (top, d.evaluate(f"{scroller}.scrollTop"))
    print("PASS a poll redraw keeps the dialog's scroll position")
    ctx_d.close()

    # Everyone's votes as the checks below expect them (trip two wins, stay: Area house).
    a.evaluate(put_js, [f"/api/rounds/{round_id}/trips/{t1}/vote", "nogo", "too far"])
    a.evaluate(put_js, [f"/api/rounds/{round_id}/trips/{t2}/vote", "love", "can't wait 🥾"])
    bob_votes((t1, "nogo", "<b>no</b>"), (t2, "ok"))
    a.evaluate("refreshVote()")
    sort_rows(a)
    expect(trips_a.nth(0)).to_have_attribute("data-trip", t2)

    close_btn = a.locator("#vote-tools button", has_text="Close now")
    close_btn.click()
    expect(a.locator("#vote-tools [data-arm=close]")).to_have_text("Confirm close")
    expect(a.locator("#vote-banner")).to_be_hidden()
    a.locator("#vote-tools [data-arm=close]").click()
    expect(a.locator("#vote-banner")).to_contain_text("🏆 Vote trip two wins – stay: Area house")
    expect(a.locator("#vote-tools")).to_be_hidden()
    rate_buttons = a.locator("#vote-trips .vote-btn-rate")
    assert rate_buttons.count() > 0
    for i in range(rate_buttons.count()):
        expect(rate_buttons.nth(i)).to_be_disabled()
    expect(a.locator("#vote-footer button", has_text="Start a new round")).to_be_visible()
    expect(a.locator("#vote-left")).to_be_hidden()
    b.evaluate("refreshVote()")
    expect(b.locator("#vote-banner")).to_contain_text("🏆 Vote trip two wins – stay: Area house")
    a.locator("#vote-banner button", has_text="Show on map").click()
    expect(dlg_a).to_be_hidden()
    expect(a.locator("#stay-note")).to_have_text("in trip “Vote trip two”")
    print("PASS Close now (two clicks) → 🏆 banner names the top trip + stay, buttons disabled, Show on map")

    # C (logged out): a failed poll's error goes away with the next good poll; Past rounds is reachable from the
    # closed round without logging in, while Start stays behind the login.
    ctx_c, c = new_page()
    c.click("#vote-btn")
    expect(c.locator("#vote-banner")).to_contain_text("🏆 Vote trip two wins")
    c.route("**/api/rounds/current", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail": "Server hiccup"}'))
    c.evaluate("refreshVote()")
    expect(c.locator("#vote-error")).to_be_visible()
    c.unroute("**/api/rounds/current")
    c.evaluate("refreshVote()")
    expect(c.locator("#vote-error")).to_be_hidden()
    print("PASS a failed poll's error is cleared by the next successful poll")
    c.locator("#vote-footer button", has_text="Past rounds").click()
    expect(c.locator("#auth-dialog")).to_be_hidden()
    expect(c.locator("#vote-history-view")).to_be_visible()
    past = c.locator("#vote-history button", has_text="November 🥾")
    expect(past).to_contain_text("🏆 Vote trip two")
    past.click()
    expect(c.locator("#vote-round-view")).to_be_visible()
    expect(c.locator("#vote-banner")).to_contain_text("🏆 Vote trip two wins")
    c.locator("#vote-footer button", has_text="← Back").click()
    c.locator("#vote-footer button", has_text="Past rounds").click()
    c.fill("#vote-start-title", "Sneaky")
    c.click("#vote-start-submit")
    expect(c.locator("#auth-dialog")).to_be_visible()
    c.click("#auth-close")
    print("PASS logged-out C reaches Past rounds from the closed round; Start still asks to log in")

    ctx_c.close()
    ctx_b.close()
    ctx_a.close()


def check_emojis(browser, errors: list[str]) -> None:
    """😀 Emojis: two sign-ups race for one emoji (409 → fresh picker), Change emoji (names elsewhere follow), and an
    account from before emojis (mocked /api/auth/me: emoji null) gets the skippable prompt once per page load."""
    def new_page():
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.goto(BASE)
        pg.wait_for_function("typeof openVote === 'function' && auth.ready")
        return ctx, pg

    def api_signup(ctx, name: str, emoji: str) -> None:  # sets that context's session cookie
        res = ctx.request.post(f"{BASE}/api/auth/signup", data={"username": name, "password": "password1", "emoji": emoji})
        assert res.status == 201, (res.status, res.text())

    # zed picks 🐸 – zoe signs up with it first; zed's Sign up answers 409 and gets a fresh picker.
    ctx_z, z = new_page()
    z.click("#auth-login-btn")
    z.click("#auth-tab-signup")
    z.fill("#auth-username", "zed")
    z.fill("#auth-password", "password1")
    z.fill("#auth-repeat", "password1")
    pick_emoji(z, "🐸")
    ctx_o = browser.new_context(viewport={"width": 1400, "height": 900})
    api_signup(ctx_o, "zoe", "🐸")
    z.click("#auth-submit")
    expect(z.locator("#auth-error")).to_have_text("That emoji is taken – pick another")
    frog = z.locator('#auth-emoji-grid .emoji-btn[data-emoji="🐸"]')
    expect(frog).to_have_attribute("aria-disabled", "true")
    expect(frog).to_have_attribute("title", "taken by zoe")
    expect(z.locator('#auth-emoji-grid .emoji-btn[aria-pressed="true"]')).to_have_count(0)
    expect(z.locator("#auth-submit")).to_be_disabled()
    expect(z.locator("#auth-dialog")).to_be_visible()
    pick_emoji(z, "🐙")
    z.click("#auth-submit")
    expect(z.locator("#auth-dialog")).to_be_hidden()
    expect(z.locator("#auth-area")).to_have_text("🐙 zed · Log out", use_inner_text=True)
    print("PASS two sign-ups with one emoji: the second gets 409 'That emoji is taken – pick another', a refreshed "
          "picker (🐸 taken by zoe) and picks again")

    # zoe (logged in through the API) sees zed's trip as "by 🐙 zed"; zed changes to 🦄; zoe's next open shows it.
    trip_id = z.evaluate("""() => fetch('/api/saved-trips', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: 'Emoji trip', hikes: [], stays: []})}).then((r) => r.json()).then((t) => t.id)""")
    o = ctx_o.new_page()
    o.on("pageerror", lambda exc: errors.append(str(exc)))
    o.goto(BASE)
    o.wait_for_function("typeof openVote === 'function' && auth.ready")
    expect(o.locator("#auth-area")).to_have_text("🐸 zoe · Log out", use_inner_text=True)
    o.click("#trips-btn")
    zed_row = o.locator("#trips-list li", has_text="Emoji trip")
    expect(zed_row).to_contain_text("by 🐙 zed")  # zoe's list stays open while zed changes his emoji

    z.click("#auth-name")
    dlg = z.locator("#emoji-dialog")
    expect(dlg).to_be_visible()
    expect(z.locator("#emoji-title")).to_have_text("Change emoji")
    expect(z.locator("#emoji-skip")).to_be_hidden()
    octopus = z.locator('#emoji-grid .emoji-btn[data-emoji="🐙"]')
    expect(octopus).to_have_attribute("aria-pressed", "true")
    expect(octopus).not_to_have_attribute("aria-disabled", "true")  # your own one is not "taken"
    expect(z.locator('#emoji-grid .emoji-btn[data-emoji="🐸"]')).to_have_attribute("title", "taken by zoe")
    z.locator('#emoji-grid .emoji-btn[data-emoji="🐸"]').click(force=True)
    expect(z.locator("#emoji-error")).to_have_text("🐸 is taken by zoe")
    expect(dlg).to_be_visible()
    z.locator('#emoji-grid .emoji-btn[data-emoji="🦄"]').click()
    expect(dlg).to_be_hidden()
    expect(z.locator("#auth-area")).to_have_text("🦄 zed · Log out", use_inner_text=True)
    assert z.evaluate("fetch('/api/auth/me').then((r) => r.json())") == {"username": "zed", "emoji": "🦄"}
    z.click("#auth-name")
    expect(z.locator('#emoji-grid .emoji-btn[data-emoji="🦄"]')).to_have_attribute("aria-pressed", "true")
    expect(z.locator('#emoji-grid .emoji-btn[data-emoji="🐙"]')).not_to_have_attribute("aria-disabled", "true")
    z.click("#emoji-close")
    expect(dlg).to_be_hidden()
    o.evaluate("auth.refreshUsers()")  # what a vote poll / log in does: the open list redraws, no reopening
    expect(zed_row).to_contain_text("by 🦄 zed")
    zed_row.click()
    expect(o.locator("#trip-created-by")).to_have_text("Created by 🦄 zed")
    # zed changes his own emoji over his open trip editor (as the after-login prompt would): "Created by" follows,
    # and zoe's open editor follows on her next users refresh.
    z.click("#trips-btn")
    z.locator("#trips-list li", has_text="Emoji trip").click()
    expect(z.locator("#trip-created-by")).to_have_text("Created by 🦄 zed")
    z.evaluate("openEmojiDialog(false)")
    z.locator('#emoji-grid .emoji-btn[data-emoji="🐙"]').click()
    expect(dlg).to_be_hidden()
    expect(z.locator("#trips-dialog")).to_be_visible()
    expect(z.locator("#trip-created-by")).to_have_text("Created by 🐙 zed")
    z.click("#trips-close")
    o.evaluate("auth.refreshUsers()")
    expect(o.locator("#trip-created-by")).to_have_text("Created by 🐙 zed")
    o.click("#trips-close")
    o.click("#trips-btn")  # reopening loads the users map before drawing the list
    expect(zed_row).to_contain_text("by 🐙 zed")
    o.click("#trips-close")
    status = z.evaluate("(id) => fetch(`/api/saved-trips/${encodeURIComponent(id)}`, {method: 'DELETE'})"
                        ".then((r) => r.status)", trip_id)
    assert status in (200, 204), status
    print("PASS Change emoji from the header name: own one highlighted, taken ones say who, 🦄 saved; "
          "open trips lists / editors (own and another browser's) redraw with the new emoji")

    # An account from before emojis: /api/auth/me says emoji null → the prompt opens once per page load, skippable;
    # it shows 👤 until it picks.
    ctx_p = browser.new_context(viewport={"width": 1400, "height": 900})
    api_signup(ctx_p, "olga", "🐢")
    ctx_p.route("**/api/auth/me", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"username": "olga", "emoji": None})))

    def users_without_olgas(route):
        res = route.fetch()
        users = [{**u, "emoji": None} if u["username"] == "olga" else u for u in res.json()]
        route.fulfill(response=res, body=json.dumps(users))
    ctx_p.route("**/api/users", users_without_olgas)
    p = ctx_p.new_page()
    p.on("pageerror", lambda exc: errors.append(str(exc)))
    p.goto(BASE)
    p.wait_for_function("typeof openVote === 'function' && auth.ready")
    prompt = p.locator("#emoji-dialog")
    expect(prompt).to_be_visible()
    expect(p.locator("#emoji-title")).to_have_text("Pick your emoji")
    expect(p.locator("#emoji-skip")).to_be_visible()
    expect(p.locator("#auth-area")).to_have_text("👤 olga · Log out", use_inner_text=True)
    expect(p.locator('#emoji-grid .emoji-btn[aria-pressed="true"]')).to_have_count(0)
    p.click("#emoji-skip")
    expect(prompt).to_be_hidden()
    p.wait_for_timeout(500)
    expect(prompt).to_be_hidden()  # once per page load
    assert p.evaluate("[auth.label('olga'), auth.label('zed'), auth.label('nobody')]") == ["👤 olga", "🐙 zed", "👤 nobody"]
    p.click("#trips-btn")  # names render without an emoji too
    expect(p.locator("#trips-dialog")).to_be_visible()
    p.click("#trips-close")
    p.reload()
    p.wait_for_function("typeof openVote === 'function' && auth.ready")
    expect(prompt).to_be_visible()  # a new page load asks again
    p.keyboard.press("Escape")
    expect(prompt).to_be_hidden()
    # The login FORM path: log out, ask for a gated action (it waits), log in as the emoji-less account through the
    # form → the action runs and the prompt opens; a different emoji-less account on the same page load is asked too.
    p.evaluate("window.ranPending = false")
    p.click("#auth-logout")
    expect(p.locator("#auth-login-btn")).to_be_visible()
    p.evaluate("requireLogin(() => { window.ranPending = true; })")
    expect(p.locator("#auth-dialog")).to_be_visible()
    p.fill("#auth-username", "olga")
    p.fill("#auth-password", "password1")
    p.click("#auth-submit")
    expect(prompt).to_be_visible()
    expect(p.locator("#emoji-title")).to_have_text("Pick your emoji")
    assert p.evaluate("window.ranPending") is True
    p.click("#emoji-skip")
    expect(prompt).to_be_hidden()
    p.click("#auth-logout")  # same page load: logging out and in again asks again (prompted resets per user)
    p.click("#auth-login-btn")
    p.fill("#auth-username", "olga")
    p.fill("#auth-password", "password1")
    p.click("#auth-submit")
    expect(prompt).to_be_visible()
    p.keyboard.press("Escape")
    expect(prompt).to_be_hidden()
    ctx_p.unroute("**/api/auth/me")
    ctx_p.unroute("**/api/users")
    p.click("#auth-name")
    expect(p.locator("#emoji-title")).to_have_text("Change emoji")
    p.locator('#emoji-grid .emoji-btn[data-emoji="🐼"]').click()
    expect(prompt).to_be_hidden()
    expect(p.locator("#auth-area")).to_have_text("🐼 olga · Log out", use_inner_text=True)
    print("PASS an account without an emoji gets the 'Pick your emoji' prompt once per page load (also after a login through "
          "the form, with the pending action still run), can skip it, shows 👤 until it picks one")
    ctx_p.close()
    ctx_o.close()
    ctx_z.close()


def check_mobile(browser, errors: list[str]) -> None:
    """📱 Phone shell at 390×844: tab bar, ☰ panel, List / Map tabs, full-screen Trips / Vote dialogs."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                              device_scale_factor=3)
    pg = ctx.new_page()
    pg.on("pageerror", lambda exc: errors.append(str(exc)))

    sent: list[tuple[str, dict]] = []
    held: list = []  # searches kept in flight while hold["on"]
    hold = {"on": False}

    def answer(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        body = route.request.post_data_json if route.request.post_data else {}
        sent.append((path, body))
        if hold["on"]:
            held.append((route, path, body))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path](body)))

    for path in ("/api/hikes", "/api/stays"):
        pg.route(f"**{path}", answer)
    pg.route("**/api/nature**", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"sights": [WATERFALL, RIVER], "warnings": []})))
    pg.goto(BASE)
    pg.wait_for_function("typeof mobile === 'object' && typeof openVote === 'function' && auth.ready")
    expect(pg.locator("#hike-filters-btn")).to_be_enabled(timeout=15000)

    def no_side_scroll(where: str) -> None:
        if not pg.evaluate("document.documentElement.scrollWidth <= innerWidth"):
            raise SystemExit(f"FAIL horizontal page scroll on the phone ({where})")
        wide = pg.evaluate("""() => [...document.querySelectorAll('dialog[open]')]
            .filter((d) => d.scrollWidth > d.clientWidth).map((d) => d.id)""")
        if wide:
            raise SystemExit(f"FAIL {wide} scroll sideways on the phone ({where})")

    def map_fills_element() -> None:
        size = pg.evaluate("""() => { const s = map.getSize(), el = map.getContainer();
            return [s.x, s.y, el.clientWidth, el.clientHeight]; }""")
        if size[0] != size[2] or size[1] != size[3] or size[0] < 300 or size[1] < 400:
            raise SystemExit(f"FAIL Leaflet size {size[:2]} vs map element {size[2:]}")

    def fills_viewport(selector: str) -> None:
        box = pg.locator(selector).bounding_box()
        if not box or (round(box["x"]), round(box["y"]), round(box["width"]), round(box["height"])) != (0, 0, 390, 844):
            raise SystemExit(f"FAIL {selector} is not full-screen: {box}")

    tabs = {name: pg.locator(f'#tabbar button[data-tab="{name}"]') for name in ("map", "list", "trips", "vote")}

    def active(name: str) -> None:
        for other, tab in tabs.items():
            expect(tab).to_have_attribute("aria-pressed", "true" if other == name else "false")

    expect(pg.locator("#tabbar")).to_be_visible()
    for name, label in (("map", "🗺️ Map"), ("list", "📋 List"), ("trips", "🧳 Trips"), ("vote", "🗳️ Vote")):
        expect(tabs[name]).to_contain_text(label)
    for hidden in ("#controls .brand", "#search-all", "#start", "#trips-btn", "#vote-btn", "#auth-login-btn", "#sidebar"):
        expect(pg.locator(hidden)).to_be_hidden()
    expect(pg.locator("#goto-input")).to_be_visible()
    expect(pg.locator("#menu-btn")).to_be_visible()
    active("map")
    if not pg.evaluate("document.body.classList.contains('phone-tab-map') && map.hasLayer(tiles.Streets)"):
        raise SystemExit("FAIL the phone starts on the Map tab with Streets")
    map_fills_element()
    no_side_scroll("map tab")
    print("PASS phone: tab bar, top bar 📍 + ☰, desktop controls hidden, Map tab")

    pg.locator("#menu-btn").tap()
    expect(pg.locator("#controls-more")).to_be_visible()
    fills_viewport("#controls-more")
    for shown in ("#start", "#end", "#hike-filters-btn", "#stay-filters-btn", "#draw-rect-btn", "#draw-circle-btn",
                  "#nature-btn", "#auth-login-btn", "#search", "#search-all", "#suggest-trips", "#menu-close"):
        expect(pg.locator(shown)).to_be_visible()
    for small in ("#menu-close", "#search-all", "#draw-rect-btn", "#hike-filters-btn", "#nature-btn"):
        box = pg.locator(small).bounding_box()
        if box["height"] < 40:
            raise SystemExit(f"FAIL {small} is {box['height']} px tall on the phone")
    no_side_scroll("panel")
    pg.locator("#menu-close").tap()
    expect(pg.locator("#controls-more")).to_be_hidden()
    print("PASS ☰ opens the full-screen panel (dates, filters, draw, nature, login, search); ✕ closes it")

    pg.locator("#menu-btn").tap()
    pg.locator("#search-all").tap()
    expect(pg.locator("#controls-more")).to_be_hidden()
    active("map")
    expect(pg.locator("#tab-badge-list")).to_have_text("1")
    print("PASS Search all from the panel closes it, stays on Map, 📋 List badge shows the hike count")

    # A slow search on the Map tab: "Searching…" on the map and a spinner on 📋 List until every answer is in.
    expect(pg.locator("#map-busy")).to_be_hidden()
    hold["on"] = True
    pg.locator("#menu-btn").tap()
    pg.locator("#search-all").tap()
    expect(pg.locator("#map-busy")).to_be_visible()
    expect(pg.locator("#map-busy")).to_contain_text("Searching…")
    expect(pg.locator("#tab-spin-list")).to_be_visible()
    busy_box = pg.locator("#map-busy").bounding_box()
    map_box = pg.locator("#map").bounding_box()
    if not (map_box["y"] < busy_box["y"] < map_box["y"] + 120):
        raise SystemExit(f"FAIL Searching… should sit at the top of the map: {busy_box} in {map_box}")
    if os.environ.get("UI_SHOTS"):
        pg.screenshot(path=os.path.join(os.environ["UI_SHOTS"], "searching.png"))
    for _ in range(50):
        if {p for _, p, _ in held} >= {"/api/hikes", "/api/stays"}:
            break
        pg.wait_for_timeout(100)
    hold["on"] = False
    first, *rest = held
    first[0].fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[first[1]](first[2])))
    if rest:  # one answer in, the other still loading
        expect(pg.locator("#map-busy")).to_be_visible()
    for route, path, body in rest:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path](body)))
    held.clear()
    expect(pg.locator("#map-busy")).to_be_hidden()
    expect(pg.locator("#tab-spin-list")).to_be_hidden()
    expect(pg.locator("#tab-badge-list")).to_have_text("1")
    print("PASS a slow search on the Map tab shows Searching… on the map + a 📋 List spinner until it is done")

    tabs["list"].tap()
    active("list")
    expect(pg.locator("#sidebar")).to_be_visible()
    expect(pg.locator("#ask")).to_be_visible()
    expect(pg.locator("#map")).to_be_hidden()
    fills = pg.evaluate("[document.querySelector('#sidebar').getBoundingClientRect().width, innerWidth]")
    if fills[0] != fills[1]:
        raise SystemExit(f"FAIL the List tab is {fills[0]} px wide, the screen {fills[1]}")
    no_side_scroll("list tab")
    pg.locator("#hike-list li").first.tap()
    active("map")
    expect(pg.locator("#map")).to_be_visible()
    expect(pg.locator("#sidebar")).to_be_hidden()
    map_fills_element()
    print("PASS List tab shows the sidebar + Ask; tapping a hike → Map tab, Leaflet sized to the map (no grey band)")

    tabs["list"].tap()
    tabs["trips"].tap()
    expect(pg.locator("#trips-dialog")).to_be_visible()
    fills_viewport("#trips-dialog")
    active("trips")
    pg.locator("#trips-close").tap()
    expect(pg.locator("#trips-dialog")).to_be_hidden()
    active("list")
    expect(pg.locator("#sidebar")).to_be_visible()
    tabs["map"].tap()
    tabs["trips"].tap()
    expect(pg.locator("#trips-dialog")).to_be_visible()
    pg.keyboard.press("Escape")
    expect(pg.locator("#trips-dialog")).to_be_hidden()
    active("map")
    map_fills_element()
    print("PASS Trips tab: full-screen dialog; ✕ / Escape return to the previous tab")

    tabs["vote"].tap()
    expect(pg.locator("#vote-dialog")).to_be_visible()
    fills_viewport("#vote-dialog")
    active("vote")
    pg.locator("#vote-close").tap()
    expect(pg.locator("#vote-dialog")).to_be_hidden()
    active("map")
    tabs["list"].tap()
    tabs["vote"].tap()
    expect(pg.locator("#vote-dialog")).to_be_visible()
    pg.evaluate("document.querySelector('#vote-dialog').close()")  # a programmatic close (e.g. after an action)
    active("list")
    print("PASS Vote tab: full-screen dialog; closing returns to the previous tab")

    tabs["map"].tap()
    pg.locator("#menu-btn").tap()
    pg.locator("#auth-login-btn").tap()
    expect(pg.locator("#auth-dialog")).to_be_visible()
    fills_viewport("#auth-dialog")
    pg.locator("#auth-close").tap()
    pg.locator("#hike-filters-btn").tap()
    expect(pg.locator("#hike-dialog")).to_be_visible()
    fills_viewport("#hike-dialog")
    pg.locator('#hike-dialog [data-act="cancel"]').tap()
    pg.locator("#menu-close").tap()
    no_side_scroll("after dialogs")
    print("PASS login + filters dialogs are full-screen on the phone; no horizontal scroll")

    # ---- the phone flow: touch drawing, popups, ＋ Trip sheet, dialogs' insides, 16 px inputs, 40 px targets ----
    shots = os.environ.get("UI_SHOTS")  # optional folder for phone screenshots

    def shot(name: str) -> None:
        if shots:
            pg.screenshot(path=os.path.join(shots, f"{name}.png"))

    def tall(selector: str, minimum: int = 40) -> None:
        loc = pg.locator(selector)
        if loc.count() == 0:
            raise SystemExit(f"FAIL nothing matches {selector}")
        for i in range(loc.count()):
            box = loc.nth(i).bounding_box()
            if box and box["height"] < minimum:
                raise SystemExit(f"FAIL {selector} #{i} is {box['height']} px tall on the phone")

    def inputs_16(scope: str) -> None:
        small = pg.evaluate("""(scope) => [...document.querySelectorAll(`${scope} input, ${scope} select, ${scope} textarea`)]
            .filter((el) => el.offsetParent && !['checkbox', 'radio', 'range'].includes(el.type))
            .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).map((el) => el.id || el.name || el.className)""",
                            scope)
        if small:
            raise SystemExit(f"FAIL inputs under 16 px in {scope}: {small}")

    def one_column(selector: str) -> None:
        lefts = pg.evaluate("(sel) => [...document.querySelectorAll(sel)].filter((el) => el.offsetParent)"
                            ".map((el) => Math.round(el.getBoundingClientRect().left))", selector)
        if not lefts or len(set(lefts)) != 1:
            raise SystemExit(f"FAIL {selector} is not one column: {lefts}")

    # ☰ panel: the rest is inert while it is open; ✕ gives the focus back to ☰.
    pg.locator("#menu-btn").tap()
    if not pg.evaluate("document.querySelector('main').inert && document.getElementById('tabbar').inert"):
        raise SystemExit("FAIL the page behind the ☰ panel is still reachable")
    pg.locator("#menu-close").tap()
    if pg.evaluate("[document.activeElement.id, document.querySelector('main').inert]") != ["menu-btn", False]:
        raise SystemExit("FAIL closing the ☰ panel should focus ☰ and release the page")
    tall("#menu-btn")
    tall("#tabbar button")
    print("PASS ☰ panel: background inert while open, focus back on ☰ when closed")

    # ☰ panel is a modal dialog while open (and only then).
    panel = pg.locator("#controls-more")
    expect(panel).not_to_have_attribute("role", "dialog")
    pg.locator("#menu-btn").tap()
    expect(panel).to_have_attribute("role", "dialog")
    expect(panel).to_have_attribute("aria-modal", "true")
    expect(panel).to_have_attribute("aria-label", "Search controls")
    pg.locator("#menu-close").tap()
    for name in ("role", "aria-modal", "aria-label"):
        if panel.get_attribute(name) is not None:
            raise SystemExit(f"FAIL the closed ☰ panel keeps {name}")
    print("PASS ☰ panel: role=dialog, aria-modal, aria-label while open; removed when closed")

    # Back gesture: closes the ☰ panel / returns from List to Map, never leaves the site; UI closes leave no entries.
    no_phone_entry = "!Array.isArray(history.state?.phone)"
    pg.wait_for_function(no_phone_entry)
    pg.locator("#menu-btn").tap()
    open_length = pg.evaluate("history.length")
    pg.locator("#menu-close").tap()
    pg.wait_for_function(no_phone_entry)
    pg.locator("#menu-btn").tap()
    pg.keyboard.press("Escape")
    expect(pg.locator("#controls-more")).to_be_hidden()
    pg.wait_for_function(no_phone_entry)
    pg.locator("#menu-btn").tap()
    if pg.evaluate("history.length") != open_length:  # each ✕ / Escape stepped back over its entry
        raise SystemExit(f"FAIL ☰ ✕ / Escape should not pile up history entries: {pg.evaluate('history.length')} "
                         f"vs {open_length}")
    pg.go_back()
    expect(pg.locator("#controls-more")).to_be_hidden()
    if pg.url.rstrip("/") != BASE.rstrip("/") or pg.evaluate("document.body.classList.contains('menu-open')"):
        raise SystemExit(f"FAIL back should close the ☰ panel and stay on the site: {pg.url}")
    if pg.evaluate("document.querySelector('main').inert"):
        raise SystemExit("FAIL back closed the panel but left the page inert")
    tabs["list"].tap()
    active("list")
    pg.go_back()
    active("map")
    expect(pg.locator("#map")).to_be_visible()
    map_fills_element()
    tabs["list"].tap()
    pg.locator("#menu-btn").tap()  # List + panel: back closes the panel first, then returns to Map
    pg.go_back()
    expect(pg.locator("#controls-more")).to_be_hidden()
    active("list")
    pg.go_back()
    active("map")
    if pg.url.rstrip("/") != BASE.rstrip("/"):
        raise SystemExit(f"FAIL back left the site: {pg.url}")
    tabs["list"].tap()
    tabs["map"].tap()  # back to Map from the UI: the List entry is dropped, not piled up
    pg.wait_for_function(no_phone_entry)
    print("PASS back gesture: closes ☰, List → Map, stays on the site; UI closes leave no history entries")

    # Leaflet's controls are thumb-sized; its draw buttons are hidden (the panel's draw buttons show the hint).
    tall(".leaflet-control-zoom a")
    tall(".leaflet-control-layers-toggle")
    expect(pg.locator(".leaflet-draw-draw-rectangle")).to_be_hidden()
    expect(pg.locator(".leaflet-draw-draw-circle")).to_be_hidden()
    expect(pg.locator(".leaflet-draw-edit-edit")).to_be_visible()
    tall(".leaflet-draw-edit-edit")
    tall(".leaflet-draw-edit-remove")
    print("PASS Leaflet zoom / layers / edit buttons ≥ 40 px; Leaflet.draw's draw buttons hidden on the phone")

    # Login dialog: sign up with 16 px fields.
    pg.locator("#menu-btn").tap()
    pg.locator("#auth-login-btn").tap()
    expect(pg.locator("#auth-dialog")).to_be_visible()
    pg.locator("#auth-tab-signup").tap()
    inputs_16("#auth-dialog")
    tall("#auth-close")
    tall("#auth-submit")
    pg.fill("#auth-username", "mia")
    pg.fill("#auth-password", "password1")
    pg.fill("#auth-repeat", "password1")
    shot("login-dialog")
    expect(pg.locator("#auth-emoji-grid .emoji-btn")).to_have_count(EMOJI_COUNT)
    tall("#auth-emoji-grid .emoji-btn")
    narrow = pg.evaluate("""() => [...document.querySelectorAll('#auth-emoji-grid .emoji-btn')]
        .filter((b) => b.getBoundingClientRect().width < 40).length""")
    if narrow:
        raise SystemExit(f"FAIL {narrow} picker buttons narrower than 40 px on the phone")
    no_side_scroll("sign-up picker")
    pg.locator('#auth-emoji-grid .emoji-btn[data-emoji="🐔"]').scroll_into_view_if_needed()
    pick_emoji(pg, "🐔", tap=True)
    shot("signup-picker-phone")
    pg.locator("#auth-submit").tap()
    expect(pg.locator("#auth-dialog")).to_be_hidden()
    expect(pg.locator("#auth-name")).to_have_text("🐔 mia")
    no_side_scroll("login")
    print("PASS login dialog on the phone: 16 px fields, 40 px buttons, picker ≥ 40 px wraps without side scroll, "
          "sign up works")

    # Change emoji on the phone: the ☰ panel (still open) → tap your name → full-screen picker.
    expect(pg.locator("#controls-more")).to_be_visible()
    tall("#auth-name")
    pg.locator("#auth-name").tap()
    expect(pg.locator("#emoji-dialog")).to_be_visible()
    fills_viewport("#emoji-dialog")
    expect(pg.locator('#emoji-grid .emoji-btn[data-emoji="🐔"]')).to_have_attribute("aria-pressed", "true")
    tall("#emoji-grid .emoji-btn")
    tall("#emoji-close")
    no_side_scroll("change emoji")
    shot("change-emoji-phone")
    sloth = pg.locator('#emoji-grid .emoji-btn[data-emoji="🦥"]')
    sloth.scroll_into_view_if_needed()
    sloth.tap()
    expect(pg.locator("#emoji-dialog")).to_be_hidden()
    expect(pg.locator("#auth-name")).to_have_text("🦥 mia")
    expect(pg.locator("#controls-more")).to_be_visible()  # back in the panel it was opened from
    print("PASS Change emoji on the phone: full-screen picker from the ☰ panel's name, 40 px buttons, no side scroll")

    # Draw by touch (a one-finger drag, no mouse events): circle first, cancel by tapping the hint, then a rectangle.
    cdp = ctx.new_cdp_session(pg)
    pg.evaluate("""() => { window.mouseSeen = 0;
        for (const t of ['mousedown', 'mouseup']) document.addEventListener(t, () => { window.mouseSeen += 1; }, true); }""")

    def touch_drag(points: list[tuple[int, int]]) -> None:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": points[0][0], "y": points[0][1]}]})
        for x, y in points[1:]:
            pg.wait_for_timeout(40)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    pg.locator("#draw-circle-btn").tap()
    expect(pg.locator("#controls-more")).to_be_hidden()
    expect(pg.locator("#draw-hint")).to_contain_text("Drag a finger across the map to draw a circle")
    pg.locator("#draw-hint").tap()
    expect(pg.locator("#draw-hint")).to_be_hidden()
    expect(pg.locator("#draw-circle-btn")).not_to_have_class(re.compile(r"\bactive\b"))
    pg.locator("#menu-btn").tap()
    pg.locator("#draw-circle-btn").tap()
    pg.evaluate("window.mouseSeen = 0")
    touch_drag([(80, 180), (110, 200), (140, 220), (170, 250)])  # away from the pins at the centre
    expect(pg.locator("#draw-hint")).to_be_hidden()
    area = pg.evaluate("drawnArea()")
    if not area or area["shape"] != "circle" or area["radius_m"] < 200 or pg.evaluate("window.mouseSeen") != 0:
        raise SystemExit(f"FAIL a touch drag (no mouse events) should draw a circle: {area}")
    pg.locator("#menu-btn").tap()
    pg.locator("#draw-rect-btn").tap()
    expect(pg.locator("#draw-hint")).to_contain_text("Drag a finger across the map to draw a rectangle · tap here to cancel")
    shot("draw-hint")
    pg.evaluate("window.mouseSeen = 0")  # the taps above bring compatibility mouse events; the drag must not
    touch_drag([(60, 540), (140, 600), (220, 650), (300, 700)])  # clear of the circle and the pins
    expect(pg.locator("#draw-hint")).to_be_hidden()
    area = pg.evaluate("drawnArea()")
    if not area or area["shape"] != "rectangle" or not (area["south"] < area["north"] and area["west"] < area["east"]):
        raise SystemExit(f"FAIL a touch drag should draw a rectangle: {area}")
    if pg.evaluate("mode()") != "drawn" or pg.evaluate("window.mouseSeen") != 0:
        raise SystemExit("FAIL touch drawing: mode should be drawn, with no mouse events")
    before = len(sent)
    pg.locator("#menu-btn").tap()
    pg.locator("#search-all").tap()
    for _ in range(50):
        hikes = [b for p, b in sent[before:] if p == "/api/hikes"]
        if hikes:
            break
        pg.wait_for_timeout(100)
    else:
        raise SystemExit("FAIL no hikes search after drawing")
    if hikes[0].get("mode") != "drawn" or hikes[0]["area"] != area:
        raise SystemExit(f"FAIL the drawn-area search should send the touch-drawn rectangle: {hikes[0]}")
    expect(pg.locator(".leaflet-marker-icon.pin.airbnb")).to_have_count(1)
    no_side_scroll("drawn search")
    print("PASS touch drawing (no mouse): circle, hint tap cancels, rectangle → drawn-area search with stays")

    # A hike popup fits the screen; its ＋ Trip opens the bottom sheet above the tab bar; ➕ New trip saves.
    pg.locator(".leaflet-marker-icon.pin.hike").first.tap()
    popup = pg.locator(".leaflet-popup")
    expect(popup).to_be_visible()
    for _ in range(30):  # Leaflet pans the map (animated) to bring the popup into view
        box = popup.bounding_box()
        if box["x"] >= 0 and box["x"] + box["width"] <= 390:
            break
        pg.wait_for_timeout(100)
    if box["width"] > 390 * 0.85 + 1 or box["x"] < 0 or box["x"] + box["width"] > 390:
        raise SystemExit(f"FAIL the popup does not fit the phone: {box}")
    tall(".leaflet-popup .add-trip")
    tall(".leaflet-popup a.leaflet-popup-close-button")
    shot("popup")
    no_side_scroll("popup")
    pg.locator(".leaflet-popup .add-trip").tap()
    sheet = pg.locator("#trip-menu")
    expect(sheet).to_be_visible()
    sheet_box = sheet.bounding_box()
    bar_top = pg.locator("#tabbar").bounding_box()["y"]
    if round(sheet_box["x"]) != 0 or round(sheet_box["width"]) != 390 or abs(sheet_box["y"] + sheet_box["height"] - bar_top) > 1:
        raise SystemExit(f"FAIL ＋ Trip should be a bottom sheet above the tab bar: {sheet_box}, bar at {bar_top}")
    tall("#trip-menu button")
    shot("trip-sheet")
    trips_before = pg.evaluate("fetch('/api/saved-trips').then((r) => r.json()).then((l) => l.length)")
    sheet.locator("button", has_text="➕ New trip").tap()
    expect(sheet).to_be_hidden()
    expect(pg.locator("#trips-count")).to_have_text(f"({trips_before + 1})")
    print("PASS popup ≤ 85vw with 40 px buttons; ＋ Trip bottom sheet above the tab bar; ➕ New trip saved")

    # Filters dialog: full screen, one column, 16 px fields, Apply works.
    pg.locator("#menu-btn").tap()
    pg.locator("#hike-filters-btn").tap()
    expect(pg.locator("#hike-dialog")).to_be_visible()
    fills_viewport("#hike-dialog")
    one_column("#hike-dialog .field")
    inputs_16("#hike-dialog")
    tall('#hike-dialog [data-act="apply"]')
    shot("filters-dialog")
    no_side_scroll("filters dialog")
    pg.evaluate("""() => { const s = document.querySelector('#hike-dialog [name=shared_drive]');
        s.value = '3'; s.dispatchEvent(new Event('input', {bubbles: true})); }""")
    pg.locator('#hike-dialog [data-act="apply"]').tap()
    expect(pg.locator("#hike-dialog")).to_be_hidden()
    expect(pg.locator("#drive")).to_have_value("3")
    pg.locator("#menu-close").tap()
    print("PASS filters dialog: full screen, one column, 16 px fields, Apply stores the change")

    # Trips tab → editor: one column, 16 px, rename + Save.
    tabs["trips"].tap()
    expect(pg.locator("#trips-dialog")).to_be_visible()
    tall("#trips-close")
    pg.locator("#trips-list li", has_text=HIKE["name"]).first.tap()
    expect(pg.locator("#trip-editor")).to_be_visible()
    inputs_16("#trip-editor")
    new_name = "Πήλιο – a long weekend with a very long trip name that must wrap nicely"
    pg.fill("#trip-name", new_name)
    shot("trip-editor")
    no_side_scroll("trip editor")
    pg.locator("#trip-save").tap()
    for _ in range(50):
        names = pg.evaluate("fetch('/api/saved-trips').then((r) => r.json()).then((l) => l.map((t) => t.name))")
        if new_name in names:
            break
        pg.wait_for_timeout(100)
    else:
        raise SystemExit(f"FAIL the renamed trip was not saved: {names}")
    pg.locator("#trips-close").tap()
    expect(pg.locator("#trips-dialog")).to_be_hidden()
    active("map")
    print("PASS Trips tab → editor: 16 px fields, rename + Save changes")

    # Vote tab → start a round and rate: the three rating buttons on one row, ≥ 40 px.
    tabs["vote"].tap()
    expect(pg.locator("#vote-dialog")).to_be_visible()
    tall("#vote-close")
    pg.locator("#vote-footer button", has_text="Start a new round").tap()
    expect(pg.locator("#vote-start")).to_be_visible()
    inputs_16("#vote-start")
    pg.fill("#vote-start-title", "Phone round 📱")
    pg.locator("#vote-start-submit").tap()
    expect(pg.locator("#vote-round-view")).to_be_visible()
    mine = pg.locator("#vote-trips .vote-trip", has_text=new_name)
    buttons = mine.locator(".vote-buttons").first.locator(".vote-btn-rate")
    expect(buttons).to_have_count(3)
    tops = {round(buttons.nth(i).bounding_box()["y"]) for i in range(3)}
    if len(tops) != 1:
        raise SystemExit(f"FAIL the rating buttons are not on one row: {tops}")
    tall("#vote-trips .vote-btn-rate")
    buttons.nth(0).tap()
    expect(buttons.nth(0)).to_have_attribute("aria-pressed", "true")
    mine.scroll_into_view_if_needed()
    shot("vote-dialog")
    no_side_scroll("vote dialog")
    pg.locator("#vote-close").tap()
    active("map")
    print("PASS Vote tab: start a round, rating buttons on one row ≥ 40 px, rate a trip")

    # 🌿 Nature: the chips are one horizontally scrollable row at the top of the map.
    pg.locator("#menu-btn").tap()
    pg.locator("#nature-btn").tap()
    expect(pg.locator("#controls-more")).to_be_hidden()
    chips = pg.locator("#nature-chips")
    expect(chips).to_be_visible()
    tall(".nature-chip")
    tops = pg.evaluate("[...document.querySelectorAll('.nature-chip')].map((c) => Math.round(c.getBoundingClientRect().top))")
    if len(set(tops)) != 1:
        raise SystemExit(f"FAIL the 🌿 chips are not one row: {tops}")
    scrolled = pg.evaluate("""() => { const c = document.getElementById('nature-chips');
        c.scrollLeft = 60; return [c.scrollWidth > c.clientWidth, c.scrollLeft > 0]; }""")
    if scrolled != [True, True]:
        raise SystemExit(f"FAIL the 🌿 chips row should scroll sideways: {scrolled}")
    pg.evaluate("document.getElementById('nature-chips').scrollLeft = 0")
    shot("nature-chips")
    no_side_scroll("nature chips")
    pg.locator("#menu-btn").tap()
    tall("#search")
    tall("#search-all")
    tall("#suggest-trips")
    print("PASS 🌿 chips: one scrollable row of 40 px chips; ☰, tabs, search buttons, ✕ and rating buttons ≥ 40 px")

    # Crossing the breakpoint resets the phone state: panel closed, Map tab.
    pg.set_viewport_size({"width": 1000, "height": 844})
    try:  # the media query's change event comes a frame later
        pg.wait_for_function("""() => !document.body.classList.contains('menu-open')
            && document.getElementById('menu-btn').getAttribute('aria-expanded') === 'false'
            && document.body.classList.contains('phone-tab-map') && !document.querySelector('main').inert""", timeout=3000)
    except Exception:
        raise SystemExit("FAIL crossing the breakpoint should close the panel and return to the Map tab")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_function("mobile.isPhone()")
    tabs["list"].tap()
    active("list")
    pg.set_viewport_size({"width": 1000, "height": 844})
    pg.wait_for_function("!mobile.isPhone() && document.body.classList.contains('phone-tab-map')")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_function("mobile.isPhone()")
    active("map")
    expect(pg.locator("#controls-more")).to_be_hidden()
    map_fills_element()
    print("PASS crossing the 760 px breakpoint closes the panel and returns to the Map tab")

    # Crossing it mid-draw cancels the draw: one from the panel (phone → desktop) and one from Leaflet.draw's
    # toolbar (desktop → phone, where that toolbar's draw buttons are hidden).
    pg.locator("#menu-btn").tap()
    pg.locator("#draw-rect-btn").tap()
    expect(pg.locator("#draw-hint")).to_be_visible()
    pg.set_viewport_size({"width": 1000, "height": 844})
    pg.wait_for_function("!mobile.isPhone()")
    expect(pg.locator("#draw-hint")).to_be_hidden()
    if pg.evaluate("activeDraw !== null || document.querySelector('#draw-rect-btn.active') !== null"):
        raise SystemExit("FAIL crossing the breakpoint should cancel a draw started from the panel")
    pg.locator(".leaflet-draw-draw-circle").click()
    if not pg.evaluate("drawControl._toolbars.draw.enabled()"):
        raise SystemExit("FAIL Leaflet.draw's circle button should start drawing on the desktop layout")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_function("mobile.isPhone()")
    pg.wait_for_function("!drawControl._toolbars.draw.enabled()", timeout=3000)
    print("PASS crossing the breakpoint cancels an unfinished draw (panel or Leaflet.draw toolbar)")
    ctx.close()


def check_mobile_landscape(browser, errors: list[str]) -> None:
    """📱 A phone in landscape (844×390, touch) gets the phone layout too, not the desktop one."""
    ctx = browser.new_context(viewport={"width": 844, "height": 390}, has_touch=True, is_mobile=True,
                              device_scale_factor=3)
    pg = ctx.new_page()
    pg.on("pageerror", lambda exc: errors.append(str(exc)))

    def answer(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        body = route.request.post_data_json if route.request.post_data else {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path](body)))

    for path in ("/api/hikes", "/api/stays"):
        pg.route(f"**{path}", answer)
    pg.goto(BASE)
    pg.wait_for_function("typeof mobile === 'object' && auth.ready")
    expect(pg.locator("#hike-filters-btn")).to_be_enabled(timeout=15000)
    if not pg.evaluate("mobile.isPhone()"):
        raise SystemExit("FAIL a phone in landscape (844×390, touch) should get the phone layout")
    expect(pg.locator("#tabbar")).to_be_visible()
    expect(pg.locator("#menu-btn")).to_be_visible()
    for hidden in ("#controls .brand", "#search-all", "#sidebar", "#trips-btn"):
        expect(pg.locator(hidden)).to_be_hidden()
    if not pg.evaluate("document.documentElement.scrollWidth <= innerWidth"):
        raise SystemExit("FAIL horizontal page scroll on the phone in landscape")
    size = pg.evaluate("""() => { const s = map.getSize(), el = map.getContainer();
        return [s.x, s.y, el.clientWidth, el.clientHeight]; }""")
    if size[0] != size[2] or size[1] != size[3] or size[0] != 844 or size[1] < 200:
        raise SystemExit(f"FAIL the landscape map should fill the width with a usable height: {size}")

    def inputs_16() -> None:
        small = pg.evaluate("""() => [...document.querySelectorAll('input, select, textarea')]
            .filter((el) => el.offsetParent && !['checkbox', 'radio', 'range'].includes(el.type))
            .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).map((el) => el.id)""")
        if small:
            raise SystemExit(f"FAIL inputs under 16 px on the landscape phone: {small}")

    inputs_16()
    pg.locator("#menu-btn").tap()
    expect(pg.locator("#controls-more")).to_be_visible()
    box = pg.locator("#controls-more").bounding_box()
    if (round(box["width"]), round(box["height"])) != (844, 390):
        raise SystemExit(f"FAIL the landscape ☰ panel is not full-screen: {box}")
    inputs_16()
    search = pg.locator("#search-all")
    expect(search).to_be_visible()
    search_box = search.bounding_box()
    if search_box["y"] + search_box["height"] > 390:
        raise SystemExit(f"FAIL the panel's search buttons should stay on screen in landscape: {search_box}")
    if not pg.evaluate("document.documentElement.scrollWidth <= innerWidth"):
        raise SystemExit("FAIL horizontal page scroll in the landscape ☰ panel")
    search.tap()
    expect(pg.locator("#controls-more")).to_be_hidden()
    expect(pg.locator("#tab-badge-list")).to_have_text("1")
    pg.locator('#tabbar button[data-tab="list"]').tap()
    expect(pg.locator("#sidebar")).to_be_visible()
    expect(pg.locator("#map")).to_be_hidden()
    if os.environ.get("UI_SHOTS"):
        pg.screenshot(path=os.path.join(os.environ["UI_SHOTS"], "landscape-list.png"))
    print("PASS landscape phone 844×390: phone layout (tab bar, full-screen ☰ panel, 16 px inputs, no side scroll, "
          "map fills the width)")
    ctx.close()


def check_overview(browser, errors: list[str]) -> None:
    """📊 Overview grid: anna and bob (accounts from the voting scenario) vote on 2 trips (+ a stay) with comments via
    the real API; the grid, the comment popover, progress, leader, tab persistence, trip name → Vote tab, a phone
    with more participants (scrolls inside its box, name column pinned), and the closed round's final grid."""
    from datetime import datetime, timedelta, timezone
    from urllib.parse import quote

    def logged_in(name: str, **kw):
        ctx = browser.new_context(**kw)
        res = ctx.request.post(f"{BASE}/api/auth/login", data={"username": name, "password": "password1"})
        return ctx if res.status == 200 else (ctx.close(), None)[1]

    def page_of(ctx):
        pg = ctx.new_page()
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.goto(BASE)
        pg.wait_for_function("typeof openVote === 'function' && typeof renderOverview === 'function' && auth.ready")
        return pg

    def call(ctx, method: str, path: str, body=None):
        res = ctx.request.fetch(f"{BASE}{path}", method=method, data=body)
        assert res.ok, (method, path, res.status, res.text())
        return res.json() if res.text() else None

    ctx_a = logged_in("anna", viewport={"width": 1400, "height": 900})
    ctx_b = logged_in("bob")
    assert ctx_a and ctx_b, "anna / bob from the voting scenario should log in"
    current = call(ctx_a, "GET", "/api/rounds/current")
    assert not current or current["status"] != "open", "the overview scenario needs no open round"
    long_name = "Overview trip two – a rather long name that has to wrap"
    t1 = call(ctx_a, "POST", "/api/saved-trips", {"name": "Overview trip one", "hikes": [HIKE], "stays": [STAY, BSTAY]})["id"]
    t2 = call(ctx_a, "POST", "/api/saved-trips", {"name": long_name, "hikes": [HIKE2], "stays": [STAY]})["id"]
    closes = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    rid = call(ctx_a, "POST", "/api/rounds", {"title": "Overview 📊", "closes_at": closes, "trip_ids": [t1, t2]})["id"]

    def cast(ctx, trip: str, rating: str, comment: str | None = None, stay: str | None = None) -> None:
        path = f"/api/rounds/{rid}/trips/{trip}" + (f"/stays/{quote(stay, safe='')}" if stay else "") + "/vote"
        call(ctx, "PUT", path, {"rating": rating, "comment": comment})

    cast(ctx_a, t1, "love")
    cast(ctx_a, t2, "ok", "<i>long drive</i>")
    cast(ctx_a, t1, "love", stay=STAY["id"])
    cast(ctx_b, t1, "nogo", "too far")  # bob leaves trip two unrated: 1/2 people done

    a = page_of(ctx_a)
    a.click("#vote-btn")
    expect(a.locator("#vote-pane")).to_be_visible()
    expect(a.locator("#vote-tab-vote")).to_have_attribute("aria-selected", "true")
    a.click("#vote-tab-overview")
    expect(a.locator("#vote-overview")).to_be_visible()
    expect(a.locator("#vote-pane")).to_be_hidden()
    head = a.locator("#vote-ov-head")
    expect(head).to_contain_text("Overview 📊")
    expect(head).to_contain_text("closes in 2 d 23 h")
    expect(head).to_contain_text(f"Leader: {long_name} (+1)")
    expect(head).to_contain_text("1/2 people done")
    people = a.locator("#vote-ov-scroll th.ov-person")
    expect(people).to_have_count(2)
    expect(people.nth(0)).to_have_text("🍕anna")  # done first; the viewer's own column highlighted
    expect(people.nth(0)).to_have_class(re.compile(r"\bme\b"))
    expect(people.nth(1)).to_have_text("🦖bob")
    expect(people.nth(1)).to_have_attribute("title", "bob")
    rows = a.locator("#vote-ov-scroll tr.ov-trip-row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_have_attribute("data-trip", t2)
    expect(rows.nth(0).locator("th.ov-name")).to_have_text(f"#1 {long_name}")
    expect(rows.nth(1).locator("th.ov-name")).to_have_text("#2 Overview trip one")
    r1, r2 = rows.nth(1), rows.nth(0)
    expect(r1.locator('td[data-user="anna"]')).to_have_text("😍")
    expect(r1.locator('td[data-user="bob"]')).to_have_text("🚫")
    expect(r1.locator('td[data-user="bob"]')).to_have_class(re.compile(r"\bnogo\b"))
    expect(r1.locator("td.ov-score")).to_have_text("-1 ⚠")
    expect(r2.locator('td[data-user="anna"]')).to_have_text("🙂")
    expect(r2.locator('td[data-user="bob"]')).to_have_text("·")
    expect(r2.locator("td.ov-score")).to_have_text("1")
    stay_row = lambda trip, stay: a.locator(f'#vote-ov-scroll tr.ov-stay-row[data-trip="{trip}"][data-stay="{stay}"]')
    expect(stay_row(t1, STAY["id"])).to_have_text(re.compile(r"^🏡 #1 Area house😍·2$"))
    expect(stay_row(t1, BSTAY["id"])).to_have_count(0)
    more = a.locator("#vote-ov-scroll .ov-more")
    expect(more).to_have_text("▸ 1 more stay")
    more.click()
    expect(stay_row(t1, BSTAY["id"])).to_have_count(1)
    expect(a.locator("#vote-ov-scroll .ov-more")).to_have_text("▾ hide 1 stay")
    a.evaluate("refreshVote()")
    expect(stay_row(t1, BSTAY["id"])).to_have_count(1)  # expanded state kept across refreshes
    print("PASS overview: rows in rank order with scores + ⚠, emoji columns (done first, own highlighted), "
          "symbols / · per cell, leader, 1/2 people done, ▸ more stays kept across a refresh")

    pop = a.locator("#vote-ov-pop")
    r1.locator('td[data-user="bob"] .ov-cell-btn').click()
    expect(pop).to_be_visible()
    expect(pop).to_have_text("🦖 bob: No-go – “too far”")
    a.keyboard.press("Escape")
    expect(pop).to_be_hidden()
    expect(a.locator("#vote-dialog")).to_be_visible()  # Escape closed only the popover
    r2.locator('td[data-user="anna"] .ov-cell-btn').click()
    expect(pop).to_have_text("🍕 anna: OK – “<i>long drive</i>”")
    expect(a.locator("#vote-dialog i")).to_have_count(0)
    if os.environ.get("UI_SHOTS"):
        a.locator("#vote-dialog").screenshot(path=os.path.join(os.environ["UI_SHOTS"], "overview-desktop.png"))
    head.click()
    expect(pop).to_be_hidden()
    print("PASS overview comment popover: text only, Escape / click outside close it, the dialog stays open")

    # Trip name → the Vote tab at that trip; the tab choice persists across reopen (and reload).
    r1.locator(".ov-trip").click()
    expect(a.locator("#vote-pane")).to_be_visible()
    expect(a.locator("#vote-tab-vote")).to_have_attribute("aria-selected", "true")
    expect(a.locator(f'#vote-trips .vote-trip[data-trip="{t1}"]')).to_have_class(re.compile(r"\bfocus\b"))
    a.click("#vote-tab-overview")
    a.click("#vote-close")
    a.reload()
    a.wait_for_function("typeof openVote === 'function' && auth.ready")
    assert a.evaluate("localStorage.getItem('hick-toors:vote-tab')") == "overview"
    a.click("#vote-btn")
    expect(a.locator("#vote-overview")).to_be_visible()
    expect(a.locator("#vote-tab-overview")).to_have_attribute("aria-selected", "true")
    # Live: bob rates trip two → 2/2 after the next poll.
    cast(ctx_b, t2, "ok")
    a.evaluate("refreshVote()")
    expect(head).to_contain_text("2/2 people done")
    expect(a.locator(f'#vote-ov-scroll tr.ov-trip-row[data-trip="{t2}"] td[data-user="bob"]')).to_have_text("🙂")
    print("PASS trip name → Vote tab at that trip; Overview tab remembered across reload; a new vote shows on refresh")

    # Phone: more participants (accounts from earlier scenarios join) → the grid scrolls inside its box.
    joined = 2
    for name in ("nikos", "zoe", "zed", "olga"):
        ctx = logged_in(name)
        if ctx:
            call(ctx, "POST", f"/api/rounds/{rid}/join")
            joined += 1
            ctx.close()
    assert joined >= 4, joined
    ctx_p = logged_in("bob", viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, device_scale_factor=2)
    p = page_of(ctx_p)
    p.locator('#tabbar button[data-tab="vote"]').tap()
    expect(p.locator("#vote-dialog")).to_be_visible()
    p.locator("#vote-tab-overview").tap()
    expect(p.locator("#vote-overview")).to_be_visible()
    expect(p.locator("#vote-ov-scroll th.ov-person")).to_have_count(joined)
    expect(p.locator("#vote-ov-head")).to_contain_text(f"2/{joined} people done")
    for sel in ("#vote-tab-overview", "#vote-tab-vote"):
        assert p.locator(sel).bounding_box()["height"] >= 40, sel
    small = p.evaluate("""() => [...document.querySelectorAll('#vote-ov-scroll td.ov-cell, #vote-ov-scroll .ov-cell-btn')]
        .map((el) => el.getBoundingClientRect()).filter((r) => r.width < 40 || r.height < 40).length""")
    assert small == 0, f"{small} overview cells under 40 px on the phone"
    sizes = p.evaluate("(() => { const b = document.getElementById('vote-ov-scroll'); return [b.scrollWidth, b.clientWidth]; })()")
    assert sizes[0] > sizes[1], f"the phone grid should scroll inside its box: {sizes}"
    p.evaluate("document.getElementById('vote-ov-scroll').scrollLeft = 10000")
    p.wait_for_timeout(100)
    pinned = p.evaluate("""() => { const box = document.getElementById('vote-ov-scroll').getBoundingClientRect();
        const name = document.querySelector('#vote-ov-scroll tr.ov-trip-row th.ov-name').getBoundingClientRect();
        return [box.left, name.left, document.getElementById('vote-ov-scroll').scrollLeft]; }""")
    assert pinned[2] > 0 and abs(pinned[0] + 1 - pinned[1]) <= 2, f"trip name column not pinned: {pinned}"
    if not p.evaluate("document.documentElement.scrollWidth <= innerWidth"):
        raise SystemExit("FAIL horizontal page scroll on the phone (overview)")
    wide = p.evaluate("[...document.querySelectorAll('dialog[open]')].filter((d) => d.scrollWidth > d.clientWidth).length")
    assert wide == 0, "the vote dialog scrolls sideways on the phone (overview)"
    if os.environ.get("UI_SHOTS"):
        p.screenshot(path=os.path.join(os.environ["UI_SHOTS"], "overview-phone-scrolled.png"))
    p.locator(f'#vote-ov-scroll tr.ov-trip-row[data-trip="{t1}"] td[data-user="bob"] .ov-cell-btn').tap()
    expect(p.locator("#vote-ov-pop")).to_have_text("🦖 bob: No-go – “too far”")
    p.locator("#vote-ov-head").tap()
    expect(p.locator("#vote-ov-pop")).to_be_hidden()
    print(f"PASS phone overview ({joined} people): grid scrolls inside its box, trip column pinned, cells ≥ 40 px, "
          "no page side-scroll, popover on tap")
    ctx_p.close()

    # Closed: the final grid with the winner line, also from Past rounds.
    call(ctx_a, "POST", f"/api/rounds/{rid}/close")
    a.evaluate("refreshVote()")
    expect(head).to_contain_text("Closed ")
    expect(head).to_contain_text(f"🏆 {long_name} wins")
    expect(a.locator("#vote-ov-scroll tr.ov-trip-row")).to_have_count(2)
    a.locator("#vote-footer button", has_text="Past rounds").click()
    a.locator("#vote-history button", has_text="Overview 📊").click()
    expect(a.locator("#vote-overview")).to_be_visible()
    expect(head).to_contain_text(f"🏆 {long_name} wins")
    expect(a.locator(f'#vote-ov-scroll tr.ov-trip-row[data-trip="{t1}"] td[data-user="bob"]')).to_have_text("🚫")
    print("PASS closed round: final grid with the 🏆 winner line, also opened from Past rounds")
    ctx_b.close()
    ctx_a.close()


if __name__ == "__main__":
    main()
