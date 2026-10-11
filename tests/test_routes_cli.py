import json
import shutil
import sqlite3

import httpx
import pytest

from app.accounts import AccountStore
from app.cache import DiskCache
from app.models import Hike, SavedTripIn, Stay
from app.routes import main
from app.sources.wikiloc_route import RouteFetcher
from app.trips_store import TripStore
from app.votes_store import RoundStore
from tests.test_wikiloc_route import FakePages

OWN = "_ibE_seK"


def hike(i, **kw):
    return Hike(id=f"wikiloc:{i}", name=f"Hike {i}", url="https://es.wikiloc.com/x", lat=39.0, lon=21.0, **kw)


def seeded(tmp_path):
    db = tmp_path / "trips.db"
    store = TripStore(db)
    accounts = AccountStore(db)
    RoundStore(db, store, accounts)  # creates the rounds tables too
    accounts.signup("nikos", "password1", ip="1.2.3.4", emoji="🦉")
    a = store.create(SavedTripIn(name="A", hikes=[hike(1), hike(2, route=OWN)],
                                 stays=[Stay(id="airbnb:1", source="airbnb", name="s", url="u", lat=39, lon=21)]))
    b = store.create(SavedTripIn(name="B", hikes=[hike(1), hike(3)], notes="keep me"))
    return db, store, a, b


def fetcher(tmp_path, pages):
    return RouteFetcher(cache=DiskCache(tmp_path / "cache"), fetch_page=pages, cooldown_s=0)


def test_fetch_from_a_local_db(tmp_path, capsys):
    db, _, _, _ = seeded(tmp_path)
    before = db.read_bytes()
    pages = FakePages()
    out = tmp_path / "routes.json"
    assert main(["fetch", "--from", str(db), "--out", str(out)], fetcher=fetcher(tmp_path, pages)) == 0
    data = json.loads(out.read_text())
    assert data["version"] == 1 and set(data["routes"]) == {"wikiloc:1", "wikiloc:3"}  # wikiloc:2 has one
    assert sorted(pages.urls) == ["https://es.wikiloc.com/wikiloc/view.do?id=1",
                                  "https://es.wikiloc.com/wikiloc/view.do?id=3"]
    assert db.read_bytes() == before
    assert "fetched 2 of 2" in capsys.readouterr().out


def test_fetch_from_a_base_url(tmp_path):
    _, store, a, b = seeded(tmp_path)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/saved-trips":
            return httpx.Response(200, json=[{"id": a.id}, {"id": b.id}])
        trip = store.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=trip.model_dump_json())

    out = tmp_path / "routes.json"
    pages_ok = FakePages()
    assert main(["fetch", "--from", "https://hick.test/", "--out", str(out)], fetcher=fetcher(tmp_path, pages_ok),
                transport=httpx.MockTransport(handler)) == 0
    assert seen == ["/api/saved-trips", f"/api/saved-trips/{a.id}", f"/api/saved-trips/{b.id}"]
    assert set(json.loads(out.read_text())["routes"]) == {"wikiloc:1", "wikiloc:3"}


def test_fetch_leaves_failures_out(tmp_path, capsys):
    db, _, _, _ = seeded(tmp_path)
    out = tmp_path / "routes.json"
    main(["fetch", "--from", str(db), "--out", str(out)], fetcher=fetcher(tmp_path, FakePages(fail=True)))
    assert json.loads(out.read_text())["routes"] == {}
    assert "(2 failed)" in capsys.readouterr().out


def test_fetch_missing_db(tmp_path):
    with pytest.raises(SystemExit):
        main(["fetch", "--from", str(tmp_path / "none.db"), "--out", str(tmp_path / "r.json")],
             fetcher=fetcher(tmp_path, FakePages()))
    assert not (tmp_path / "none.db").exists()


def dump(db, table):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def test_apply_merges_routes_into_a_copy_of_the_db(tmp_path, capsys):
    db, _, a, b = seeded(tmp_path)
    copy = tmp_path / "copy.db"
    shutil.copy(db, copy)
    with sqlite3.connect(copy) as conn:
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    assert {"trips", "users", "sessions", "rounds"} <= set(tables)
    others = {t: dump(copy, t) for t in tables if t != "trips"}
    before = {t.id: t for t in TripStore(copy).list_trips()}
    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps({"version": 1, "routes": {"wikiloc:1": "_p~iF~ps|U", "wikiloc:2": "_seK_ibE",
                                                                 "wikiloc:9": OWN, "wikiloc:3": "bad route"}}))
    assert main(["apply", str(routes_file), "--db", str(copy)]) == 0
    out = capsys.readouterr().out
    assert "3 routes in" in out and "2 hikes in 2 trips got a route" in out and "1 already had one" in out
    store = TripStore(copy)
    ta, tb = store.get(a.id), store.get(b.id)
    assert [(h.id, h.route) for h in ta.hikes] == [("wikiloc:1", "_p~iF~ps|U"), ("wikiloc:2", OWN)]
    assert [(h.id, h.route) for h in tb.hikes] == [("wikiloc:1", "_p~iF~ps|U"), ("wikiloc:3", None)]
    # nothing else changed: other fields, timestamps, other tables
    assert ta.model_copy(update={"hikes": a.hikes}) == a and tb.model_copy(update={"hikes": b.hikes}) == b
    assert {t: dump(copy, t) for t in tables if t != "trips"} == others
    assert {t.id: t.updated_at for t in store.list_trips()} == {i: t.updated_at for i, t in before.items()}
    # applying again changes nothing
    snapshot = dump(copy, "trips")
    main(["apply", str(routes_file), "--db", str(copy)])
    assert dump(copy, "trips") == snapshot


def test_apply_missing_db_or_bad_file(tmp_path):
    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps({"routes": {}}))
    with pytest.raises(SystemExit):
        main(["apply", str(routes_file), "--db", str(tmp_path / "none.db")])
    assert not (tmp_path / "none.db").exists()
    routes_file.write_text("[1, 2]")
    db, _, _, _ = seeded(tmp_path)
    with pytest.raises(SystemExit):
        main(["apply", str(routes_file), "--db", str(db)])
