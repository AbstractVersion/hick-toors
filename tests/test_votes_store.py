import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.accounts import AccountStore, User
from app.models import Counts, RoundIn, RoundPatch, SavedTripIn, Stay, VoteIn
from app.trips_store import TripStore
from tests.builders import emoji_for
from app.vote_ranking import rank
from app.votes_store import NotInRound, NotOpener, RoundAlreadyOpen, RoundClosed, RoundNotFound, RoundStore


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def stay(trip, i):
    return Stay(id=f"airbnb:{trip}{i}", source="airbnb", name=f"Stay {trip}{i}", url="https://www.airbnb.com/rooms/1",
                lat=39.0, lon=21.0)


class Env:
    pass


@pytest.fixture
def env(tmp_path):
    e = Env()
    e.clock = Clock()
    e.path = tmp_path / "trips.db"
    e.trips = TripStore(e.path)
    e.accounts = AccountStore(e.path, now=e.clock)
    users = {}
    for name in ("Nikos", "Maria", "Kostas"):
        e.accounts.signup(name, "password1", "1.1.1.1", emoji_for(name))
        users[name] = User(id=e.accounts.user_id(name), username=name)
    e.nikos, e.maria, e.kostas = users["Nikos"], users["Maria"], users["Kostas"]
    e.ids = {key: e.trips.create(SavedTripIn(name=f"Trip {key}", stays=[stay(key, 1), stay(key, 2)])).id
             for key in "ABC"}
    e.store = RoundStore(e.path, e.trips, e.accounts, now=e.clock)
    return e


def open_round(env, keys="ABC", hours=48, user=None, title="Autumn"):
    return env.store.open(user or env.nikos, RoundIn(title=title, closes_at=env.clock() + timedelta(hours=hours),
                                                     trip_ids=[env.ids[k] for k in keys]))


def by_id(view):
    return {t.trip_id: t for t in view.trips}


# --- open --------------------------------------------------------------------

def test_open_returns_view_in_given_order(env):
    view = open_round(env, keys="CAB")
    assert view.status == "open" and view.title == "Autumn" and view.opener == "Nikos"
    assert [t.trip_id for t in view.trips] == [env.ids["C"], env.ids["A"], env.ids["B"]]
    assert [t.rank for t in view.trips] == [1, 2, 3]
    assert view.winner is None and view.closed_at is None
    assert view.created_at == env.clock()
    assert view.closes_at == env.clock() + timedelta(hours=48)
    assert view.trips[0].name == "Trip C"
    assert [s.stay_id for s in view.trips[0].stays] == ["airbnb:C1", "airbnb:C2"]
    assert view.trips[0].stays[1].name == "Stay C2"
    assert all(t.score == 0 and t.counts == Counts() for t in view.trips)


def test_second_open_rejected(env):
    open_round(env)
    with pytest.raises(RoundAlreadyOpen) as exc:
        open_round(env, user=env.maria)
    assert str(exc.value) == "A round is already open"


def test_deadline_bounds(env):
    now = env.clock()
    for closes in (now + timedelta(minutes=59), now + timedelta(days=30, seconds=1), now - timedelta(hours=1)):
        with pytest.raises(ValueError):
            env.store.open(env.nikos, RoundIn(title="x", closes_at=closes, trip_ids=[env.ids["A"]]))
    env.store.open(env.nikos, RoundIn(title="x", closes_at=now + timedelta(hours=1), trip_ids=[env.ids["A"]]))
    env.store.cancel(env.store.current(None).id, env.nikos)
    env.store.open(env.nikos, RoundIn(title="y", closes_at=now + timedelta(days=30), trip_ids=[env.ids["A"]]))


def test_unknown_trip_rejected(env):
    with pytest.raises(ValueError):
        env.store.open(env.nikos, RoundIn(title="x", closes_at=env.clock() + timedelta(days=1),
                                          trip_ids=[env.ids["A"], "nope"]))
    assert env.store.current(None) is None
    assert env.store.history() == []


def test_round_in_rules():
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert RoundIn(title="  T  ", closes_at=now, trip_ids=["a"]).title == "T"
    assert RoundIn(title="T", closes_at=now, trip_ids=["a", "b", "a"]).trip_ids == ["a", "b"]
    for bad in ({"title": "  "}, {"title": "x" * 81}, {"trip_ids": []}, {"trip_ids": [str(i) for i in range(51)]},
                {"closes_at": "2026-10-12T10:00:00"}):
        with pytest.raises(ValidationError):
            RoundIn(**{"title": "T", "closes_at": now, "trip_ids": ["a"], **bad})


