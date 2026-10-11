import hashlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.accounts as accounts_mod

from app.accounts import (AccountStore, BadCredentials, LoginLocked, SignupRateLimited, TooManyAccounts,
                          UsernameTaken, hash_password, main, verify_password)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return AccountStore(tmp_path / "trips.db", now=clock)


def test_hash_format_and_salt():
    h = hash_password("secret123")
    assert h.count("$") == 5 and h.startswith("scrypt$16384$8$1$")
    assert h != hash_password("secret123")
    assert verify_password("secret123", h) is True
    assert verify_password("secret124", h) is False


@pytest.mark.parametrize("bad", ["", "nope", "scrypt$x$8$1$aa$bb", "scrypt$16384$8$1$!!$!!", "scrypt$16384$8$1$aa"])
def test_verify_malformed_is_false(bad):
    assert verify_password("secret123", bad) is False


def test_signup_login_case_insensitive(store):
    assert store.signup("Nikos", "password1", "1.1.1.1") == "Nikos"
    with pytest.raises(UsernameTaken):
        store.signup("nikos", "password1", "1.1.1.1")
    assert store.login("NIKOS", "password1") == "Nikos"


@pytest.mark.parametrize("name", ["ab", "a" * 21, "bad name", "νίκος"])
def test_bad_usernames(store, name):
    with pytest.raises(ValueError):
        store.signup(name, "password1", "1.1.1.1")


def test_password_length_rules(store):
    for pw in ("", "x" * 201):
        with pytest.raises(ValueError, match="Password must be 1-200 characters"):
            store.signup("nikos", pw, "1.1.1.1")
    assert store.signup("nikos", "a", "1.1.1.1") == "nikos"  # one letter is enough for a group of friends
    assert store.login("Nikos", "a") == "nikos"
    assert store.signup("maria", "x" * 200, "1.1.1.1") == "maria"
    with pytest.raises(ValueError, match="Password must be 1-200 characters"):
        store.reset_password("nikos", "")
    store.reset_password("nikos", "b")
    assert store.login("NIKOS", "b") == "nikos"


def test_lockout(store, clock):
    store.signup("Nikos", "password1", "1.1.1.1")
    for _ in range(5):
        with pytest.raises(BadCredentials):
            store.login("nikos", "wrong")
    with pytest.raises(LoginLocked):
        store.login("nikos", "password1")
    clock.advance(seconds=61)
    assert store.login("nikos", "password1") == "Nikos"


def test_success_resets_counter(store):
    store.signup("Nikos", "password1", "1.1.1.1")
    for _ in range(4):
        with pytest.raises(BadCredentials):
            store.login("Nikos", "wrong")
    store.login("Nikos", "password1")
    for _ in range(4):
        with pytest.raises(BadCredentials):
            store.login("Nikos", "wrong")
    assert store.login("Nikos", "password1") == "Nikos"


def test_unknown_user_counts_toward_lockout(store):
    for _ in range(5):
        with pytest.raises(BadCredentials):
            store.login("ghost", "whatever1")
    with pytest.raises(LoginLocked):
        store.login("Ghost", "whatever1")


def test_ip_limit(store, clock):
    for i in range(10):
        store.signup(f"user{i}", "password1", "1.2.3.4")
    with pytest.raises(SignupRateLimited):
        store.signup("user10", "password1", "1.2.3.4")
    store.signup("user10", "password1", "5.6.7.8")
    clock.advance(hours=1, seconds=1)
    store.signup("user11", "password1", "1.2.3.4")


def test_max_users(tmp_path, clock):
    s = AccountStore(tmp_path / "t.db", now=clock, max_users=3)
    for i in range(3):
        s.signup(f"user{i}", "password1", "1.1.1.1")
    with pytest.raises(TooManyAccounts):
        s.signup("user3", "password1", "1.1.1.1")


def test_sessions(store, clock, tmp_path):
    store.signup("Nikos", "password1", "1.1.1.1")
    token = store.create_session("Nikos")
    user = store.session_user(token)
    assert user.username == "Nikos" and user.id == store.user_id("nikos")
    with sqlite3.connect(tmp_path / "trips.db") as db:
        stored = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert stored != token and stored == hashlib.sha256(token.encode()).hexdigest()
    assert store.session_user(None) is None and store.session_user("garbage") is None
    clock.advance(days=30, seconds=1)
    assert store.session_user(token) is None
    store.delete_session(token)


def test_delete_and_sweep(store, clock):
    store.signup("Nikos", "password1", "1.1.1.1")
    t1 = store.create_session("Nikos")
    store.delete_session(t1)
    assert store.session_user(t1) is None
    old = store.create_session("Nikos")
    clock.advance(days=20)
    fresh = store.create_session("Nikos")
    clock.advance(days=11)
    assert store.sweep_sessions() == 1
    assert store.session_user(old) is None and store.session_user(fresh) is not None


