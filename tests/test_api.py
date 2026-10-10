import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.geocode import Place
from app.models import BBox, Hike, HikeSearch, Stay, StaySearch, TripSearch


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def search_hikes(self, q):
        self.calls.append(("hikes", q))
        return HikeSearch(hikes=[Hike(id="wikiloc:1", name="h", url="u", lat=39.4, lon=23.0)],
                          warnings=["w"], drive_limit_minutes=240)

    async def search_stays(self, q):
        self.calls.append(("stays", q))
        return StaySearch(stays=[Stay(id="airbnb:1", source="airbnb", name="s", url="u", lat=39.4, lon=23.0)])

    async def plan_trip(self, q):
        self.calls.append(("trips", q))
        return TripSearch()


class FakeAsk:
    def available(self):
        return True


def client(tmp_path, engine=None):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")
    return TestClient(create_app(engine=engine or FakeEngine(), ask_runner=FakeAsk(), settings=settings))


def test_index_served(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200 and "<html" in r.text


def test_page_and_static_files_are_revalidated(tmp_path):
    # A cached old index.html with a new app.js (or the reverse) breaks the UI after an update.
    c = client(tmp_path)
    for path in ["/", "/static/app.js", "/static/trips.js", "/static/style.css"]:
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path


def test_config(tmp_path):
    body = client(tmp_path).get("/api/config").json()
    assert body["home"] == {"name": "Athens", "lat": 37.9838, "lon": 23.7275}
    assert body["drive_bands"] == {"day_trip": 90, "weekend": 240}
    assert body["ask_available"] is True and body["default_guests"] == 2


def test_hikes_endpoint(tmp_path):
    engine = FakeEngine()
    r = client(tmp_path, engine).post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25",
                                                          "drive_limit": "unlimited"})
    assert r.status_code == 200
    body = r.json()
    assert body["hikes"][0]["id"] == "wikiloc:1" and body["warnings"] == ["w"]
    assert engine.calls[0][1].drive_limit == "unlimited"


def test_stays_and_trips_endpoints(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1"})
    assert r.status_code == 200 and r.json()["stays"][0]["id"] == "airbnb:1"
    r = c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25", "dog": False})
    assert r.status_code == 200 and r.json() == {"combos": [], "warnings": []}


def test_end_before_start_is_422_with_message(tmp_path):
    r = client(tmp_path).post("/api/hikes", json={"start": "2026-10-25", "end": "2026-10-23"})
    assert r.status_code == 422
    assert "end date must be on or after start date" in r.text


def test_filters_catalogue_endpoint(tmp_path):
    body = client(tmp_path).get("/api/filters").json()
    assert body["hike"]["defaults"]["activities"] == [1]
    assert body["stay"]["defaults"]["price_max"] is None
    assert [f["key"] for f in body["hike"]["fields"]][0] == "activities"
    assert any(f["key"] == "amenities" for f in body["stay"]["fields"])
    assert body["wikiloc_login"] is False


def test_filters_catalogue_reports_wikiloc_login(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db", wikiloc_cookie="SESSION=abc")
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings))
    body = c.get("/api/filters").json()
    assert body["wikiloc_login"] is True and "SESSION" not in json.dumps(body)


def test_filters_and_area_reach_engine(tmp_path):
    engine = FakeEngine()
    c = client(tmp_path, engine)
    r = c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "drawn",
                                   "area": {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000},
                                   "filters": {"min_trailrank": 60, "sort": "top_rated"}})
    assert r.status_code == 200
    q = engine.calls[0][1]
    assert q.area.radius_m == 8000 and q.filters.min_trailrank == 60 and q.filters.sort == "top_rated"
    c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1",
                               "filters": {"price_max": 120, "amenities": ["parking"]}})
    assert engine.calls[1][1].filters.amenities == ["parking"]
    c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25",
                               "area": {"shape": "rectangle", "south": 38.3, "west": 22.3, "north": 38.7,
                                        "east": 22.9},
                               "hike_filters": {"difficulty_max": 3}, "stay_filters": {"entire_place": True}})
    trip = engine.calls[2][1]
    assert trip.area.shape == "rectangle" and trip.hike_filters.difficulty_max == 3
    assert trip.stay_filters.entire_place is True


def test_invalid_filter_range_and_missing_area_are_422(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1",
                                   "filters": {"price_min": 200, "price_max": 100}})
    assert r.status_code == 422 and "price per night: minimum must not be greater than maximum" in r.text
    r = c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "drawn"})
    assert r.status_code == 422 and "Draw a rectangle or circle first" in r.text


def test_index_loads_leaflet_and_app(tmp_path):
    html = client(tmp_path).get("/").text
    assert "leaflet@1.9.4" in html and "leaflet.markercluster@1.5.3" in html
    assert "/static/app.js" in html and 'id="ask-form"' in html


def test_static_assets_served(tmp_path):
    c = client(tmp_path)
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/style.css").status_code == 200


from app.ask import AskResult


class FakeAskRunner:
    def __init__(self):
        self.requests = []

    def available(self):
        return True

    async def ask(self, req):
        self.requests.append(req)
        return AskResult(summary="Go to Pelion")


def test_ask_endpoint(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")
    runner = FakeAskRunner()
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=runner, settings=settings))
    r = c.post("/api/ask", json={"question": "weekend hike", "context": {"dog": True}})
    assert r.status_code == 200 and r.json()["summary"] == "Go to Pelion"
    assert runner.requests[0].context == {"dog": True}
    assert c.post("/api/ask", json={"question": "x"}).status_code == 422


