import asyncio
import os
import time

from app.cache import DAY, DiskCache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_set_get_roundtrip(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("k", {"a": [1, 2]}, ttl_seconds=60)
    assert cache.get("k") == {"a": [1, 2]}


def test_missing_key_returns_none(tmp_path):
    assert DiskCache(tmp_path).get("nope") is None


def test_expired_entry_returns_none(tmp_path):
    clock = Clock()
    cache = DiskCache(tmp_path, clock=clock)
    cache.set("k", 1, ttl_seconds=10)
    clock.now += 11
    assert cache.get("k") is None


def test_corrupt_file_returns_none(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("k", 1, ttl_seconds=10)
    for f in tmp_path.iterdir():
        f.write_text("{not json")
    assert cache.get("k") is None


def test_creates_directory(tmp_path):
    DiskCache(tmp_path / "nested" / "dir").set("k", 1, 10)
    assert (tmp_path / "nested" / "dir").is_dir()


# --- sweep ---------------------------------------------------------------------------------------------


def _age(path, clock, seconds):
    os.utime(path, (clock() - seconds, clock() - seconds))


def test_sweep_removes_expired_old_corrupt_and_stray_tmp(tmp_path):
    clock = Clock()
    clock.now = time.time()
    cache = DiskCache(tmp_path, clock=clock)
    cache.set("expired", 1, 10)
    cache.set("fresh", 2, 100 * DAY)
    cache.set("old", 3, 100 * DAY)
    _age(cache._path("old"), clock, 32 * DAY)  # longer than any TTL (geocoding: 30 days)
    cache.set("geocode", 4, 30 * DAY)
    _age(cache._path("geocode"), clock, 20 * DAY)  # long-lived entries are kept while unexpired
    (tmp_path / "corrupt.json").write_text("{not json")
    stray = tmp_path / "abc.123.tmp"
    stray.write_text("x")
    _age(stray, clock, 2 * 3600)
    writing = tmp_path / "def.456.tmp"  # another process may be writing it right now
    writing.write_text("x")
    clock.now += 11
    assert cache.sweep() == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [cache._path("fresh").name, cache._path("geocode").name, writing.name])
    assert cache.get("fresh") == 2


def test_sweep_on_empty_dir_is_quiet(tmp_path):
    assert DiskCache(tmp_path).sweep() == 0


async def test_sweep_loop_logs_errors_and_repeats(capsys):
    from app.main import sweep_cache_daily

    calls = []

    class Broken:
        def sweep(self):
            calls.append(1)
            raise OSError("disk gone")

    task = asyncio.create_task(sweep_cache_daily(Broken(), interval=0.01))
    for _ in range(100):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert len(calls) >= 2 and "disk gone" in capsys.readouterr().err


def test_app_sweeps_cache_at_startup(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    cache = DiskCache(tmp_path / "cache")
    cache.set("expired", 1, -1)

    class Engine:
        pass

    engine = Engine()
    engine.cache = cache
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", trips_db=tmp_path / "trips.db")
    with TestClient(create_app(engine=engine, ask_runner=object(), settings=settings)):
        for _ in range(200):
            if not list((tmp_path / "cache").iterdir()):
                break
            time.sleep(0.01)
        assert list((tmp_path / "cache").iterdir()) == []