# --- votes -------------------------------------------------------------------

def test_vote_replace_clear(env):
    rid = open_round(env).id
    a = env.ids["A"]
    res = env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="ok", comment="  fine  "))
    assert res.moved_love_from is None
    t = by_id(res.round)[a]
    assert t.my_rating == "ok" and t.counts == Counts(ok=1) and t.score == 1
    assert [v.model_dump() for v in t.votes] == [{"username": "Nikos", "rating": "ok", "comment": "fine"}]
    res = env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="nogo", comment=""))
    t = by_id(res.round)[a]
    assert t.counts == Counts(nogo=1) and t.score == -3
    assert [v.model_dump() for v in t.votes] == [{"username": "Nikos", "rating": "nogo", "comment": None}]
    res = env.store.clear_trip_vote(rid, a, env.nikos)
    t = by_id(res.round)[a]
    assert t.votes == [] and t.my_rating is None and res.moved_love_from is None


def test_comment_limit():
    assert VoteIn(rating="ok", comment="x" * 140).comment == "x" * 140
    assert VoteIn(rating="ok", comment="   ").comment is None
    with pytest.raises(ValidationError):
        VoteIn(rating="ok", comment="x" * 141)
    with pytest.raises(ValidationError):
        VoteIn(rating="meh")


def test_love_moves_between_trips(env):
    rid = open_round(env).id
    a, b = env.ids["A"], env.ids["B"]
    env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="love", comment="best"))
    env.store.vote_trip(rid, a, env.maria, VoteIn(rating="love"))
    res = env.store.vote_trip(rid, b, env.nikos, VoteIn(rating="love"))
    assert res.moved_love_from == a
    trips = by_id(res.round)
    assert trips[a].my_rating == "ok" and trips[b].my_rating == "love"
    assert {v.username: (v.rating, v.comment) for v in trips[a].votes} == {"Nikos": ("ok", "best"),
                                                                         "Maria": ("love", None)}
    assert res.round.me.love_trip_id == b
    # Love again on the same trip moves nothing
    assert env.store.vote_trip(rid, b, env.nikos, VoteIn(rating="love")).moved_love_from is None


def test_love_moves_between_stays_within_trip_only(env):
    rid = open_round(env).id
    a, b = env.ids["A"], env.ids["B"]
    assert env.store.vote_stay(rid, a, "airbnb:A1", env.nikos, VoteIn(rating="love")).moved_love_from is None
    res = env.store.vote_stay(rid, b, "airbnb:B1", env.nikos, VoteIn(rating="love"))
    assert res.moved_love_from is None  # another trip: no move
    res = env.store.vote_stay(rid, a, "airbnb:A2", env.nikos, VoteIn(rating="love"))
    assert res.moved_love_from == "airbnb:A1"
    trips = by_id(res.round)
    stays_a = {s.stay_id: s for s in trips[a].stays}
    assert stays_a["airbnb:A1"].my_rating == "ok" and stays_a["airbnb:A2"].my_rating == "love"
    assert {s.stay_id: s.my_rating for s in trips[b].stays}["airbnb:B1"] == "love"
    assert res.round.me.love_trip_id is None  # stay loves don't touch the trip love
    res = env.store.clear_stay_vote(rid, a, "airbnb:A2", env.nikos)
    assert {s.stay_id: s.my_rating for s in by_id(res.round)[a].stays}["airbnb:A2"] is None


def test_vote_not_in_round(env):
    rid = open_round(env, keys="AB").id
    with pytest.raises(NotInRound):
        env.store.vote_trip(rid, env.ids["C"], env.nikos, VoteIn(rating="ok"))
    with pytest.raises(NotInRound):
        env.store.vote_stay(rid, env.ids["A"], "airbnb:B1", env.nikos, VoteIn(rating="ok"))
    with pytest.raises(NotInRound):
        env.store.vote_stay(rid, env.ids["C"], "airbnb:C1", env.nikos, VoteIn(rating="ok"))
    with pytest.raises(RoundNotFound):
        env.store.vote_trip("nope", env.ids["A"], env.nikos, VoteIn(rating="ok"))
    assert env.store.get("nope", None) is None


# --- me / waiting_for / ranking view ------------------------------------------