def test_index_has_filter_buttons_and_scripts(tmp_path):
    c = client(tmp_path)
    html = c.get("/").text
    assert 'id="hike-filters-btn"' in html and 'id="stay-filters-btn"' in html
    assert html.index("/static/filters.js") < html.index("/static/dialog.js") < html.index("/static/app.js")
    assert c.get("/static/filters.js").status_code == 200 and c.get("/static/dialog.js").status_code == 200


def test_stays_accept_a_drawn_area_and_hikes_a_page_offset(tmp_path):
    engine = FakeEngine()
    c = client(tmp_path, engine)
    r = c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25",
                                   "area": {"shape": "circle", "lat": 38.5, "lon": 22.6, "radius_m": 8000}})
    assert r.status_code == 200 and engine.calls[0][1].area.radius_m == 8000
    assert c.post("/api/stays", json={"start": "2026-10-23", "end": "2026-10-25"}).status_code == 422
    r = c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25", "page_offset": 20})
    assert r.status_code == 200 and engine.calls[1][1].page_offset == 20
    assert c.post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25",
                                      "page_offset": 401}).status_code == 422


def test_hike_response_carries_total_and_next_page_offset(tmp_path):
    class Paged(FakeEngine):
        async def search_hikes(self, q):
            return HikeSearch(total_available=17000, next_page_offset=20)

    body = client(tmp_path, Paged()).post("/api/hikes", json={"start": "2026-10-23", "end": "2026-10-25"}).json()
    assert body["total_available"] == 17000 and body["next_page_offset"] == 20


class FakeGeocoder:
    def __init__(self, place=None, fail=False):
        self.place, self.fail, self.calls = place, fail, []

    async def locate(self, text):
        self.calls.append(text)
        if self.fail:
            raise ConnectionError("down")
        return self.place


PLACE = Place(name="Makrinitsa, Volos", lat=39.39, lon=22.99,
              bbox=BBox(south=39.38, west=22.98, north=39.40, east=23.00))


def geo_client(tmp_path, geocoder):
    engine = FakeEngine()
    engine.geocoder = geocoder
    return client(tmp_path, engine)


def test_locate_found(tmp_path):
    geo = FakeGeocoder(PLACE)
    body = geo_client(tmp_path, geo).get("/api/locate", params={"q": "  Μακρινίτσα "}).json()
    assert body["place"]["name"] == "Makrinitsa, Volos" and body["place"]["bbox"]["north"] == 39.40
    assert body["place"]["lat"] == 39.39 and body["warnings"] == [] and geo.calls == ["Μακρινίτσα"]


def test_locate_not_found_warns(tmp_path):
    body = geo_client(tmp_path, FakeGeocoder(None)).get("/api/locate", params={"q": "Atlantis"}).json()
    assert body == {"place": None, "warnings": ["Couldn't find 'Atlantis' in Greece"]}


def test_locate_geocoder_down_is_200_with_warning(tmp_path):
    r = geo_client(tmp_path, FakeGeocoder(fail=True)).get("/api/locate", params={"q": "Volos"})
    assert r.status_code == 200
    assert r.json() == {"place": None, "warnings": ["Place search unavailable – try again later"]}


def test_locate_validates_q(tmp_path):
    c = geo_client(tmp_path, FakeGeocoder(PLACE))
    assert c.get("/api/locate").status_code == 422
    assert c.get("/api/locate", params={"q": ""}).status_code == 422
    assert c.get("/api/locate", params={"q": "   "}).status_code == 422
    assert c.get("/api/locate", params={"q": "x" * 201}).status_code == 422
    assert c.get("/api/locate", params={"q": "x" * 200}).status_code == 200


def test_stays_endpoint_accepts_providers(tmp_path):
    engine = FakeEngine()
    c = client(tmp_path, engine)
    body = {"start": "2026-10-23", "end": "2026-10-25", "hike_id": "wikiloc:1"}
    assert c.post("/api/stays", json={**body, "providers": ["booking"]}).status_code == 200
    assert engine.calls[0][1].providers == ["booking"]
    assert c.post("/api/stays", json={**body, "providers": ["expedia"]}).status_code == 422


def test_trips_endpoint_accepts_mode_and_place(tmp_path):
    engine = FakeEngine()
    c = client(tmp_path, engine)
    r = c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "place", "place": "Zagori"})
    assert r.status_code == 200 and engine.calls[0][1].place == "Zagori"
    assert c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "place"}).status_code == 422
    assert c.post("/api/trips", json={"start": "2026-10-23", "end": "2026-10-25", "mode": "drawn"}).status_code == 422


def test_index_has_two_action_buttons_and_no_trip_radio(tmp_path):
    html = client(tmp_path).get("/").text
    assert 'id="search"' in html and 'id="suggest-trips"' in html and 'value="trip"' not in html
    assert 'id="combo-section"' in html and 'id="retry-booking"' in html


def test_index_has_goto_control(tmp_path):
    html = client(tmp_path).get("/").text
    assert 'id="goto-input"' in html and 'id="goto-btn"' in html


def test_index_has_draw_tools(tmp_path):
    html = client(tmp_path).get("/").text
    assert "leaflet-draw@1.0.4/dist/leaflet.draw.js" in html and "leaflet-draw@1.0.4/dist/leaflet.draw.css" in html
    assert 'id="draw-rect-btn"' in html and 'id="draw-circle-btn"' in html and 'id="draw-hint"' in html and 'value="drawn"' in html
