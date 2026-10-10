import json
import sqlite3
from datetime import timedelta, timezone

import pytest

from app.models import MAX_TRIP_BYTES, Hike, SavedTripIn, Stay, TripItemAdd
from app.trips_store import (MAX_ITEMS, MAX_TRIPS, TooManyTrips, TripConflict, TripFull, TripStore,
                             TripTooLarge)


def hike(i):
    return Hike(id=f"wikiloc:{i}", name=f"Hike {i}", url="https://es.wikiloc.com/x", lat=39.0, lon=21.0)


def stay(i):
    return Stay(id=f"airbnb:{i}", source="airbnb", name=f"Stay {i}", url="https://www.airbnb.com/rooms/1",
                lat=39.0, lon=21.0)


@pytest.fixture
def store(tmp_path):
    return TripStore(tmp_path / "sub" / "trips.db")


def test_creates_parent_dir_and_starts_empty(tmp_path, store):
    assert (tmp_path / "sub" / "trips.db").exists()
    assert store.list_trips() == []


def test_create_get_roundtrip(store):
    trip = store.create(SavedTripIn(name="Zagori", start="2026-10-23", end="2026-10-25",
                                    hikes=[hike(1)], stays=[stay(1)], source="suggested", score=77.5))
    assert len(trip.id) == 32
    assert trip.created_at == trip.updated_at
    assert trip.created_at.tzinfo is not None
    got = store.get(trip.id)
    assert got == trip
    assert store.get("missing") is None


def test_list_newest_first_with_counts(store):
    a = store.create(SavedTripIn(name="A", hikes=[hike(1), hike(2)]))
    b = store.create(SavedTripIn(name="B", stays=[stay(1)]))
    summaries = store.list_trips()
    assert [s.name for s in summaries] == ["B", "A"]
    assert (summaries[1].hike_count, summaries[1].stay_count) == (2, 0)
    assert (summaries[0].hike_count, summaries[0].stay_count) == (0, 1)
    store.replace(a.id, SavedTripIn(name="A2"))
    assert [s.name for s in store.list_trips()] == ["A2", "B"]
    assert b.id in {s.id for s in store.list_trips()}


def test_replace_keeps_id_and_created_at(store):
    trip = store.create(SavedTripIn(name="Old", hikes=[hike(1), hike(2)]))
    new = store.replace(trip.id, SavedTripIn(name="New", notes="n", hikes=[hike(2), hike(1)]))
    assert (new.id, new.created_at, new.name, new.notes) == (trip.id, trip.created_at, "New", "n")
    assert new.updated_at > trip.updated_at
    assert [h.id for h in store.get(trip.id).hikes] == ["wikiloc:2", "wikiloc:1"]
    assert store.replace("missing", SavedTripIn(name="x")) is None


def test_delete(store):
    trip = store.create(SavedTripIn(name="T"))
    assert store.delete(trip.id) is True
    assert store.get(trip.id) is None
    assert store.delete(trip.id) is False


def test_add_item_appends_and_dedupes(store):
    trip = store.create(SavedTripIn(name="T", hikes=[hike(1)]))
    after = store.add_item(trip.id, TripItemAdd(hike=hike(2)))
    assert [h.id for h in after.hikes] == ["wikiloc:1", "wikiloc:2"]
    same = store.add_item(trip.id, TripItemAdd(hike=hike(1)))
    assert [h.id for h in same.hikes] == ["wikiloc:1", "wikiloc:2"]
    with_stay = store.add_item(trip.id, TripItemAdd(stay=stay(1)))
    assert [s.id for s in with_stay.stays] == ["airbnb:1"]
    assert store.add_item("missing", TripItemAdd(stay=stay(1))) is None


def test_add_item_full_raises_and_keeps_trip(store):
    trip = store.create(SavedTripIn(name="T", stays=[stay(i) for i in range(MAX_ITEMS)]))
    with pytest.raises(TripFull):
        store.add_item(trip.id, TripItemAdd(stay=stay(999)))
    assert len(store.get(trip.id).stays) == MAX_ITEMS
    added = store.add_item(trip.id, TripItemAdd(hike=hike(1)))  # hikes have their own limit
    assert [h.id for h in added.hikes] == ["wikiloc:1"]