def test_me_and_waiting_for(env):
    rid = open_round(env).id
    a, b, c = env.ids["A"], env.ids["B"], env.ids["C"]
    view = env.store.get(rid, env.nikos)
    assert view.me.model_dump() == {"rated": 0, "total": 3, "love_trip_id": None, "joined": True}
    assert view.waiting_for == ["Nikos"]  # only participants: the opener is the only one in so far
    for t in (a, b):
        env.store.vote_trip(rid, t, env.nikos, VoteIn(rating="ok"))
    env.store.vote_stay(rid, c, "airbnb:C1", env.nikos, VoteIn(rating="love"))  # stays don't count
    view = env.store.get(rid, env.nikos)
    assert view.me.model_dump() == {"rated": 2, "total": 3, "love_trip_id": None, "joined": True}
    assert "Nikos" in view.waiting_for
    env.store.vote_trip(rid, c, env.nikos, VoteIn(rating="love"))
    env.store.join(rid, env.kostas)
    for t in (a, b, c):
        env.store.vote_trip(rid, t, env.maria, VoteIn(rating="nogo"))
    view = env.store.get(rid, env.nikos)
    assert view.me.model_dump() == {"rated": 3, "total": 3, "love_trip_id": c, "joined": True}
    assert view.waiting_for == ["Kostas"]


def test_view_ranking_matches_rules_and_viewers(env):
    rid = open_round(env).id
    a, b, c = env.ids["A"], env.ids["B"], env.ids["C"]
    env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="ok"))
    env.store.vote_trip(rid, a, env.maria, VoteIn(rating="nogo", comment="too far"))
    env.store.vote_trip(rid, b, env.maria, VoteIn(rating="ok"))
    env.store.vote_trip(rid, c, env.kostas, VoteIn(rating="ok"))
    env.store.vote_stay(rid, b, "airbnb:B2", env.kostas, VoteIn(rating="love"))
    view = env.store.get(rid, env.maria)
    # b and c tie on score 1 with no nogos/loves -> position; a is -2
    assert [t.trip_id for t in view.trips] == [b, c, a]
    assert [t.rank for t in view.trips] == [1, 2, 3]
    expected = rank(view.trips, lambda t: t.counts, lambda t: [a, b, c].index(t.trip_id))
    assert [t.trip_id for t in expected] == [t.trip_id for t in view.trips]
    trips = by_id(view)
    assert trips[a].score == -2 and trips[a].counts == Counts(ok=1, nogo=1)
    assert trips[a].my_rating == "nogo" and trips[b].my_rating == "ok" and trips[c].my_rating is None
    assert {(v.username, v.rating, v.comment) for v in trips[a].votes} == {("Nikos", "ok", None),
                                                                          ("Maria", "nogo", "too far")}
    assert [s.stay_id for s in trips[b].stays] == ["airbnb:B2", "airbnb:B1"]
    assert [s.rank for s in trips[b].stays] == [1, 2] and trips[b].stays[0].score == 2
    assert by_id(env.store.get(rid, env.nikos))[a].my_rating == "ok"
    anon = env.store.get(rid, None)
    assert anon.me is None
    assert all(t.my_rating is None and all(s.my_rating is None for s in t.stays) for t in anon.trips)


# --- opener actions --------------------------------------------------------------

def test_opener_only(env):
    rid = open_round(env).id
    with pytest.raises(NotOpener) as exc:
        env.store.patch(rid, env.maria, RoundPatch(closes_at=env.clock() + timedelta(days=5)))
    assert str(exc.value) == "Only the opener can do this"
    with pytest.raises(NotOpener):
        env.store.close(rid, env.maria)
    with pytest.raises(NotOpener):
        env.store.cancel(rid, env.maria)
    assert env.store.get(rid, None).status == "open"


def test_extend_rules(env):
    view = open_round(env, hours=48)
    rid = view.id
    for bad in (view.closes_at, view.closes_at - timedelta(hours=1), env.clock() + timedelta(days=30, seconds=1)):
        with pytest.raises(ValueError):
            env.store.patch(rid, env.nikos, RoundPatch(closes_at=bad))
    new = env.clock() + timedelta(days=30)
    assert env.store.patch(rid, env.nikos, RoundPatch(closes_at=new)).closes_at == new


