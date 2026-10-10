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
    assert r.status_code == 422 and "Give exactly one of hike, stay or sight" in r.text


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


def test_nan_coordinates_rejected_and_list_survives(tmp_path):
    c, store = make(tmp_path)
    ok = c.post("/api/saved-trips", json={"name": "ok", "hikes": [HIKE]}).json()
    raw = '{"name": "poison", "hikes": [{"id": "w:9", "name": "n", "url": "u", "lat": NaN, "lon": 20.7}]}'
    r = c.post("/api/saved-trips", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422 and "hikes.0: lat/lon must be finite coordinates" in r.text
    raw_item = '{"stay": {"id": "a:9", "source": "airbnb", "name": "n", "url": "u", "lat": 1, "lon": 2,' \
               ' "total_price": Infinity}}'
    r = c.post(f"/api/saved-trips/{ok['id']}/items", content=raw_item, headers={"content-type": "application/json"})
    assert r.status_code == 422 and "stay: total_price must be a finite number" in r.text
    r = c.get("/api/saved-trips")
    assert r.status_code == 200 and [t["id"] for t in r.json()] == [ok["id"]]


def test_corrupt_row_is_skipped_404_and_deletable(tmp_path):
    import sqlite3

    c, store = make(tmp_path)
    ok = c.post("/api/saved-trips", json={"name": "ok"}).json()
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO trips (id, doc, updated_at) VALUES ('bad', '{\"name\": null}', 'z')")
    assert [t["id"] for t in c.get("/api/saved-trips").json()] == [ok["id"]]
    assert c.get("/api/saved-trips/bad").status_code == 404
    assert c.get("/api/saved-trips/bad/export.md").status_code == 404
    assert c.delete("/api/saved-trips/bad").status_code == 204


def test_size_caps(tmp_path):
    c, _ = make(tmp_path)
    fat = [{**HIKE, "id": f"wikiloc:{i}", "name": "x" * 6000} for i in range(50)]
    r = c.post("/api/saved-trips", json={"name": "big", "hikes": fat})
    assert r.status_code == 422 and "Trip is too large (max 256 KB)" in r.text
    tid = c.post("/api/saved-trips", json={"name": "big", "hikes": fat[:40]}).json()["id"]
    statuses = [c.post(f"/api/saved-trips/{tid}/items", json={"hike": h}) for h in fat[40:]]
    too_large = [r for r in statuses if r.status_code == 413]
    assert too_large and too_large[0].json() == {"detail": "Trip is too large (max 256 KB)"}
    assert c.get(f"/api/saved-trips/{tid}").status_code == 200


def test_trip_count_cap(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "unused.db")
    store = TripStore(tmp_path / "trips.db", max_trips=2)
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings, trip_store=store))
    assert [c.post("/api/saved-trips", json={"name": f"t{i}"}).status_code for i in range(2)] == [201, 201]
    r = c.post("/api/saved-trips", json={"name": "t3"})
    assert r.status_code == 409
    assert r.json() == {"detail": "At most 500 saved trips – delete some first"}


def test_put_expected_updated_at_detects_lost_edits(tmp_path):
    c, _ = make(tmp_path)
    trip = c.post("/api/saved-trips", json={"name": "t", "expected_updated_at": "2000-01-01T00:00:00Z"}).json()
    assert trip["name"] == "t"  # POST ignores the field
    tid = trip["id"]
    tab_a = c.put(f"/api/saved-trips/{tid}", json={"name": "A", "expected_updated_at": trip["updated_at"]})
    assert tab_a.status_code == 200
    tab_b = c.put(f"/api/saved-trips/{tid}", json={"name": "B", "expected_updated_at": trip["updated_at"]})
    assert tab_b.status_code == 409
    assert tab_b.json() == {"detail": "Trip changed elsewhere – reopen it to see the latest version"}
    assert c.get(f"/api/saved-trips/{tid}").json()["name"] == "A"
    again = c.put(f"/api/saved-trips/{tid}", json={"name": "A2", "expected_updated_at": tab_a.json()["updated_at"]})
    assert again.status_code == 200 and "expected_updated_at" not in again.json()
    assert c.put(f"/api/saved-trips/{tid}", json={"name": "no check"}).status_code == 200


def test_export_markdown(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "Ζαγόρι weekend", "hikes": [HIKE]}).json()["id"]
    r = c.get(f"/api/saved-trips/{tid}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="weekend.md"; filename*=UTF-8\'\'')
    assert r.text.startswith("# Ζαγόρι weekend\n")
    assert "**[Vikos Gorge](https://es.wikiloc.com/v)**" in r.text


def test_print_page(tmp_path):
    c, _ = make(tmp_path)
    tid = c.post("/api/saved-trips", json={"name": "Ζαγόρι weekend", "hikes": [HIKE]}).json()["id"]
    r = c.get(f"/api/saved-trips/{tid}/print")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    import base64
    import hashlib
    import re

    csp = r.headers["content-security-policy"]
    (script,) = re.findall(r"<script>(.*?)</script>", r.text, re.S)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert script_src.strip() == f"script-src 'sha256-{digest}'" and "unsafe-inline" not in script_src
    assert "default-src 'none'" in csp and "base-uri 'none'" in csp and "form-action 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["cache-control"] == "no-cache"
    assert "<title>Ζαγόρι weekend</title>" in r.text
    r = c.get("/api/saved-trips/nope/print")
    assert r.status_code == 404 and r.json() == {"detail": "Trip not found"}


def test_default_store_uses_settings_path(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "d" / "trips.db")
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings))
    c.post("/api/saved-trips", json={"name": "t"})
    assert (tmp_path / "d" / "trips.db").exists()


def test_sights_in_trip_and_full_message(tmp_path):
    c, _ = make(tmp_path)
    s = {"id": "osm:node/1", "name": "Falls", "kind": "waterfall", "lat": 38.0, "lon": 22.0,
         "url": "https://www.openstreetmap.org/node/1", "lines": [[[1, 2], [3, 4]]]}
    tid = c.post("/api/saved-trips", json={"name": "t"}).json()["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"sight": s})
    assert r.status_code == 200 and r.json()["sights"][0]["lines"] == []
    full = c.post("/api/saved-trips", json={"name": "f", "sights": [{**s, "id": f"osm:node/{i}"} for i in range(100)]})
    r = c.post(f"/api/saved-trips/{full.json()['id']}/items", json={"sight": {**s, "id": "osm:node/x"}})
    assert r.status_code == 409 and r.json() == {"detail": "A trip holds at most 100 nature sights"}
    assert c.post(f"/api/saved-trips/{tid}/items", json={}).status_code == 422
