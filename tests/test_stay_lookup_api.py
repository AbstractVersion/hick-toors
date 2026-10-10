import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.mcp_tools as tools
from app.config import Settings
from app.main import create_app
from app.models import Stay, StayLookupResult
from app.sources.stays.airbnb import AirbnbParseError, ListingNotFound
from app.sources.stays.base import ProviderUnavailable

LINK = "https://www.airbnb.com/rooms/576031322358400592?adults=4&check_in=2026-11-06&pets=1"


class FakeEngine:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def lookup_airbnb(self, room_id, start, end, guests, dog):
        self.calls.append((room_id, start, end, guests, dog))
        if self.error:
            raise self.error
        stay = Stay(id=f"airbnb:{room_id}", source="airbnb", name="Bezeniko", url="u", lat=37.7, lon=22.2)
        return StayLookupResult(stay=stay, warnings=["Price not found for these dates – you can type it in"])


class FakeAsk:
    def available(self):
        return False


def client(tmp_path, engine):
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")
    return TestClient(create_app(engine=engine, ask_runner=FakeAsk(), settings=settings))


def test_lookup_ok_passes_page_dates_not_link_dates(tmp_path):
    engine = FakeEngine()
    r = client(tmp_path, engine).post("/api/stays/lookup", json={
        "url": LINK, "start": "2026-10-23", "end": "2026-10-25", "guests": 3, "dog": False})
    assert r.status_code == 200
    body = r.json()
    assert body["stay"]["id"] == "airbnb:576031322358400592" and body["stay"]["name"] == "Bezeniko"
    assert body["warnings"] == ["Price not found for these dates – you can type it in"]
    assert engine.calls == [("576031322358400592", date(2026, 10, 23), date(2026, 10, 25), 3, False)]


def test_lookup_defaults(tmp_path):
    engine = FakeEngine()
    assert client(tmp_path, engine).post("/api/stays/lookup", json={"url": "123"}).status_code == 200
    assert engine.calls == [("123", None, None, 2, True)]


@pytest.mark.parametrize("url", ["https://evil.com/rooms/1", "https://www.airbnb.com/s/homes", "hello"])
def test_bad_link_is_422_with_message(tmp_path, url):
    engine = FakeEngine()
    r = client(tmp_path, engine).post("/api/stays/lookup", json={"url": url})
    assert r.status_code == 422 and r.json()["detail"] == "Paste an Airbnb room link (airbnb.com/rooms/…)"
    assert engine.calls == []


@pytest.mark.parametrize("body", [
    {"url": ""}, {"url": "1" * 2001}, {"url": "1", "guests": 0}, {"url": "1", "guests": 17},
    {"url": "1", "start": "2026-10-25", "end": "2026-10-23"},
])
def test_invalid_body_is_422(tmp_path, body):
    assert client(tmp_path, FakeEngine()).post("/api/stays/lookup", json=body).status_code == 422


@pytest.mark.parametrize("error,status,detail", [
    (ListingNotFound("1"), 404, "That Airbnb listing wasn't found (removed or private)"),
    (ProviderUnavailable("Airbnb didn't answer – try again in a minute"), 502,
     "Airbnb didn't answer – try again in a minute"),
    (AirbnbParseError("no coords"), 502, "Airbnb didn't answer – try again in a minute"),
])
def test_lookup_errors(tmp_path, error, status, detail):
    r = client(tmp_path, FakeEngine(error)).post("/api/stays/lookup", json={"url": LINK})
    assert (r.status_code, r.json()["detail"]) == (status, detail)


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(tools, "_engine", fake)
    return fake


async def test_mcp_lookup_airbnb(engine):
    out = json.loads(await tools.lookup_airbnb(LINK, start="2026-10-23", end="2026-10-25", guests=4))
    assert out["stay"]["id"] == "airbnb:576031322358400592" and out["warnings"]
    assert engine.calls == [("576031322358400592", date(2026, 10, 23), date(2026, 10, 25), 4, True)]


async def test_mcp_lookup_airbnb_errors(engine, monkeypatch):
    assert json.loads(await tools.lookup_airbnb("https://evil.com/rooms/1")) == {
        "error": "Paste an Airbnb room link (airbnb.com/rooms/…)"}
    assert "error" in json.loads(await tools.lookup_airbnb(LINK, start="2026-10-25", end="2026-10-23"))
    monkeypatch.setattr(tools, "_engine", FakeEngine(ListingNotFound("1")))
    assert json.loads(await tools.lookup_airbnb(LINK)) == {
        "error": "That Airbnb listing wasn't found (removed or private)"}
    monkeypatch.setattr(tools, "_engine", FakeEngine(ProviderUnavailable("x")))
    assert json.loads(await tools.lookup_airbnb(LINK)) == {"error": "Airbnb didn't answer – try again in a minute"}
