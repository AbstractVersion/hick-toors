"""Accounts for the group: users, scrypt passwords, sessions, brute-force lockout, reset command."""

import argparse
import base64
import getpass as _getpass
import hashlib
import hmac
import re
import secrets
import sqlite3
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

SESSION_DAYS = 30
COOKIE_NAME = "hick_session"
MAX_USERS = 200
SIGNUPS_PER_IP_HOUR = 10
LOCK_AFTER_FAILURES = 5
LOCK_SECONDS = 60
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,20}")
_SCRYPT = (2 ** 14, 8, 1)


class UsernameTaken(Exception):
    """The lowercase username is already registered."""


class TooManyAccounts(Exception):
    """The store already holds max_users accounts."""


class SignupRateLimited(Exception):
    """Too many sign-ups from this IP in the last hour."""


class BadCredentials(Exception):
    """Unknown username or wrong password."""


class LoginLocked(Exception):
    """Too many wrong passwords for this username; try again in a minute."""


@dataclass(frozen=True)
class User:
    id: int
    username: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    # Fixed-width UTC text so string comparison is chronological.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * 2 + 2 ** 20)


def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, n, r, p)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        if n != _SCRYPT[0] or (r, p) != _SCRYPT[1:]:  # only the parameters we write
            return False
        expected = base64.b64decode(digest, validate=True)
        actual = _scrypt(password, base64.b64decode(salt, validate=True), n, r, p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccountStore:
    def __init__(self, path: Path, now: Callable[[], datetime] = utcnow, max_users: int = MAX_USERS,
                 signups_per_ip_hour: int = SIGNUPS_PER_IP_HOUR):
        self.path = Path(path)
        self.now = now
        self.max_users = max_users
        self.signups_per_ip_hour = signups_per_ip_hour
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, datetime] = {}
        self._signups: dict[str, list[datetime]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS users ("
                       "id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE, "
                       "pw_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS sessions ("
                       "token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL)")

    def __repr__(self) -> str:
        return f"AccountStore(path={str(self.path)!r})"

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    # --- sign-up / login -------------------------------------------------

    def signup(self, username: str, password: str, ip: str) -> str:
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError("Username must be 3-20 letters, digits, _ . -")
        if not 8 <= len(password) <= 200:
            raise ValueError("Password must be 8-200 characters")
        now = self.now()
        with self._lock:
            recent = [t for t in self._signups.get(ip, []) if now - t < timedelta(hours=1)]
            if len(recent) >= self.signups_per_ip_hour:
                self._signups[ip] = recent
                raise SignupRateLimited()
            self._signups[ip] = [*recent, now]
        pw_hash = hash_password(password)  # slow: before taking the database write lock
        key = username.lower()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # lock before counting: the cap holds under concurrency
            if db.execute("SELECT 1 FROM users WHERE username_key = ?", (key,)).fetchone():
                raise UsernameTaken()
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= self.max_users:
                raise TooManyAccounts()
            db.execute("INSERT INTO users (username, username_key, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                       (username, key, pw_hash, _stamp(now)))
        return username

    def login(self, username: str, password: str) -> str:
        key = username.lower()
        now = self.now()
        with self._lock:
            until = self._locked_until.get(key)
            if until is not None:
                if now < until:
                    raise LoginLocked()
                del self._locked_until[key]
        with self._db() as db:
            row = db.execute("SELECT username, pw_hash FROM users WHERE username_key = ?", (key,)).fetchone()
        ok = verify_password(password, row[1] if row else _DUMMY_HASH)  # unknown user: same timing
        if row is None or not ok:
            with self._lock:
                count = self._failures.get(key, 0) + 1
                if count >= LOCK_AFTER_FAILURES:
                    self._failures.pop(key, None)
                    self._locked_until[key] = now + timedelta(seconds=LOCK_SECONDS)
                else:
                    self._failures[key] = count
            raise BadCredentials()
        with self._lock:
            self._failures.pop(key, None)
        return row[0]

    # --- sessions --------------------------------------------------------

    def create_session(self, username: str) -> str:
        user_id = self.user_id(username)
        if user_id is None:
            raise KeyError(username)
        token = secrets.token_urlsafe(32)
        expires = self.now() + timedelta(days=SESSION_DAYS)
        with self._db() as db:
            db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                       (_sha256(token), user_id, _stamp(expires)))
        return token

    def session_user(self, token: str | None) -> User | None:
        if not token or not isinstance(token, str):
            return None
        with self._db() as db:
            row = db.execute("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                             "WHERE s.token_hash = ? AND s.expires_at > ?",
                             (_sha256(token), _stamp(self.now()))).fetchone()
        return User(id=row[0], username=row[1]) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))

    def sweep_sessions(self) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_stamp(self.now()),)).rowcount

    # --- lookups / admin -------------------------------------------------

    def usernames(self) -> list[str]:
        with self._db() as db:
            rows = db.execute("SELECT username FROM users").fetchall()
        return sorted((r[0] for r in rows), key=lambda n: (n.lower(), n))

    def user_id(self, username: str) -> int | None:
        with self._db() as db:
            row = db.execute("SELECT id FROM users WHERE username_key = ?", (username.lower(),)).fetchone()
        return row[0] if row else None

    def reset_password(self, username: str, password: str) -> None:
        if not 8 <= len(password) <= 200:
            raise ValueError("Password must be 8-200 characters")
        pw_hash = hash_password(password)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM users WHERE username_key = ?", (username.lower(),)).fetchone()
            if row is None:
                raise KeyError(username)
            db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (pw_hash, row[0]))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row[0],))
        with self._lock:
            self._failures.pop(username.lower(), None)
            self._locked_until.pop(username.lower(), None)


def main(argv: list[str] | None = None, getpass: Callable[[str], str] = _getpass.getpass,
         path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.accounts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset-password", help="set a new password and log the user out everywhere").add_argument("username")
    args = parser.parse_args(argv)
    if path is None:
        from app.config import Settings
        path = Settings().trips_db
    store = AccountStore(path)
    if store.user_id(args.username) is None:
        print(f"No such user: {args.username}", file=sys.stderr)
        return 1
    password = getpass("New password: ")
    if password != getpass("Repeat password: "):
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        store.reset_password(args.username, password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Password reset for {args.username}; logged out everywhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
