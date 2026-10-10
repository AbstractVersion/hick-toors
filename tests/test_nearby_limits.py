import pytest

from app.nearby_limits import NearbyBusy, NearbyLimits


def test_limits_per_trip_cooldown_and_global():
    now = {"t": 0.0}
    limits = NearbyLimits(clock=lambda: now["t"])
    with limits.slot("a"):
        with pytest.raises(NearbyBusy, match="already running"):
            with limits.slot("a"):
                pass
        with limits.slot("b"):
            with pytest.raises(NearbyBusy, match="Too many searches"):
                with limits.slot("c"):
                    pass
    assert limits.running == set()
    with pytest.raises(NearbyBusy, match="Wait a moment"):
        with limits.slot("a"):
            pass
    now["t"] = 30.0
    with limits.slot("a"):
        pass
    with pytest.raises(RuntimeError):  # a failed search still frees its slot
        with limits.slot("z"):
            raise RuntimeError("boom")
    assert limits.running == set()
