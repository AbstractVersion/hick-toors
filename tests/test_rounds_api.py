import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.mcp_tools as tools
from app.accounts import AccountStore
from app.config import Settings
from app.main import create_app
from app.models import SavedTripIn, Stay
from app.trips_store import TripStore
from app.votes_store import RoundStore
from tests.builders import login_client
from tests.test_api import FakeAsk, FakeEngine


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


def stay(i):
    return Stay(id=f"airbnb:{i}", source="airbnb", name=f"Stay {i}", url="https://www.airbnb.com/rooms/1",
                lat=39.0, lon=21.0)


class Env:
    def client(self, name=None):
        c = TestClient(self.app)
        if name:
            login_client(c, name)
        return c

    def closes(self, hours=48):
        return (self.clock() + timedelta(hours=hours)).isoformat()

    def body(self, keys="AB", hours=48, title="Autumn"):
        return {"title": title, "closes_at": self.closes(hours), "trip_ids": [self.ids[k] for k in keys]}


@pytest.fixture
def env(tmp_path):
    e = Env()
    e.clock = Clock()
    path = tmp_path / "trips.db"
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=path)
    trips = TripStore(path)
    accounts = AccountStore(path, now=e.clock)
    e.ids = {k: trips.create(SavedTripIn(name=f"Trip {k}", stays=[stay(1), stay(2)])).id for k in "ABC"}
    e.rounds = RoundStore(path, trips, accounts, now=e.clock)
    e.app = create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings, trip_store=trips,
                       accounts=accounts, rounds=e.rounds)
    e.nikos = e.client("Nikos")
    e.maria = e.client("Maria")
    e.anon = e.client()
    return e


def open_round(env, **kw):
    r = env.nikos.post("/api/rounds", json=env.body(**kw))
    assert r.status_code == 201, r.text
    return r.json()


def test_no_round_current_is_null_and_history_empty(env):
    assert env.anon.get("/api/rounds/current").json() is None
    assert env.anon.get("/api/rounds").json() == []


def test_open_round_and_read_it_logged_out(env):
    view = open_round(env)
    assert view["title"] == "Autumn" and view["opener"] == "Nikos" and view["status"] == "open"
    assert view["me"] == {"rated": 0, "total": 2, "love_trip_id": None, "joined": True}
    cur = env.anon.get("/api/rounds/current").json()
    assert cur["id"] == view["id"] and cur["me"] is None
    assert all(t["my_rating"] is None for t in cur["trips"])
    assert env.anon.get(f"/api/rounds/{view['id']}").json()["id"] == view["id"]
    hist = env.anon.get("/api/rounds").json()
    assert [(h["id"], h["status"], h["opener"]) for h in hist] == [(view["id"], "open", "Nikos")]


def test_vote_trip_and_moved_love(env):
    rid = open_round(env)["id"]
    a, b = env.ids["A"], env.ids["B"]
    r = env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "love", "comment": " great "})
    assert r.status_code == 200 and r.json()["moved_love_from"] is None
    view = r.json()["round"]
    ta = next(t for t in view["trips"] if t["trip_id"] == a)
    assert ta["my_rating"] == "love" and ta["votes"] == [{"username": "Maria", "rating": "love", "comment": "great"}]
    assert view["me"] == {"rated": 1, "total": 2, "love_trip_id": a, "joined": True}
    r = env.maria.put(f"/api/rounds/{rid}/trips/{b}/vote", json={"rating": "love"})
    assert r.json()["moved_love_from"] == a
    r = env.maria.delete(f"/api/rounds/{rid}/trips/{b}/vote")
    assert r.status_code == 200 and r.json()["round"]["me"]["love_trip_id"] is None


def test_vote_stay_and_clear(env):
    rid = open_round(env)["id"]
    a = env.ids["A"]
    url = f"/api/rounds/{rid}/trips/{a}/stays/airbnb:1/vote"
    r = env.maria.put(url, json={"rating": "nogo", "comment": "too far"})
    assert r.status_code == 200
    ta = next(t for t in r.json()["round"]["trips"] if t["trip_id"] == a)
    assert next(s for s in ta["stays"] if s["stay_id"] == "airbnb:1")["my_rating"] == "nogo"
    r = env.maria.delete(url)
    ta = next(t for t in r.json()["round"]["trips"] if t["trip_id"] == a)
    assert next(s for s in ta["stays"] if s["stay_id"] == "airbnb:1")["my_rating"] is None


