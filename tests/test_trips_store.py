import pytest

from app.models import Hike, SavedTripIn, Stay, TripItemAdd
from app.trips_store import MAX_ITEMS, TripFull, TripStore


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


def test_persists_across_instances(tmp_path):
    path = tmp_path / "trips.db"
    trip = TripStore(path).create(SavedTripIn(name="Kept", hikes=[hike(1)]))
    assert TripStore(path).get(trip.id).name == "Kept"
