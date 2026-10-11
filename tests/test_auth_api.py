from fastapi.testclient import TestClient

from app.accounts import AccountStore
from app.config import Settings
from app.main import create_app
from app.models import SavedTrip, SavedTripIn
from app.trips_store import TripStore
from tests.builders import emoji_for, login_client
from tests.test_api import FakeAsk, FakeEngine


def make(tmp_path, accounts=None, base_url="http://testserver"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, cache_dir=tmp_path, trips_db=tmp_path / "trips.db")
    app = create_app(engine=FakeEngine(), ask_runner=FakeAsk(), settings=settings,
                     trip_store=TripStore(tmp_path / "trips.db"), accounts=accounts)
    return TestClient(app, base_url=base_url)


def signup(c, username="Nikos", password="password1", emoji=None):
    return c.post("/api/auth/signup", json={"username": username, "password": password,
                                            "emoji": emoji or emoji_for(username)})


def test_signup_sets_cookie_flags_and_logs_in(tmp_path):
    c = make(tmp_path)
    r = signup(c)
    assert r.status_code == 201 and r.json() == {"username": "Nikos"}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("hick_session=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low and "max-age=2592000" in low
    assert "secure" not in low
    assert c.get("/api/auth/me").json() == {"username": "Nikos", "emoji": emoji_for("Nikos")}


def test_secure_follows_the_request_scheme_only(tmp_path):
    c = make(tmp_path / "a", base_url="https://testserver")
    assert "secure" in signup(c).headers["set-cookie"].lower()
    c = make(tmp_path / "b", base_url="http://testserver")
    r = c.post("/api/auth/signup", json={"username": "Eve", "password": "password1", "emoji": emoji_for("Eve")},
               headers={"X-Forwarded-Proto": "https"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_me_logout_and_old_cookie_dies(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/auth/me").json() == {"username": None, "emoji": None}
    signup(c)
    token = c.cookies.get("hick_session")
    r = c.post("/api/auth/logout")
    assert r.status_code == 204 and r.content == b""
    assert c.get("/api/auth/me").json() == {"username": None, "emoji": None}
    c.cookies.set("hick_session", token)
    assert c.get("/api/auth/me").json() == {"username": None, "emoji": None}
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
    assert c.get("/api/auth/me").json() == {"username": "Nikos", "emoji": emoji_for("Nikos")}


def test_signup_errors(tmp_path):
    c = make(tmp_path)
    signup(c)
    r = signup(c, "nikos")
    assert r.status_code == 409 and r.json() == {"detail": "That username is taken"}
    r = signup(c, "a b", "password1")
    assert r.status_code == 422 and r.json() == {"detail": "Username must be 3-20 letters, digits, _ . -"}
    r = signup(c, "Maria", "")
    assert r.status_code == 422 and r.json() == {"detail": "Password must be 1-200 characters"}
    assert signup(c, "Eleni", "e").status_code == 201  # a 1-character password is fine
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": "eleni", "password": "e"}).status_code == 200
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
    r = c.post("/api/auth/login", json={"username": "nikos", "password": secret, "emoji": "🤡"})
    assert r.status_code == 422 and "s3cret" not in r.text
    r = c.post("/api/auth/signup", json={"username": "nikos", "password": secret, "emoji": "🤡"})
    assert r.status_code == 422 and "s3cret" not in r.text


def test_sessions_swept_at_startup(tmp_path):
    accounts = AccountStore(tmp_path / "a.db")
    called = []
    accounts.sweep_sessions = lambda: called.append(1) or 0
    with make(tmp_path, accounts=accounts):
        pass
    assert called


def test_login_ip_limit_is_429(tmp_path):
    c = make(tmp_path, accounts=AccountStore(tmp_path / "a.db", logins_per_ip_window=2))
    for name in ("aaa", "bbb"):  # distinct usernames: only the per-IP limit can trip
        assert c.post("/api/auth/login", json={"username": name, "password": "wrongwrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "ccc", "password": "wrongwrong"})
    assert r.status_code == 429 and r.json() == {"detail": "Too many attempts – wait a minute"}


# --- emojis -------------------------------------------------------------

def test_signup_emoji_rules(tmp_path):
    c = make(tmp_path)
    for body in ({"emoji": "nope"}, {"emoji": "🇬🇷"}, {"emoji": ""}):
        r = c.post("/api/auth/signup", json={"username": "Nikos", "password": "password1", **body})
        assert r.status_code == 422 and r.json() == {"detail": "Pick an emoji from the list"}
    assert c.post("/api/auth/signup", json={"username": "Nikos", "password": "password1"}).status_code == 422
    assert c.get("/api/auth/me").json() == {"username": None, "emoji": None}
    assert signup(c, "Nikos", emoji="🌶️").status_code == 201
    assert c.get("/api/auth/me").json() == {"username": "Nikos", "emoji": "🌶️"}
    other = make(tmp_path / "x", accounts=c.app.state.accounts)
    r = signup(other, "Maria", emoji="🌶️")
    assert r.status_code == 409 and r.json() == {"detail": "That emoji is taken – pick another"}
    assert other.get("/api/auth/me").json() == {"username": None, "emoji": None}


def test_signup_when_every_emoji_is_taken(tmp_path, monkeypatch):
    import app.accounts as accounts_mod
    monkeypatch.setattr(accounts_mod, "_scrypt", lambda *a: b"x" * 32)
    accounts = AccountStore(tmp_path / "a.db", signups_per_ip_hour=1000)
    for i, e in enumerate(accounts_mod.EMOJIS):
        accounts.signup(f"user{i}", "pw", "9.9.9.9", e)
    c = make(tmp_path, accounts=accounts)
    r = signup(c, "Late", emoji="🤡")
    assert r.status_code == 409 and r.json() == {"detail": "All emojis are taken"}


def test_emojis_and_users_are_public(tmp_path):
    from app.accounts import EMOJIS
    c = make(tmp_path)
    assert c.get("/api/auth/emojis").json() == {"emojis": list(EMOJIS), "taken": []}
    assert c.get("/api/users").json() == []
    signup(c, "maria", emoji="🍆")
    c.post("/api/auth/logout")
    signup(c, "Anna", emoji="🤡")
    c.post("/api/auth/logout")
    assert c.get("/api/auth/emojis").json()["taken"] == ["🤡", "🍆"]
    assert c.get("/api/users").json() == [{"username": "Anna", "emoji": "🤡"}, {"username": "maria", "emoji": "🍆"}]


def test_change_emoji(tmp_path):
    c = make(tmp_path)
    assert c.put("/api/auth/emoji", json={"emoji": "🤡"}).status_code == 401
    signup(c, "maria", emoji="🍆")
    c.post("/api/auth/logout")
    signup(c, "Nikos", emoji="🤡")
    assert c.put("/api/auth/emoji", json={"emoji": "🤡"}).json() == {"username": "Nikos", "emoji": "🤡"}
    r = c.put("/api/auth/emoji", json={"emoji": "🍆"})
    assert r.status_code == 409 and r.json() == {"detail": "That emoji is taken – pick another"}
    r = c.put("/api/auth/emoji", json={"emoji": "x"})
    assert r.status_code == 422 and r.json() == {"detail": "Pick an emoji from the list"}
    assert c.put("/api/auth/emoji", json={"emoji": "☠️"}).status_code == 200
    assert c.get("/api/auth/me").json() == {"username": "Nikos", "emoji": "☠️"}
    assert c.get("/api/auth/emojis").json()["taken"] == ["🍆", "☠️"]


def test_old_account_without_emoji(tmp_path):
    import sqlite3
    accounts = AccountStore(tmp_path / "a.db")
    with sqlite3.connect(tmp_path / "a.db") as db:
        db.execute("INSERT INTO users (username, username_key, pw_hash, created_at) "
                   "VALUES ('Old', 'old', ?, 'x')", (__import__("app.accounts").accounts.hash_password("pw"),))
    c = make(tmp_path, accounts=accounts)
    assert c.post("/api/auth/login", json={"username": "old", "password": "pw"}).status_code == 200
    assert c.get("/api/auth/me").json() == {"username": "Old", "emoji": None}
    assert c.get("/api/users").json() == [{"username": "Old", "emoji": None}]
    assert c.put("/api/auth/emoji", json={"emoji": "🌶️"}).status_code == 200
    assert c.get("/api/auth/me").json()["emoji"] == "🌶️"
