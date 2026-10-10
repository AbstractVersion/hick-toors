"""Headless-Chrome check of the filter popups, results, Trip picks, saved trips and the 🌿 Nature layer
(not collected by pytest).

Start the app first with a throwaway trips database, then run the check against it:
    TRIPS_DB=$(mktemp -d)/trips.db env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765
    env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765

The script talks only to the local server. Search endpoints are answered by Playwright with
canned JSON, so no Wikiloc/Airbnb/Booking/Overpass request is made; the captured request bodies are
asserted instead. The saved-trips API (/api/saved-trips...) is NOT mocked: the trips scenario
creates, edits, exports and deletes a real trip, so it refuses to run unless the trips list is empty.
"""

import json
import re
import sys
from urllib.parse import parse_qs, urlsplit

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
        expect(page.locator(".leaflet-marker-icon.pin.airbnb, .marker-cluster")).not_to_have_count(0)
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
        menu = page.locator("#trip-menu")
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
        expect(rows.first).to_contain_text("2 hikes · 1 stay · ⭐")
        rows.first.click()
        expect(page.locator("#trip-editor")).to_be_visible()
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

        rows.first.click()
        page.click("#trip-delete")
        expect(page.locator("#trip-delete")).to_have_text("Confirm delete")
        page.click("#trip-delete")
        expect(rows).to_have_count(0)
        expect(page.locator("#trips-empty")).to_be_visible()
        expect(page.locator("#trips-count")).to_have_text("(0)")
        page.click("#trips-close")
        expect(dlg).to_be_hidden()
        print("PASS Delete needs two clicks; list empty, count (0)")

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

        browser.close()
    if errors:
        raise SystemExit(f"FAIL page errors: {errors}")
    print("ALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
