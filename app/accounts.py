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

PICK_FROM_LIST = "Pick an emoji from the list"
SESSION_DAYS = 30
COOKIE_NAME = "hick_session"
MAX_USERS = 200
SIGNUPS_PER_IP_HOUR = 10
LOCK_AFTER_FAILURES = 5
LOCK_SECONDS = 60
LOGINS_PER_IP_WINDOW = 30
LOGIN_IP_WINDOW = timedelta(minutes=10)
SIGNUP_IP_WINDOW = timedelta(hours=1)
SCRYPT_SLOTS = 4  # ~16 MiB each: caps hashing RAM/CPU however many requests arrive
# The funny set users pick from, funniest first. Stored exactly as written here (some carry U+FE0F).
EMOJIS: tuple[str, ...] = (
    "🤡", "🍆", "🍑", "💩", "👽", "🤖", "👻", "🦄", "🐸", "🐔",
    "🦆", "🐙", "🦖", "🌶️", "🥑", "🍕", "🌮", "🧀", "🦩", "🐷",
    "🙈", "🦥", "🦦", "🐌", "🦀", "🍄", "🌵", "🎃", "🤠", "🥸",
    "🤓", "😈", "👹", "💀", "🧟", "🧛", "🧙", "🥷", "🐉", "🦈",
    "🐊", "🦔", "🦇", "🐝", "🔥", "💣", "🎸", "🛸", "🚀", "🍌",
    "🥥", "🥒", "🍔", "🍩", "🍺", "🦑", "🐧", "🦉", "🦜", "🐮",
    "🐵", "🦍", "🐼", "🐨", "🦘", "🦒", "🐘", "🦛", "🐫", "🦙",
    "🐢", "🐍", "🦎", "🦞", "🦐", "🪲", "🦋", "🐛", "🕷️", "🦂",
    "🦃", "🧞", "🧜", "🧚", "👾", "☠️", "🤪", "🥴", "🤯", "🥳",
    "🍟", "🌭", "🥓", "🧇", "🍉", "🍍", "🍓", "🥔", "🧅", "🥦",
    "🍦", "🧁", "🍪", "🥨", "🥐", "🍣", "🍜", "🥟", "🧄", "🐒",
)
_EMOJI_SET = frozenset(EMOJIS)
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,20}")
_SCRYPT = (2 ** 14, 8, 1)


class UsernameTaken(Exception):
    """The lowercase username is already registered."""


class TooManyAccounts(Exception):
    """The store already holds max_users accounts."""


class SignupRateLimited(Exception):
    """Too many sign-ups from this IP in the last hour."""


class EmojiTaken(Exception):
    """Another account already uses this emoji."""


class NoEmojisLeft(Exception):
    """Every emoji in the set belongs to someone."""


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