def test_writes_need_login(env):
    rid = open_round(env)["id"]
    a = env.ids["A"]
    calls = [("post", "/api/rounds", env.body()), ("patch", f"/api/rounds/{rid}", {}),
             ("post", f"/api/rounds/{rid}/close", None), ("post", f"/api/rounds/{rid}/cancel", None),
             ("put", f"/api/rounds/{rid}/trips/{a}/vote", {"rating": "ok"}),
             ("delete", f"/api/rounds/{rid}/trips/{a}/vote", None),
             ("put", f"/api/rounds/{rid}/trips/{a}/stays/airbnb:1/vote", {"rating": "ok"}),
             ("delete", f"/api/rounds/{rid}/trips/{a}/stays/airbnb:1/vote", None)]
    for method, url, body in calls:
        r = env.anon.request(method, url, json=body) if body is not None else env.anon.request(method, url)
        assert r.status_code == 401 and r.json() == {"detail": "Log in first"}, (method, url)


def test_error_mapping(env):
    rid = open_round(env)["id"]
    a, c = env.ids["A"], env.ids["C"]
    r = env.maria.post("/api/rounds", json=env.body())
    assert (r.status_code, r.json()) == (409, {"detail": "A round is already open"})
    for url in (f"/api/rounds/{rid}/close", f"/api/rounds/{rid}/cancel"):
        r = env.maria.post(url)
        assert (r.status_code, r.json()) == (403, {"detail": "Only the opener can do this"})
    assert env.maria.patch(f"/api/rounds/{rid}", json={}).status_code == 403
    r = env.maria.get("/api/rounds/nope")
    assert (r.status_code, r.json()) == (404, {"detail": "Round not found"})
    r = env.maria.put("/api/rounds/nope/trips/x/vote", json={"rating": "ok"})
    assert (r.status_code, r.json()) == (404, {"detail": "Round not found"})
    r = env.maria.put(f"/api/rounds/{rid}/trips/{c}/vote", json={"rating": "ok"})
    assert (r.status_code, r.json()) == (404, {"detail": "Not in this round"})
    r = env.maria.put(f"/api/rounds/{rid}/trips/{a}/stays/airbnb:99/vote", json={"rating": "ok"})
    assert (r.status_code, r.json()) == (404, {"detail": "Not in this round"})
    r = env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "meh"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


def test_value_errors_are_422_with_message(env):
    r = env.nikos.post("/api/rounds", json=env.body(hours=0))
    assert r.status_code == 422 and "between 1 hour and 30 days" in r.json()["detail"]
    r = env.nikos.post("/api/rounds", json={**env.body(), "trip_ids": ["ghost"]})
    assert (r.status_code, r.json()) == (422, {"detail": "Unknown trip: ghost"})
    rid = open_round(env)["id"]
    r = env.nikos.patch(f"/api/rounds/{rid}", json={"closes_at": env.closes(1)})
    assert r.status_code == 422 and "later" in r.json()["detail"]


def test_patch_extends_and_adds_trips(env):
    rid = open_round(env, keys="A")["id"]
    r = env.nikos.patch(f"/api/rounds/{rid}", json={"closes_at": env.closes(72), "add_trip_ids": [env.ids["B"]]})
    assert r.status_code == 200
    assert [t["trip_id"] for t in r.json()["trips"]] == [env.ids["A"], env.ids["B"]]