def test_concurrent_add_item_loses_nothing(store):
    from concurrent.futures import ThreadPoolExecutor

    trip = store.create(SavedTripIn(name="T"))
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda i: store.add_item(trip.id, TripItemAdd(hike=hike(i))), range(10)))
    assert {h.id for h in store.get(trip.id).hikes} == {f"wikiloc:{i}" for i in range(10)}


def _insert_raw(store, trip_id, doc, updated_at="2026-10-10T00:00:00.000000Z"):
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO trips (id, doc, updated_at) VALUES (?, ?, ?)", (trip_id, doc, updated_at))


def test_corrupt_rows_are_skipped_not_fatal(store, capsys):
    good = store.create(SavedTripIn(name="Good", hikes=[hike(1)]))
    poisoned = json.loads(good.model_dump_json()) | {"id": "bad1", "name": "Bad"}
    poisoned["hikes"][0]["lat"] = None  # what a stored NaN turns into
    _insert_raw(store, "bad1", json.dumps(poisoned), "2099-01-01T00:00:00.000000Z")
    _insert_raw(store, "bad2", "{not json")
    assert [s.id for s in store.list_trips()] == [good.id]
    err = capsys.readouterr().err
    assert "bad1" in err and "bad2" in err and "ValidationError" in err
    assert len(err.strip().splitlines()) == 2
    assert store.get("bad1") is None and store.get("bad2") is None
    assert store.replace("bad1", SavedTripIn(name="x")) is None
    assert store.add_item("bad1", TripItemAdd(hike=hike(2))) is None
    assert store.delete("bad1") is True and store.delete("bad2") is True
    assert store.get(good.id) == good


def test_add_item_cannot_grow_trip_past_size_cap(store):
    def fat(i):
        return hike(i).model_copy(update={"name": "x" * 6000})

    trip = store.create(SavedTripIn(name="T", hikes=[fat(i) for i in range(40)]))
    before = store.get(trip.id)
    for i in range(40, 50):
        try:
            store.add_item(trip.id, TripItemAdd(hike=fat(i)))
        except TripTooLarge:
            break
    else:
        pytest.fail("TripTooLarge not raised")
    after = store.get(trip.id)
    assert len(after.model_dump_json(include=set(SavedTripIn.model_fields)).encode()) <= MAX_TRIP_BYTES
    assert len(after.hikes) < 50 and len(after.hikes) >= len(before.hikes)
    with pytest.raises(TripTooLarge):  # still too large: nothing stored
        store.add_item(trip.id, TripItemAdd(hike=fat(99)))
    assert store.get(trip.id) == after


def test_create_caps_number_of_trips(tmp_path):
    small = TripStore(tmp_path / "trips.db", max_trips=3)
    trips = [small.create(SavedTripIn(name=f"T{i}")) for i in range(3)]
    with pytest.raises(TooManyTrips):
        small.create(SavedTripIn(name="one too many"))
    assert len(small.list_trips()) == 3
    small.delete(trips[0].id)
    assert small.create(SavedTripIn(name="room again")).name == "room again"
    assert MAX_TRIPS == 500


def test_replace_with_expected_updated_at(store):
    trip = store.create(SavedTripIn(name="T"))
    same_moment = trip.updated_at.astimezone(timezone(timedelta(hours=3)))  # equal datetime, other tz
    first = store.replace(trip.id, SavedTripIn(name="A"), expected_updated_at=same_moment)
    assert first.name == "A"
    with pytest.raises(TripConflict):  # stale: still holds the pre-edit timestamp
        store.replace(trip.id, SavedTripIn(name="B"), expected_updated_at=trip.updated_at)
    assert store.get(trip.id).name == "A"
    assert store.replace(trip.id, SavedTripIn(name="C"), expected_updated_at=first.updated_at).name == "C"
    assert store.replace(trip.id, SavedTripIn(name="D")).name == "D"  # no expectation: last write wins
    assert store.replace("missing", SavedTripIn(name="x"), expected_updated_at=trip.updated_at) is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "trips.db"
    trip = TripStore(path).create(SavedTripIn(name="Kept", hikes=[hike(1)]))
    assert TripStore(path).get(trip.id).name == "Kept"


