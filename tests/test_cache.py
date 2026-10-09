from app.cache import DiskCache


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