def test_reset_password(store):
    store.signup("Nikos", "password1", "1.1.1.1")
    token = store.create_session("nikos")
    store.reset_password("nikos", "newpassword")
    assert store.session_user(token) is None
    assert store.login("Nikos", "newpassword") == "Nikos"
    with pytest.raises(BadCredentials):
        store.login("Nikos", "password1")
    with pytest.raises(KeyError):
        store.reset_password("ghost", "newpassword")


def test_usernames_sorted(store):
    for n in ("bob", "Alice", "carl"):
        store.signup(n, "password1", "1.1.1.1")
    assert store.usernames() == ["Alice", "bob", "carl"]


def test_cli(tmp_path, clock, capsys):
    path = tmp_path / "trips.db"
    AccountStore(path, now=clock).signup("Nikos", "password1", "1.1.1.1")

    def feed(*answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    assert main(["reset-password", "Nikos"], getpass=feed("newpassword", "newpassword"), path=path) == 0
    assert AccountStore(path, now=clock).login("Nikos", "newpassword") == "Nikos"
    assert main(["reset-password", "Nikos"], getpass=feed("newpassword", "other-pass"), path=path) == 1
    assert main(["reset-password", "ghost"], getpass=feed("newpassword", "newpassword"), path=path) == 1
    assert "newpassword" not in capsys.readouterr().out


def test_no_password_leaks(store):
    pw = "s3cretPassw0rd"
    store.signup("Nikos", pw, "1.1.1.1")
    assert pw not in repr(store)
    with pytest.raises(BadCredentials) as e:
        store.login("Nikos", pw + "x")
    assert pw not in repr(e.value) and pw not in str(e.value)
    with pytest.raises(UsernameTaken) as e:
        store.signup("Nikos", pw, "1.1.1.1")
    assert pw not in repr(e.value)


def count_scrypt(monkeypatch):
    calls = []
    real = accounts_mod._scrypt
    monkeypatch.setattr(accounts_mod, "_scrypt", lambda *a: calls.append(1) or real(*a))
    return calls


def test_login_ip_limit_checked_before_scrypt(store, clock, monkeypatch):
    store.signup("Nikos", "password1", "1.1.1.1")
    calls = count_scrypt(monkeypatch)
    for i in range(30):  # distinct usernames: the per-username lock never trips
        with pytest.raises(BadCredentials):
            store.login(f"ghost{i}", "whatever1", ip="6.6.6.6")
    assert len(calls) == 30
    with pytest.raises(LoginLocked):
        store.login("Nikos", "password1", ip="6.6.6.6")
    assert len(calls) == 30  # the 31st attempt never ran scrypt
    assert store.login("Nikos", "password1", ip="7.7.7.7") == "Nikos"  # another IP is fine
    clock.advance(minutes=10, seconds=1)
    assert store.login("Nikos", "password1", ip="6.6.6.6") == "Nikos"  # window expired


def test_login_ip_limit_counts_successes_too(store):
    store.signup("Nikos", "password1", "1.1.1.1")
    for _ in range(30):
        store.login("Nikos", "password1", ip="6.6.6.6")
    with pytest.raises(LoginLocked):
        store.login("Nikos", "password1", ip="6.6.6.6")


def test_throttle_dicts_stay_bounded(store, clock, monkeypatch):
    monkeypatch.setattr(accounts_mod, "_scrypt", lambda *a: b"x" * 32)  # speed: hashing is not under test
    for round_ in range(20):
        store.signup(f"user{round_}", "password1", f"9.9.{round_}.1")
        for i in range(25):
            ip = f"10.{round_}.0.{i}"
            for _ in range(5):  # locks the username
                with pytest.raises(BadCredentials):
                    store.login(f"spray{round_}x{i}", "whatever1", ip=ip)
            for _ in range(3):  # failures short of a lock
                with pytest.raises(BadCredentials):
                    store.login(f"half{round_}x{i}", "whatever1", ip=ip)
        # Only this round's entries survive: earlier rounds were pruned on write.
        assert len(store._locked_until) == 25
        assert len(store._failures) == 25
        assert len(store._login_ips) == 25
        assert len(store._signups) == 1
        clock.advance(minutes=61)
    with pytest.raises(BadCredentials):
        store.login("again", "whatever1", ip="8.8.4.4")
    store.signup("last", "password1", "9.9.9.9")
    assert len(store._locked_until) == 0 and len(store._failures) == 1
    assert len(store._login_ips) == 1 and len(store._signups) == 1


def test_scrypt_concurrency_is_bounded(monkeypatch):
    lock = threading.Lock()
    active = [0]
    peak = [0]

    def slow_scrypt(*a, **kw):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return b"x" * 32

    monkeypatch.setattr(accounts_mod.hashlib, "scrypt", slow_scrypt)
    threads = [threading.Thread(target=accounts_mod._scrypt, args=("pw", b"salt", 2 ** 14, 8, 1))
               for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 4
