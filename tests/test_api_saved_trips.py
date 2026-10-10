from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.trips_store import TripStore
from tests.test_api import FakeAsk, FakeEngine

HIKE = {"id": "wikiloc:1", "name": "Vikos Gorge", "url": "https://es.wikiloc.com/v", "lat": 39.9, "lon": 20.7}
HIKE2 = {**HIKE, "id": "wikiloc:2", "name": "Papingo pools"}
STAY = {"id": "airbnb:1", "source": "airbnb", "name": "Stone house", "url": "https://www.airbnb.com/rooms/1",
        "lat": 39.95, "lon": 20.75, "total_price": 190, "currency": "EUR"}


def make(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "unused.db")
    store = TripStore(tmp_path / "trips.db")
    return TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings,
                                 trip_store=store)), store


def test_crud_flow(tmp_path):
    c, store = make(tmp_path)
    assert c.get("/api/saved-trips").json() == []
    r = c.post("/api/saved-trips", json={"name": " Zagori ", "start": "2026-10-23", "end": "2026-10-25",
                                         "hikes": [HIKE], "stays": [STAY], "source": "suggested", "score": 77.5})
    assert r.status_code == 201
    trip = r.json()
    assert trip["name"] == "Zagori" and trip["hikes"][0]["id"] == "wikiloc:1"
    tid = trip["id"]
    listed = c.get("/api/saved-trips").json()
    assert [(t["id"], t["hike_count"], t["stay_count"], t["source"]) for t in listed] == [(tid, 1, 1, "suggested")]
    assert c.get(f"/api/saved-trips/{tid}").json()["score"] == 77.5
    r = c.put(f"/api/saved-trips/{tid}", json={"name": "Renamed", "notes": "n", "hikes": [HIKE2, HIKE]})
    assert r.status_code == 200
    assert [h["id"] for h in r.json()["hikes"]] == ["wikiloc:2", "wikiloc:1"]
    assert r.json()["created_at"] == trip["created_at"]
    assert r.json()["stays"] == []
    r = c.delete(f"/api/saved-trips/{tid}")
    assert r.status_code == 204 and r.content == b""
    assert c.get("/api/saved-trips").json() == []


def test_not_found(tmp_path):
    c, _ = make(tmp_path)
    for method, path, body in [("get", "/api/saved-trips/nope", None),
                               ("put", "/api/saved-trips/nope", {"name": "x"}),
                               ("delete", "/api/saved-trips/nope", None),
                               ("post", "/api/saved-trips/nope/items", {"hike": HIKE}),
                               ("get", "/api/saved-trips/nope/export.md", None)]:
        r = c.request(method.upper(), path, json=body)
        assert r.status_code == 404, path
        assert r.json() == {"detail": "Trip not found"}


def test_validation(tmp_path):
    c, _ = make(tmp_path)
    assert c.post("/api/saved-trips", json={"name": "  "}).status_code == 422
    r = c.post("/api/saved-trips", json={"name": "t", "start": "2026-10-25", "end": "2026-10-23"})
    assert r.status_code == 422 and "end must be on or after start" in r.text
    tid = c.post("/api/saved-trips", json={"name": "t"}).json()["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={})
    assert r.status_code == 422 and "Give exactly one of hike or stay" in r.text


def test_add_items_dedupe_and_full(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "t", "hikes": [HIKE]}).json()["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200 and len(r.json()["hikes"]) == 1
    r = c.post(f"/api/saved-trips/{tid}/items", json={"stay": STAY})
    assert [s["id"] for s in r.json()["stays"]] == ["airbnb:1"]
    full = c.post("/api/saved-trips", json={"name": "f", "stays": [{**STAY, "id": f"airbnb:{i}"} for i in range(50)]})
    r = c.post(f"/api/saved-trips/{full.json()['id']}/items", json={"stay": {**STAY, "id": "airbnb:x"}})
    assert r.status_code == 409
    assert r.json() == {"detail": "A trip holds at most 50 hikes and 50 stays"}


def test_export_markdown(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "Ζαγόρι weekend", "hikes": [HIKE]}).json()["id"]
    r = c.get(f"/api/saved-trips/{tid}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="weekend.md"; filename*=UTF-8\'\'')
    assert r.text.startswith("# Ζαγόρι weekend\n")
    assert "**[Vikos Gorge](https://es.wikiloc.com/v)**" in r.text


def test_default_store_uses_settings_path(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "d" / "trips.db")
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings))
    c.post("/api/saved-trips", json={"name": "t"})
    assert (tmp_path / "d" / "trips.db").exists()