_scrypt_slots = threading.BoundedSemaphore(SCRYPT_SLOTS)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with _scrypt_slots:
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
                 signups_per_ip_hour: int = SIGNUPS_PER_IP_HOUR, logins_per_ip_window: int = LOGINS_PER_IP_WINDOW):
        self.path = Path(path)
        self.now = now
        self.max_users = max_users
        self.signups_per_ip_hour = signups_per_ip_hour
        self.logins_per_ip_window = logins_per_ip_window
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, datetime]] = {}  # key -> (wrong passwords in a row, last one)
        self._locked_until: dict[str, datetime] = {}
        self._signups: dict[str, list[datetime]] = {}  # ip -> recent sign-up times
        self._login_ips: dict[str, list[datetime]] = {}  # ip -> recent login attempt times
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS users ("
                       "id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE, "
                       "pw_hash TEXT NOT NULL, created_at TEXT NOT NULL, emoji TEXT)")
            if "emoji" not in {r[1] for r in db.execute("PRAGMA table_info(users)")}:
                try:
                    db.execute("ALTER TABLE users ADD COLUMN emoji TEXT")  # database from before emojis
                except sqlite3.OperationalError as exc:  # another process migrated in between
                    if "duplicate column" not in str(exc).lower():
                        raise
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_emoji ON users (emoji) WHERE emoji IS NOT NULL")
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

    # --- in-memory throttles (call with self._lock held) -----------------

    def _prune(self, now: datetime) -> None:
        """Drop expired throttle entries so a username/IP spray cannot grow memory without bound."""
        lock_window = timedelta(seconds=LOCK_SECONDS)
        self._locked_until = {k: t for k, t in self._locked_until.items() if now < t}
        self._failures = {k: v for k, v in self._failures.items() if now - v[1] < lock_window}
        self._signups = {ip: ts for ip, ts in self._signups.items() if now - ts[-1] < SIGNUP_IP_WINDOW}
        self._login_ips = {ip: ts for ip, ts in self._login_ips.items() if now - ts[-1] < LOGIN_IP_WINDOW}

    @staticmethod
    def _hit(windows: dict[str, list[datetime]], ip: str, now: datetime, window: timedelta, limit: int) -> bool:
        """Record an attempt for ip unless it is over limit within window; True when allowed."""
        recent = [t for t in windows.get(ip, []) if now - t < window]
        if len(recent) >= limit:
            windows[ip] = recent
            return False
        windows[ip] = [*recent, now]
        return True

    # --- sign-up / login -------------------------------------------------

    def signup(self, username: str, password: str, ip: str, emoji: str) -> str:
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError("Username must be 3-20 letters, digits, _ . -")
        if not 1 <= len(password) <= 200:
            raise ValueError("Password must be 1-200 characters")
        if emoji not in _EMOJI_SET:
            raise ValueError(PICK_FROM_LIST)
        now = self.now()
        with self._lock:
            self._prune(now)
            if not self._hit(self._signups, ip, now, SIGNUP_IP_WINDOW, self.signups_per_ip_hour):
                raise SignupRateLimited()
        pw_hash = hash_password(password)  # slow: before taking the database write lock
        key = username.lower()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # lock before counting: the cap holds under concurrency
            if db.execute("SELECT 1 FROM users WHERE username_key = ?", (key,)).fetchone():
                raise UsernameTaken()
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= self.max_users:
                raise TooManyAccounts()
            if db.execute("SELECT 1 FROM users WHERE emoji = ?", (emoji,)).fetchone():
                if db.execute("SELECT COUNT(*) FROM users WHERE emoji IS NOT NULL").fetchone()[0] >= len(EMOJIS):
                    raise NoEmojisLeft()
                raise EmojiTaken()
            try:
                db.execute("INSERT INTO users (username, username_key, pw_hash, created_at, emoji) "
                           "VALUES (?, ?, ?, ?, ?)", (username, key, pw_hash, _stamp(now), emoji))
            except sqlite3.IntegrityError:  # lost a race on the unique index
                raise EmojiTaken()
        return username

    def set_emoji(self, username: str, emoji: str) -> None:
        """Give an existing account this emoji (its own current one is fine)."""
        if emoji not in _EMOJI_SET:
            raise ValueError(PICK_FROM_LIST)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id, emoji FROM users WHERE username_key = ?", (username.lower(),)).fetchone()
            if row is None:
                raise KeyError(username)
            if row[1] == emoji:
                return
            if db.execute("SELECT 1 FROM users WHERE emoji = ?", (emoji,)).fetchone():
                raise EmojiTaken()
            try:
                db.execute("UPDATE users SET emoji = ? WHERE id = ?", (emoji, row[0]))
            except sqlite3.IntegrityError:
                raise EmojiTaken()

    def emoji_of(self, username: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT emoji FROM users WHERE username_key = ?", (username.lower(),)).fetchone()
        return row[0] if row else None

    def users(self) -> list[tuple[str, str | None]]:
        with self._db() as db:
            rows = db.execute("SELECT username, emoji FROM users").fetchall()
        return sorted(((r[0], r[1]) for r in rows), key=lambda u: (u[0].lower(), u[0]))

    def taken_emojis(self) -> list[str]:
        with self._db() as db:
            rows = db.execute("SELECT emoji FROM users WHERE emoji IS NOT NULL").fetchall()
        taken = {r[0] for r in rows}
        return [e for e in EMOJIS if e in taken]

    def login(self, username: str, password: str, ip: str | None = None) -> str:
        """ip=None (CLI/tests) skips the per-IP limit; the HTTP route always passes the client IP."""
        key = username.lower()
        now = self.now()
        with self._lock:  # every throttle is checked before scrypt runs
            self._prune(now)
            if ip is not None and not self._hit(self._login_ips, ip, now, LOGIN_IP_WINDOW,
                                                self.logins_per_ip_window):
                raise LoginLocked()
            if key in self._locked_until:  # _prune just dropped the expired ones
                raise LoginLocked()
        with self._db() as db:
            row = db.execute("SELECT username, pw_hash FROM users WHERE username_key = ?", (key,)).fetchone()
        ok = verify_password(password, row[1] if row else _DUMMY_HASH)  # unknown user: same timing
        if row is None or not ok:
            with self._lock:
                self._prune(now)
                count = self._failures.get(key, (0, now))[0] + 1
                if count >= LOCK_AFTER_FAILURES:
                    self._failures.pop(key, None)
                    self._locked_until[key] = now + timedelta(seconds=LOCK_SECONDS)
                else:
                    self._failures[key] = (count, now)
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
        if not 1 <= len(password) <= 200:
            raise ValueError("Password must be 1-200 characters")
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