def test_add_trips(env):
    rid = open_round(env, keys="B").id
    a, b, c = env.ids["A"], env.ids["B"], env.ids["C"]
    with pytest.raises(ValueError):
        env.store.patch(rid, env.nikos, RoundPatch(add_trip_ids=[a, "nope"]))
    assert [t.trip_id for t in env.store.get(rid, None).trips] == [b]
    view = env.store.patch(rid, env.nikos, RoundPatch(add_trip_ids=[c, b, a, c]))
    assert [t.trip_id for t in view.trips] == [b, c, a]
    env.store.vote_trip(rid, a, env.maria, VoteIn(rating="ok"))  # added trips are votable


def test_close_now_and_cancel(env):
    rid = open_round(env).id
    env.store.vote_trip(rid, env.ids["B"], env.maria, VoteIn(rating="love"))
    env.clock.advance(hours=1)
    view = env.store.close(rid, env.nikos)
    assert view.status == "closed" and view.closed_at == env.clock()
    assert view.winner.model_dump() == {"trip_id": env.ids["B"], "trip_name": "Trip B", "stay_id": None,
                                        "stay_name": None}
    assert view.waiting_for == []
    with pytest.raises(RoundClosed) as exc:
        env.store.close(rid, env.nikos)
    assert str(exc.value) == "This round is closed"
    with pytest.raises(RoundClosed):
        env.store.patch(rid, env.nikos, RoundPatch(add_trip_ids=[env.ids["A"]]))
    rid2 = open_round(env).id
    view = env.store.cancel(rid2, env.nikos)
    assert view.status == "cancelled" and view.winner is None and view.closed_at == env.clock()
    assert view.waiting_for == []
    with pytest.raises(RoundClosed):
        env.store.vote_trip(rid2, env.ids["A"], env.maria, VoteIn(rating="ok"))
    assert env.store.current(None).id == rid  # cancelled never current


# --- lazy close / frozen result -------------------------------------------------

def test_lazy_close_freezes_result(env):
    view = open_round(env, hours=48)
    rid = view.id
    a, b = env.ids["A"], env.ids["B"]
    env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="love", comment="yes"))
    env.store.vote_trip(rid, a, env.maria, VoteIn(rating="ok"))
    env.store.vote_trip(rid, b, env.maria, VoteIn(rating="love"))
    env.store.vote_stay(rid, a, "airbnb:A2", env.maria, VoteIn(rating="ok"))
    env.clock.advance(hours=48, seconds=1)
    current = env.store.current(env.maria)
    assert current.id == rid and current.status == "closed" and current.closed_at == view.closes_at
    assert current.winner.model_dump() == {"trip_id": a, "trip_name": "Trip A", "stay_id": "airbnb:A2",
                                           "stay_name": "Stay A2"}
    assert current.waiting_for == []
    assert by_id(current)[a].my_rating == "ok" and current.me.love_trip_id == b
    with pytest.raises(RoundClosed):
        env.store.vote_trip(rid, a, env.kostas, VoteIn(rating="ok"))
    with pytest.raises(RoundClosed):
        env.store.clear_trip_vote(rid, a, env.nikos)
    before = env.store.get(rid, env.nikos)
    env.trips.delete(a)
    after = env.store.get(rid, env.nikos)
    assert after == before
    assert by_id(after)[a].my_rating == "love"
    assert after.me.model_dump() == {"rated": 1, "total": 3, "love_trip_id": a, "joined": True}
    anon = env.store.get(rid, None)
    assert anon.me is None and all(t.my_rating is None for t in anon.trips)
    with sqlite3.connect(env.path) as db:
        frozen = db.execute("SELECT result FROM rounds WHERE id = ?", (rid,)).fetchone()[0]
    assert '"me":null' in frozen and '"my_rating":"' not in frozen


def test_vote_after_deadline_settles_first(env):
    rid = open_round(env, hours=2).id
    env.clock.advance(hours=2)  # closes_at == now -> closed
    with pytest.raises(RoundClosed):
        env.store.vote_trip(rid, env.ids["A"], env.nikos, VoteIn(rating="ok"))
    view = env.store.get(rid, None)
    assert view.status == "closed" and view.winner is None


def test_stay_winner_none_when_stays_unvoted(env):
    rid = open_round(env).id
    env.store.vote_trip(rid, env.ids["C"], env.nikos, VoteIn(rating="ok"))
    env.store.vote_stay(rid, env.ids["A"], "airbnb:A1", env.nikos, VoteIn(rating="love"))
    view = env.store.close(rid, env.nikos)
    assert view.winner.trip_id == env.ids["C"] and view.winner.stay_id is None and view.winner.stay_name is None


