from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Hike, HikeSearch, Stay, StaySearch, TripSearch


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
    settings = Settings(_env_file=None, cache_dir=tmp_path)
    return TestClient(create_app(engine=engine or FakeEngine(), ask_runner=FakeAsk(), settings=settings))


def test_index_served(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200 and "<html" in r.text


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


def test_index_loads_leaflet_and_app(tmp_path):
    html = client(tmp_path).get("/").text
    assert "leaflet@1.9.4" in html and "leaflet.markercluster@1.5.3" in html
    assert "/static/app.js" in html and 'id="ask-form"' in html


def test_static_assets_served(tmp_path):
    c = client(tmp_path)
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/style.css").status_code == 200
