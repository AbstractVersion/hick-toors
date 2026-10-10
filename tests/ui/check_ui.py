"""Headless-Chrome check of the filter popups, results, Trip picks and saved trips (not collected by pytest).

Start the app first with a throwaway trips database, then run the check against it:
    TRIPS_DB=$(mktemp -d)/trips.db env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765
    env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765

The script talks only to the local server. Search endpoints are answered by Playwright with
canned JSON, so no Wikiloc/Airbnb/Booking request is made; the captured request bodies are
asserted instead. The saved-trips API (/api/saved-trips...) is NOT mocked: the trips scenario
creates, edits, exports and deletes a real trip, so it refuses to run unless the trips list is empty.
"""

import json
import re
import sys

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
LOCATE_RE = re.compile(r".*/api/locate(\?.*)?$")
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

        # Part A: a drawn-area hike search also loads the stays inside the shape, no hike click needed.
        expect(page.locator("#search")).to_be_enabled()
        page.click("#search")
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

        # A new search while stays are loading drops their spinners at once; late answers change nothing.
        expect(page.locator("#search")).to_be_enabled()
        hold.add("/api/stays")
        page.locator("#hike-list li").first.click()
        wait_held(2)
        expect(page.locator('[data-spin="stay:airbnb"]')).to_be_visible()
        expect(page.locator('[data-spin="stay:booking"]')).to_be_visible()
        expect(page.locator("#stay-section")).to_have_attribute("aria-busy", "true")
        page.click("#search")
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

        browser.close()
    if errors:
        raise SystemExit(f"FAIL page errors: {errors}")
    print("ALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