def test_deleted_trip_and_removed_stay_before_close(env):
    rid = open_round(env).id
    a, b = env.ids["A"], env.ids["B"]
    env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="love"))
    env.store.vote_stay(rid, b, "airbnb:B1", env.nikos, VoteIn(rating="love"))
    env.store.vote_stay(rid, b, "airbnb:B2", env.maria, VoteIn(rating="ok"))
    env.trips.delete(a)
    trip_b = env.trips.get(b)
    env.trips.replace(b, SavedTripIn(name=trip_b.name, stays=[trip_b.stays[1]]))
    view = env.store.get(rid, env.nikos)
    assert a not in by_id(view)
    assert view.me.model_dump() == {"rated": 0, "total": 2, "love_trip_id": None, "joined": True}
    stays_b = by_id(view)[b].stays
    assert [s.stay_id for s in stays_b] == ["airbnb:B2"] and stays_b[0].my_rating is None
    with pytest.raises(NotInRound):
        env.store.vote_trip(rid, a, env.nikos, VoteIn(rating="ok"))
    with pytest.raises(NotInRound):
        env.store.vote_stay(rid, b, "airbnb:B1", env.nikos, VoteIn(rating="ok"))
    view = env.store.close(rid, env.nikos)
    assert view.winner is None  # the only trip vote was on the deleted trip


# --- current / history ------------------------------------------------------------

def test_current_rules(env):
    assert env.store.current(None) is None
    rid = open_round(env).id
    assert env.store.current(None).id == rid
    env.store.close(rid, env.nikos)
    assert env.store.current(None).id == rid
    env.clock.advance(days=6, hours=23)
    assert env.store.current(None).id == rid
    env.clock.advance(hours=1, seconds=1)
    assert env.store.current(None) is None
    rid2 = open_round(env).id
    env.store.cancel(rid2, env.nikos)
    assert env.store.current(None) is None


def test_history_newest_first(env):
    r1 = open_round(env, title="First").id
    env.store.vote_trip(r1, env.ids["B"], env.maria, VoteIn(rating="ok"))
    env.store.close(r1, env.nikos)
    env.clock.advance(minutes=1)
    r2 = open_round(env, title="Second", user=env.maria).id
    env.store.cancel(r2, env.maria)
    env.clock.advance(minutes=1)
    r3 = open_round(env, title="Third").id
    hist = env.store.history()
    assert [h.id for h in hist] == [r3, r2, r1]
    assert [(h.title, h.status, h.opener, h.winner_name) for h in hist] == [
        ("Third", "open", "Nikos", None), ("Second", "cancelled", "Maria", None), ("First", "closed", "Nikos", "Trip B")]
    assert hist[0].closed_at is None and hist[2].closed_at is not None


# --- concurrency ----------------------------------------------------------------------

def test_concurrent_loves_keep_one(env):
    rid = open_round(env).id
    a, b = env.ids["A"], env.ids["B"]
    stores = [RoundStore(env.path, env.trips, env.accounts, now=env.clock) for _ in range(2)]
    start = threading.Barrier(2)
    # Rendezvous after the membership reads, before the Love lookup/writes. Without a write lock taken
    # up front both threads meet here and then both insert a Love; with BEGIN IMMEDIATE the second thread
    # is still waiting for the lock, so the first one's wait times out and the votes run one after another.
    middle = threading.Barrier(2, timeout=0.5)
    errors = []

    for store in stores:
        original = store._trip_in_round

        def hooked(*args, _original=original):
            trip = _original(*args)
            try:
                middle.wait()
            except threading.BrokenBarrierError:
                pass
            return trip

        store._trip_in_round = hooked

    def go(store, trip_id):
        try:
            start.wait()
            store.vote_trip(rid, trip_id, env.nikos, VoteIn(rating="love"))
        except Exception as exc:  # surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(s, t)) for s, t in zip(stores, (a, b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with sqlite3.connect(env.path) as db:
        loves = db.execute("SELECT COUNT(*) FROM trip_votes WHERE round_id = ? AND user_id = ? AND rating = 'love'",
                           (rid, env.nikos.id)).fetchone()[0]
    assert loves == 1


# --- participants -----------------------------------------------------------------