def sight(i, **kw):
    from app.models import Sight
    return Sight(id=f"osm:node/{i}", name=f"S{i}", kind="spring", lat=38.0, lon=22.0,
                 url=f"https://www.openstreetmap.org/node/{i}", **kw)


def test_add_sight_appends_and_dedupes(store):
    trip = store.create(SavedTripIn(name="T"))
    added = store.add_item(trip.id, TripItemAdd(sight=sight(1, lines=[[[1.0, 2.0], [3.0, 4.0]]])))
    assert [s.id for s in added.sights] == ["osm:node/1"]
    again = store.add_item(trip.id, TripItemAdd(sight=sight(1)))
    assert len(again.sights) == 1 and again.updated_at == added.updated_at


def test_add_sight_full_raises_sights_kind(store):
    trip = store.create(SavedTripIn(name="T", sights=[sight(i) for i in range(100)]))
    with pytest.raises(TripFull) as exc:
        store.add_item(trip.id, TripItemAdd(sight=sight(999)))
    assert exc.value.kind == "sights"
    assert len(store.get(trip.id).sights) == 100
    full_hikes = store.create(SavedTripIn(name="H", hikes=[hike(i) for i in range(MAX_ITEMS)]))
    with pytest.raises(TripFull) as exc:
        store.add_item(full_hikes.id, TripItemAdd(hike=hike(999)))
    assert exc.value.kind == "hikes"


def test_list_counts_sights(store):
    store.create(SavedTripIn(name="N", sights=[sight(1), sight(2)]))
    store.create(SavedTripIn(name="H", hikes=[hike(1)]))
    counts = {s.name: s.sight_count for s in store.list_trips()}
    assert counts == {"N": 2, "H": 0}


# --- add_sights (🌿 Find nearby sights) ---------------------------------------------------------------


def test_add_sights_appends_in_order_dedupes_and_drops_lines(store):
    trip = store.create(SavedTripIn(name="T", sights=[sight(1)]))
    line = [[[38.0, 22.0], [38.1, 22.1]]]
    trip2, added, stopped = store.add_sights(trip.id, [sight(3, lines=line), sight(1), sight(2), sight(3)])
    assert [s.id for s in trip2.sights] == ["osm:node/1", "osm:node/3", "osm:node/2"]
    assert added == 2 and stopped is None
    assert all(s.lines == [] for s in trip2.sights)
    assert store.get(trip.id) == trip2 and trip2.updated_at > trip.updated_at


def test_add_sights_nothing_new_keeps_updated_at(store):
    trip = store.create(SavedTripIn(name="T", sights=[sight(1)]))
    same, added, stopped = store.add_sights(trip.id, [sight(1)])
    assert added == 0 and stopped is None and same.updated_at == trip.updated_at
    empty, added, _ = store.add_sights(trip.id, [])
    assert added == 0 and store.get(trip.id).updated_at == trip.updated_at


def test_add_sights_stops_at_100(store):
    trip = store.create(SavedTripIn(name="T", sights=[sight(i) for i in range(98)]))
    full, added, stopped = store.add_sights(trip.id, [sight(i) for i in range(95, 105)])
    assert added == 2 and stopped == "sights" and len(full.sights) == 100
    assert [s.id for s in full.sights[-2:]] == ["osm:node/98", "osm:node/99"]
    again, added, stopped = store.add_sights(trip.id, [sight(200)])
    assert added == 0 and stopped == "sights" and again.updated_at == full.updated_at
    _, added, stopped = store.add_sights(trip.id, [sight(5)])  # only already-present ones: nothing stopped
    assert added == 0 and stopped is None


def test_add_sights_respects_size_cap(store):
    trip = store.create(SavedTripIn(name="T"))
    big = [sight(i, wikipedia="x" * 4000) for i in range(100)]  # ~4 KB each: 100 would exceed 256 KB
    grown, added, stopped = store.add_sights(trip.id, big)
    assert 0 < added < 100 and stopped == "size"
    assert len(grown.model_dump_json()) <= MAX_TRIP_BYTES + 200
    from app.models import trip_size
    assert trip_size(grown) <= MAX_TRIP_BYTES
    assert [s.id for s in grown.sights] == [s.id for s in big[:added]]


def test_add_sights_missing_trip(store):
    assert store.add_sights("missing", [sight(1)]) is None
