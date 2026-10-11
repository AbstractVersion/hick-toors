from fastapi.testclient import TestClient

from app.cache import DiskCache
from app.config import Settings
from app.main import create_app
from app.models import MAX_TRIP_BYTES, SavedTripIn, trip_size
from app.sources.wikiloc_route import ROUTE_TTL, RouteFetcher, decode_polyline
from app.trips_store import TripStore
from tests.builders import login_client
from tests.test_api import FakeAsk, FakeEngine
from tests.test_wikiloc_route import FIXTURE, FakePages

HIKE = {"id": "wikiloc:1", "name": "Vikos Gorge", "url": "https://es.wikiloc.com/v", "lat": 39.9, "lon": 20.7}
HIKE2 = {**HIKE, "id": "wikiloc:2", "name": "Papingo pools"}
OWN = "_ibE_seK"


def make(tmp_path, pages=None, login=True, online=True):
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", trips_db=tmp_path / "unused.db")
    store = TripStore(tmp_path / "trips.db")
    pages = pages or FakePages()
    fetcher = RouteFetcher(cache=DiskCache(tmp_path / "routes"), fetch_page=pages, online=online, cooldown_s=0)
    c = TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings, trip_store=store,
                              route_fetcher=fetcher))
    return (login_client(c) if login else c), store, pages, fetcher


def new_trip(c, **body):
    r = c.post("/api/saved-trips", json={"name": "T", **body})
    assert r.status_code == 201, r.text
    return r.json()


def test_add_item_fetches_and_stores_the_route(tmp_path):
    c, store, pages, _ = make(tmp_path)
    tid = new_trip(c)["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200
    route = r.json()["hikes"][0]["route"]
    assert decode_polyline(route)[0] == tuple(round(v, 5) for v in FIXTURE["start"])
    assert store.get(tid).hikes[0].route == route
    assert pages.urls == ["https://es.wikiloc.com/wikiloc/view.do?id=1"]
    c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})  # already in the trip: no fetch
    tid2 = new_trip(c, hikes=[])["id"]
    assert c.post(f"/api/saved-trips/{tid2}/items", json={"hike": HIKE}).json()["hikes"][0]["route"] == route
    assert len(pages.urls) == 1  # the second trip got it from the cache


def test_add_item_without_a_route_when_the_fetch_fails(tmp_path):
    c, store, pages, _ = make(tmp_path, pages=FakePages(fail=True))
    tid = new_trip(c)["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200 and r.json()["hikes"][0]["route"] is None
    assert len(pages.urls) == 1


def test_add_item_keeps_a_sent_route_without_fetching(tmp_path):
    c, _, pages, _ = make(tmp_path)
    tid = new_trip(c)["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": {**HIKE, "route": OWN}})
    assert r.json()["hikes"][0]["route"] == OWN and pages.urls == []


def test_add_item_stays_and_unknown_trips_fetch_nothing(tmp_path):
    c, _, pages, _ = make(tmp_path)
    assert c.post("/api/saved-trips/nope/items", json={"hike": HIKE}).status_code == 404
    assert pages.urls == []