def test_opener_auto_joined_and_join_idempotent(env):
    rid = open_round(env).id
    assert env.store.get(rid, env.maria).me.joined is False
    assert env.store.get(rid, None).me is None
    env.clock.advance(minutes=5)
    view = env.store.join(rid, env.maria)
    assert view.participants == ["Nikos", "Maria"] and view.me.joined is True
    env.clock.advance(minutes=5)
    env.store.join(rid, env.kostas)
    again = env.store.join(rid, env.maria)  # idempotent, keeps its place
    assert again.participants == ["Nikos", "Maria", "Kostas"]
    assert again.waiting_for == ["Kostas", "Maria", "Nikos"]
    assert env.store.get(rid, env.nikos).me.joined is True


def test_any_vote_joins(env):
    rid = open_round(env).id
    env.store.vote_trip(rid, env.ids["A"], env.maria, VoteIn(rating="ok"))
    env.clock.advance(minutes=1)
    env.store.vote_stay(rid, env.ids["A"], "airbnb:A1", env.kostas, VoteIn(rating="love"))
    assert env.store.get(rid, None).participants == ["Nikos", "Maria", "Kostas"]


def test_join_closed_or_cancelled_or_unknown(env):
    rid = open_round(env).id
    env.store.close(rid, env.nikos)
    with pytest.raises(RoundClosed) as exc:
        env.store.join(rid, env.maria)
    assert str(exc.value) == "This round is closed"
    rid2 = open_round(env).id
    env.store.cancel(rid2, env.nikos)
    with pytest.raises(RoundClosed):
        env.store.join(rid2, env.maria)
    with pytest.raises(RoundNotFound):
        env.store.join("nope", env.maria)


def test_participants_union_for_round_without_rows(env):
    """A round from before participants existed: no rows, but opener and voters count."""
    rid = open_round(env).id
    env.store.vote_trip(rid, env.ids["A"], env.maria, VoteIn(rating="ok"))
    env.store.vote_stay(rid, env.ids["B"], "airbnb:B1", env.kostas, VoteIn(rating="ok"))
    with sqlite3.connect(env.path) as db:
        db.execute("DELETE FROM round_participants")
        db.execute("UPDATE trip_votes SET updated_at = '2026-10-11T13:00:00.000000Z'")
        db.execute("UPDATE stay_votes SET updated_at = '2026-10-11T12:30:00.000000Z'")
    view = env.store.get(rid, env.kostas)
    assert view.participants == ["Nikos", "Kostas", "Maria"]  # opener, then first vote time
    assert view.me.joined is True
    assert env.store.get(rid, env.nikos).waiting_for == ["Kostas", "Maria", "Nikos"]
    assert env.store.get(rid, None).participants == ["Nikos", "Kostas", "Maria"]


def test_waiting_for_excludes_non_participants(env):
    rid = open_round(env).id
    view = env.store.get(rid, env.maria)
    assert "Maria" not in view.waiting_for and "Kostas" not in view.waiting_for
    assert view.me.joined is False


def test_frozen_result_keeps_participants_and_recomputes_joined(env):
    rid = open_round(env).id
    env.store.join(rid, env.maria)
    env.store.vote_trip(rid, env.ids["A"], env.maria, VoteIn(rating="love"))
    env.clock.advance(hours=49)
    kostas = env.store.get(rid, env.kostas)
    assert kostas.status == "closed" and kostas.participants == ["Nikos", "Maria"] and kostas.me.joined is False
    assert env.store.get(rid, env.maria).me.joined is True
    with sqlite3.connect(env.path) as db:
        frozen = db.execute("SELECT result FROM rounds WHERE id = ?", (rid,)).fetchone()[0]
    assert '"participants":["Nikos","Maria"]' in frozen
    assert '"joined"' not in frozen  # per-viewer, never frozen


def test_old_frozen_result_without_participants_falls_back_to_voters(env):
    import json
    rid = open_round(env).id
    env.store.vote_trip(rid, env.ids["A"], env.maria, VoteIn(rating="love"))
    env.clock.advance(hours=49)
    env.store.get(rid, None)
    with sqlite3.connect(env.path) as db:
        data = json.loads(db.execute("SELECT result FROM rounds WHERE id = ?", (rid,)).fetchone()[0])
        del data["participants"]
        db.execute("UPDATE rounds SET result = ? WHERE id = ?", (json.dumps(data), rid))
    assert env.store.get(rid, env.maria).participants == ["Nikos", "Maria"]
    assert env.store.get(rid, env.maria).me.joined is True
