"""Headless-Chrome check of the filter popups (not collected by pytest).

Start the app first, then run the check against it:
    env -u VIRTUAL_ENV uv run uvicorn app.main:app --port 8765
    env -u VIRTUAL_ENV uv run --with playwright python tests/ui/check_ui.py http://127.0.0.1:8765

The script talks only to the local server. Search endpoints are answered by Playwright with
canned JSON, so no Wikiloc/Airbnb/Booking request is made; the captured request bodies are
asserted instead.
"""

import json
import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HIKE = {"id": "wikiloc:1", "name": "Test hike", "url": "https://es.wikiloc.com/x", "lat": 38.5, "lon": 22.6,
        "distance_km": 9.5, "elevation_gain_m": 600, "difficulty": "Moderate", "trailrank": 70}
HIKE2 = {**HIKE, "id": "wikiloc:2", "name": "Second hike", "lat": 38.6}
STAY = {"id": "airbnb:1", "source": "airbnb", "name": "Area house", "url": "https://www.airbnb.com/rooms/1",
        "lat": 38.55, "lon": 22.65, "total_price": 180, "currency": "EUR", "rating": 4.8, "badge": "pets",
        "distance_km": 3.2}
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
    # Stays inside a drawn area come back with one house; hike-centred stays stay empty.
    "/api/stays": lambda body: {"stays": [STAY] if "area" in body else [], "warnings": [], "center": None},
    "/api/trips": lambda body: {"combos": [], "warnings": []},
}


def main() -> None:
    requests: list[tuple[str, dict]] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def answer(route):
            path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
            requests.append((path, route.request.post_data_json or {}))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED[path](requests[-1][1])))

        for path in CANNED:
            page.route(f"**{path}", answer)

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

        # A new shape replaces the old one; Trip plan uses it.
        page.evaluate("""map.fire(L.Draw.Event.CREATED,
            {layer: L.circle([38.5, 22.6], {radius: 8000}), layerType: 'circle'})""")
        assert page.evaluate("drawnItems.getLayers().length") == 1
        page.check('input[name="mode"][value="trip"]')
        path, body = search_and_capture()
        assert path == "/api/trips" and body["area"] == {
            "shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000}, body
        print("PASS circle replaces rectangle; Trip plan sends the area")

        # Without a shape Trip plan falls back to the drive radius (no area sent).
        page.evaluate("drawnItems.clearLayers()")
        path, body = search_and_capture()
        assert path == "/api/trips" and "area" not in body, body
        print("PASS Trip plan without a shape")

        browser.close()
    if errors:
        raise SystemExit(f"FAIL page errors: {errors}")
    print("ALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
