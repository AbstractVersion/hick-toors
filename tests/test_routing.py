from app.cache import DiskCache
from app.models import LatLon
from app.routing import Router

ATHENS = LatLon(lat=37.9838, lon=23.7275)
PELION = LatLon(lat=39.39, lon=22.99)
CRETE = LatLon(lat=35.30, lon=23.95)


async def test_ors_matrix_minutes_and_unreachable(tmp_path):
    calls = []

    async def post(url, body, headers):
        calls.append((body, headers))
        return {"durations": [[12600.0, None]]}

    router = Router(DiskCache(tmp_path), api_key="KEY", post=post)
    times, warning = await router.drive_times(ATHENS, [PELION, CRETE])
    assert warning is None
    assert (times[0].minutes, times[0].estimated, times[0].reachable) == (210, False, True)
    assert (times[1].minutes, times[1].reachable) == (None, False)
    body, headers = calls[0]
    assert headers["Authorization"] == "KEY"
    assert body["locations"][0] == [23.7275, 37.9838]
    assert body["sources"] == [0] and body["destinations"] == [1, 2]


async def test_results_are_cached(tmp_path):
    calls = []

    async def post(url, body, headers):
        calls.append(body)
        return {"durations": [[600.0]]}

    router = Router(DiskCache(tmp_path), api_key="KEY", post=post)
    await router.drive_times(ATHENS, [PELION])
    times, _ = await router.drive_times(ATHENS, [PELION])
    assert len(calls) == 1 and times[0].minutes == 10


async def test_no_key_estimates_without_warning(tmp_path):
    times, warning = await Router(DiskCache(tmp_path), api_key=None).drive_times(ATHENS, [PELION])
    assert warning is None
    assert times[0].estimated is True and times[0].reachable is True
    assert 150 <= times[0].minutes <= 190  # ~170 km straight line at 60 km/h


async def test_ors_failure_falls_back_with_warning(tmp_path):
    async def post(url, body, headers):
        raise ConnectionError("down")

    times, warning = await Router(DiskCache(tmp_path), api_key="KEY", post=post).drive_times(ATHENS, [PELION])
    assert "estimated" in warning
    assert times[0].estimated is True


async def test_empty_destinations(tmp_path):
    assert await Router(DiskCache(tmp_path), api_key="KEY").drive_times(ATHENS, []) == ([], None)
