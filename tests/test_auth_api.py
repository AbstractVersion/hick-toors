from fastapi.testclient import TestClient

from app.accounts import AccountStore
from app.config import Settings
from app.main import create_app
from app.models import SavedTrip, SavedTripIn
from app.trips_store import TripStore
from tests.builders import login_client
from tests.test_api import FakeAsk, FakeEngine


def make(tmp_path, accounts=None, base_url="http://testserver"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")
    app = create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings,
                     trip_store=TripStore(tmp_path / "trips.db"), accounts=accounts)
    return TestClient(app, base_url=base_url)


def signup(c, username="Nikos", password="password1"):
    return c.post("/api/auth/signup", json={"username": username, "password": password})


def test_signup_sets_cookie_flags_and_logs_in(tmp_path):
    c = make(tmp_path)
    r = signup(c)
    assert r.status_code == 201 and r.json() == {"username": "Nikos"}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("hick_session=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low and "max-age=2592000" in low
    assert "secure" not in low
    assert c.get("/api/auth/me").json() == {"username": "Nikos"}


def test_secure_follows_the_request_scheme_only(tmp_path):
    c = make(tmp_path / "a", base_url="https://testserver")
    assert "secure" in signup(c).headers["set-cookie"].lower()
    c = make(tmp_path / "b", base_url="http://testserver")
    r = c.post("/api/auth/signup", json={"username": "Eve", "password": "password1"},
               headers={"X-Forwarded-Proto": "https"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_me_logout_and_old_cookie_dies(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/auth/me").json() == {"username": None}
    signup(c)
    token = c.cookies.get("hick_session")
    r = c.post("/api/auth/logout")
    assert r.status_code == 204 and r.content == b""
    assert c.get("/api/auth/me").json() == {"username": None}
    c.cookies.set("hick_session", token)
    assert c.get("/api/auth/me").json() == {"username": None}
    assert c.post("/api/auth/logout").status_code == 204  # logging out when logged out is fine


def test_login_wrong_then_locked(tmp_path):
    c = make(tmp_path)
    signup(c)
    c.post("/api/auth/logout")
    for _ in range(5):
        r = c.post("/api/auth/login", json={"username": "nikos", "password": "wrongwrong"})
        assert r.status_code == 401 and r.json() == {"detail": "Wrong username or password"}
    r = c.post("/api/auth/login", json={"username": "nikos", "password": "password1"})
    assert r.status_code == 429 and r.json() == {"detail": "Too many attempts – wait a minute"}


def test_login_ok_sets_cookie(tmp_path):
    c = make(tmp_path)
    signup(c)
    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"username": "NIKOS", "password": "password1"})
    assert r.status_code == 200 and r.json() == {"username": "Nikos"}
    assert "hick_session=" in r.headers["set-cookie"]
    assert c.get("/api/auth/me").json() == {"username": "Nikos"}


def test_signup_errors(tmp_path):
    c = make(tmp_path)
    signup(c)
    r = signup(c, "nikos")
    assert r.status_code == 409 and r.json() == {"detail": "That username is taken"}
    r = signup(c, "a b", "password1")
    assert r.status_code == 422 and r.json() == {"detail": "Username must be 3-20 letters, digits, _ . -"}
    r = signup(c, "Maria", "short")
    assert r.status_code == 422 and r.json() == {"detail": "Password must be 8-200 characters"}
    assert signup(c, "x" * 21).status_code == 422
    assert signup(c, "Maria", "p" * 201).status_code == 422  # never reaches scrypt
    assert c.post("/api/auth/signup", json={"username": "Maria"}).status_code == 422


def test_signup_ip_limit_and_account_cap(tmp_path):
    c = make(tmp_path / "a", accounts=AccountStore(tmp_path / "a.db", signups_per_ip_hour=2))
    assert signup(c, "aaa").status_code == 201 and signup(c, "bbb").status_code == 201
    assert signup(c, "ccc").status_code == 429
    c = make(tmp_path / "b", accounts=AccountStore(tmp_path / "b.db", max_users=1))
    assert signup(c, "aaa").status_code == 201
    r = signup(c, "bbb")
    assert r.status_code == 409 and r.json() == {"detail": "No more accounts can be created"}


def test_bodies_never_hold_password_or_token(tmp_path):
    c = make(tmp_path)
    r = signup(c)
    token = c.cookies.get("hick_session")
    assert token and "password1" not in r.text and token not in r.text
    r = c.post("/api/auth/login", json={"username": "Nikos", "password": "password1"})
    token = c.cookies.get("hick_session")
    assert "password1" not in r.text and token not in r.text


def test_protected_routes_need_login_and_reads_stay_open(tmp_path):
    c = make(tmp_path)
    tid = TripStore(tmp_path / "trips.db").create(SavedTripIn(name="T")).id
    calls = [c.post("/api/saved-trips", json={"name": "x"}),
             c.put(f"/api/saved-trips/{tid}", json={"name": "x"}),
             c.delete(f"/api/saved-trips/{tid}"),
             c.post(f"/api/saved-trips/{tid}/items", json={}),
             c.post(f"/api/saved-trips/{tid}/nearby-sights")]
    for r in calls:
        assert r.status_code == 401 and r.json() == {"detail": "Log in first"}
    assert c.get("/api/saved-trips").status_code == 200
    assert c.get(f"/api/saved-trips/{tid}").status_code == 200
    assert c.get(f"/api/saved-trips/{tid}/export.md").status_code == 200
    assert c.get(f"/api/saved-trips/{tid}/print").status_code == 200
    assert c.post("/api/stays/lookup", json={}).status_code == 422  # validation, not the login gate


def test_created_by_is_server_set_and_kept(tmp_path):
    c = login_client(make(tmp_path), "Nikos")
    r = c.post("/api/saved-trips", json={"name": "T", "created_by": "eve"})
    assert r.status_code == 201 and r.json()["created_by"] == "Nikos"
    tid = r.json()["id"]
    assert c.get("/api/saved-trips").json()[0]["created_by"] == "Nikos"
    r = c.put(f"/api/saved-trips/{tid}", json={"name": "T2", "created_by": "eve"})
    assert r.json()["created_by"] == "Nikos"
    other = login_client(TestClient(c.app), "Maria")
    r = other.put(f"/api/saved-trips/{tid}", json={"name": "T3"})
    assert r.status_code == 200 and r.json()["created_by"] == "Nikos"
    assert c.get(f"/api/saved-trips/{tid}").json()["created_by"] == "Nikos"


def test_old_trip_json_loads_with_no_creator():
    doc = '{"name":"Old","id":"1","created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}'
    assert SavedTrip.model_validate_json(doc).created_by is None


def test_validation_errors_never_echo_the_password(tmp_path):
    c = make(tmp_path)
    secret = "s3cret-" + "x" * 200
    r = c.post("/api/auth/signup", json={"password": "hunter2hunter2"})
    assert r.status_code == 422 and "hunter2hunter2" not in r.text
    r = c.post("/api/auth/login", json={"username": "nikos", "password": secret})
    assert r.status_code == 422 and "s3cret" not in r.text
    r = c.post("/api/auth/signup", json={"username": "nikos", "password": secret})
    assert r.status_code == 422 and "s3cret" not in r.text


def test_sessions_swept_at_startup(tmp_path):
    accounts = AccountStore(tmp_path / "a.db")
    called = []
    accounts.sweep_sessions = lambda: called.append(1) or 0
    with make(tmp_path, accounts=accounts):
        pass
    assert called