def test_add_item_drops_the_route_at_the_size_cap(tmp_path):
    c, store, pages, _ = make(tmp_path)
    slim = SavedTripIn(name="T", hikes=[{**HIKE2}, {**HIKE}])
    pad = MAX_TRIP_BYTES - trip_size(slim) - 300  # room for HIKE but not its ~800-char route
    tid = new_trip(c, hikes=[{**HIKE2, "route": OWN, "name": "P" + "x" * pad}])["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200
    assert [(h["id"], h["route"] is None) for h in r.json()["hikes"]] == [("wikiloc:2", False), ("wikiloc:1", True)]
    assert len(pages.urls) == 1


def test_create_fills_missing_routes(tmp_path):
    c, _, pages, _ = make(tmp_path)
    trip = new_trip(c, hikes=[HIKE, {**HIKE2, "route": OWN}])
    assert trip["hikes"][0]["route"] and trip["hikes"][1]["route"] == OWN
    assert pages.urls == ["https://es.wikiloc.com/wikiloc/view.do?id=1"]


def test_put_keeps_stored_routes_and_fetches_only_new_hikes(tmp_path):
    c, store, pages, _ = make(tmp_path)
    trip = new_trip(c, hikes=[{**HIKE, "route": OWN}])
    # an editor with old data sends the hike without its route, plus a new hike
    r = c.put(f"/api/saved-trips/{trip['id']}", json={"name": "Renamed", "hikes": [HIKE2, HIKE]})
    assert r.status_code == 200
    hikes = r.json()["hikes"]
    assert [h["id"] for h in hikes] == ["wikiloc:2", "wikiloc:1"]
    assert hikes[1]["route"] == OWN and hikes[0]["route"]
    assert pages.urls == ["https://es.wikiloc.com/wikiloc/view.do?id=2"]
    r = c.put(f"/api/saved-trips/{trip['id']}", json={"name": "Again", "hikes": [HIKE2, HIKE]})
    assert [h["route"] for h in r.json()["hikes"]] == [hikes[0]["route"], OWN]
    assert len(pages.urls) == 1  # both stored: nothing fetched again


def test_put_unknown_trip_fetches_nothing(tmp_path):
    c, _, pages, _ = make(tmp_path)
    assert c.put("/api/saved-trips/nope", json={"name": "x", "hikes": [HIKE]}).status_code == 404
    assert pages.urls == []


def test_route_endpoint_cache_first(tmp_path):
    c, _, pages, fetcher = make(tmp_path, login=False)
    fetcher.cache.set("route:v1:5", OWN, ROUTE_TTL)
    assert c.get("/api/hikes/5/route").json() == {"route": OWN}
    assert c.get("/api/hikes/wikiloc:5/route").json() == {"route": OWN}
    assert c.get("/api/hikes/6/route").json() == {"route": None}  # logged out: cache only
    assert pages.urls == []
    login_client(c)
    route = c.get("/api/hikes/6/route").json()["route"]
    assert route and pages.urls == ["https://es.wikiloc.com/wikiloc/view.do?id=6"]
    assert c.get("/api/hikes/6/route").json() == {"route": route}
    assert len(pages.urls) == 1


def test_route_endpoint_rejects_bad_ids(tmp_path):
    c, _, pages, _ = make(tmp_path)
    for bad in ("abc", "airbnb:1", "1.5", "9" * 13):
        assert c.get(f"/api/hikes/{bad}/route").status_code == 422, bad
    assert pages.urls == []


def test_route_endpoint_failure_is_null(tmp_path):
    c, _, _, _ = make(tmp_path, pages=FakePages(fail=True))
    assert c.get("/api/hikes/7/route").json() == {"route": None}


def test_offline_fetcher_never_fetches_and_config_says_so(tmp_path):
    c, _, pages, _ = make(tmp_path, online=False)
    tid = new_trip(c, hikes=[HIKE])["id"]
    assert c.get(f"/api/saved-trips/{tid}").json()["hikes"][0]["route"] is None
    assert c.get("/api/hikes/1/route").json() == {"route": None}
    assert pages.urls == []
    assert c.get("/api/config").json()["route_fetch"] is False


def test_default_app_with_an_injected_engine_does_not_fetch(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", trips_db=tmp_path / "t.db")
    c = login_client(TestClient(create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings)))
    assert c.get("/api/config").json()["route_fetch"] is False
    assert c.post("/api/saved-trips", json={"name": "T", "hikes": [HIKE]}).json()["hikes"][0]["route"] is None



def test_an_out_of_range_track_never_breaks_the_trip(tmp_path):
    from tests.test_wikiloc_route import out_of_range_page

    c, store, _, _ = make(tmp_path, pages=FakePages(html=out_of_range_page()))
    tid = new_trip(c, hikes=[HIKE2])["id"]
    r = c.post(f"/api/saved-trips/{tid}/items", json={"hike": HIKE})
    assert r.status_code == 200 and r.json()["hikes"][1]["route"] is None
    assert c.get(f"/api/saved-trips/{tid}").status_code == 200
    assert [t["id"] for t in c.get("/api/saved-trips").json()] == [tid]
    assert c.put(f"/api/saved-trips/{tid}", json={"name": "T", "hikes": [HIKE, HIKE2]}).status_code == 200
    assert c.post("/api/saved-trips", json={"name": "U", "hikes": [{**HIKE, "id": "wikiloc:7"}]}).status_code == 201


def test_a_broken_page_never_breaks_a_save(tmp_path):
    c, _, _, _ = make(tmp_path, pages=FakePages(html="<script>var mapData = " + "[" * 200000 + "</script>"))
    assert c.post("/api/saved-trips", json={"name": "T", "hikes": [HIKE]}).status_code == 201


def test_a_crafted_route_in_a_body_is_a_422(tmp_path):
    c, _, _, _ = make(tmp_path)
    bad = {**HIKE, "route": "~" * 300 + "??"}
    assert c.post("/api/saved-trips", json={"name": "T", "hikes": [bad]}).status_code == 422
    tid = new_trip(c)["id"]
    assert c.post(f"/api/saved-trips/{tid}/items", json={"hike": bad}).status_code == 422
    assert c.put(f"/api/saved-trips/{tid}", json={"name": "T", "hikes": [bad]}).status_code == 422


def test_route_endpoint_does_not_refetch_a_trail_without_track(tmp_path):
    pages = FakePages(html="<html>no map</html>")
    c, _, _, _ = make(tmp_path, pages=pages)
    assert c.get("/api/hikes/8/route").json() == {"route": None}
    assert c.get("/api/hikes/8/route").json() == {"route": None}
    assert len(pages.urls) == 1