def test_close_and_cancel(env):
    rid = open_round(env)["id"]
    env.maria.put(f"/api/rounds/{rid}/trips/{env.ids['A']}/vote", json={"rating": "love"})
    r = env.nikos.post(f"/api/rounds/{rid}/close")
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert r.json()["winner"]["trip_id"] == env.ids["A"]
    assert env.anon.get("/api/rounds/current").json()["status"] == "closed"
    r = env.maria.put(f"/api/rounds/{rid}/trips/{env.ids['A']}/vote", json={"rating": "ok"})
    assert (r.status_code, r.json()) == (409, {"detail": "This round is closed"})
    assert env.nikos.post(f"/api/rounds/{rid}/close").status_code == 409
    r2 = open_round(env)
    assert env.nikos.post(f"/api/rounds/{r2['id']}/cancel").json()["status"] == "cancelled"
    assert env.anon.get("/api/rounds/current").json()["id"] == rid  # cancelled is never current


def test_vote_after_deadline_is_409_and_round_is_frozen(env):
    rid = open_round(env, hours=2)["id"]
    a = env.ids["A"]
    env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "love"})
    env.clock.t += timedelta(hours=3)
    r = env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "nogo"})
    assert (r.status_code, r.json()) == (409, {"detail": "This round is closed"})
    cur = env.anon.get("/api/rounds/current").json()
    assert cur["status"] == "closed" and cur["winner"]["trip_id"] == a


def test_user_text_comes_back_verbatim(env):
    nasty = '<script>"x"</script>'
    rid = open_round(env, title=nasty)["id"]
    a = env.ids["A"]
    r = env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "ok", "comment": nasty})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    view = env.anon.get("/api/rounds/current").json()
    assert view["title"] == nasty
    assert next(t for t in view["trips"] if t["trip_id"] == a)["votes"][0]["comment"] == nasty
    assert env.anon.get("/api/rounds").json()[0]["title"] == nasty


@pytest.fixture
def standings(env, monkeypatch):
    monkeypatch.setattr(tools, "_round_store", env.rounds)
    return env


async def test_vote_standings_none(standings):
    assert json.loads(await tools.vote_standings()) == {"round": None}


async def test_vote_standings_without_me_or_my_rating(standings):
    env = standings
    rid = open_round(env)["id"]
    a = env.ids["A"]
    env.maria.put(f"/api/rounds/{rid}/trips/{a}/vote", json={"rating": "love", "comment": "<b>"})
    env.maria.put(f"/api/rounds/{rid}/trips/{a}/stays/airbnb:1/vote", json={"rating": "ok"})
    rnd = json.loads(await tools.vote_standings())["round"]
    assert rnd["id"] == rid and "me" not in rnd and sorted(rnd["waiting_for"]) == ["Maria", "Nikos"]
    assert rnd["participants"] == ["Nikos", "Maria"]
    assert all(t["my_rating"] is None and all(s["my_rating"] is None for s in t["stays"]) for t in rnd["trips"])
    ta = next(t for t in rnd["trips"] if t["trip_id"] == a)
    assert ta["votes"][0] == {"username": "Maria", "rating": "love", "comment": "<b>"}
    assert ta["counts"]["love"] == 1


def test_join_route(env):
    rid = open_round(env)["id"]
    assert env.anon.post(f"/api/rounds/{rid}/join").status_code == 401
    r = env.maria.post(f"/api/rounds/{rid}/join")
    assert r.status_code == 200 and r.json()["participants"] == ["Nikos", "Maria"] and r.json()["me"]["joined"] is True
    assert env.maria.post(f"/api/rounds/{rid}/join").json()["participants"] == ["Nikos", "Maria"]
    assert env.maria.post("/api/rounds/nope/join").status_code == 404
    env.nikos.post(f"/api/rounds/{rid}/close")
    r = env.maria.post(f"/api/rounds/{rid}/join")
    assert r.status_code == 409 and r.json()["detail"] == "This round is closed"


def test_trips_carry_their_fixed_round_position(env):
    rid = open_round(env, keys="ABC")["id"]
    c = env.ids["C"]
    view = env.maria.put(f"/api/rounds/{rid}/trips/{c}/vote", json={"rating": "love"}).json()["round"]
    assert view["trips"][0]["trip_id"] == c  # ranked first …
    by_position = sorted(view["trips"], key=lambda t: t["position"])
    assert [t["trip_id"] for t in by_position] == [env.ids[k] for k in "ABC"]  # … but its position stays
