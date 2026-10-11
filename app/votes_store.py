"""Group voting rounds in SQLite: Love/OK/No-go votes on trips and stays, ranking, lazy close, frozen results."""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from app.accounts import AccountStore, User, utcnow
from app.models import (MAX_ROUND_TRIPS, Counts, Me, RoundIn, RoundPatch, RoundSummary, RoundView, StayStanding,
                        TripStanding, VoteIn, VoteOut, VoteResult, Winner)
from app.trips_store import TripStore
from app.vote_ranking import rank, score, winner

MIN_OPEN = timedelta(hours=1)
MAX_OPEN = timedelta(days=30)
CURRENT_AFTER_CLOSE = timedelta(days=7)
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


class RoundNotFound(Exception):
    """No round with that id."""


class RoundAlreadyOpen(Exception):
    def __init__(self, message: str = "A round is already open"):
        super().__init__(message)


class RoundClosed(Exception):
    def __init__(self, message: str = "This round is closed"):
        super().__init__(message)


class NotOpener(Exception):
    def __init__(self, message: str = "Only the opener can do this"):
        super().__init__(message)


class NotInRound(Exception):
    """The trip is not (any more) in the round, or the stay not (any more) in the trip."""


def _stamp(moment: datetime) -> str:
    # Fixed-width UTC text so string comparison / ORDER BY is chronological.
    return moment.astimezone(timezone.utc).strftime(_STAMP)


def _parse(text: str | None) -> datetime | None:
    return datetime.strptime(text, _STAMP).replace(tzinfo=timezone.utc) if text else None


def _counts(votes: list[VoteOut]) -> Counts:
    c = Counts()
    for v in votes:
        setattr(c, v.rating, getattr(c, v.rating) + 1)
    return c


def _vote_key(v: VoteOut):
    return (v.username.lower(), v.username)


_ROUND_COLS = "r.id, r.title, u.username, r.opener_id, r.created_at, r.closes_at, r.status, r.closed_at, r.result"


class RoundStore:
    def __init__(self, path: Path, trips: TripStore, accounts: AccountStore,
                 now: Callable[[], datetime] = utcnow):
        self.path = Path(path)
        self.trips = trips
        self.accounts = accounts
        self.now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS rounds ("
                       "id TEXT PRIMARY KEY, title TEXT NOT NULL, opener_id INTEGER NOT NULL, "
                       "created_at TEXT NOT NULL, closes_at TEXT NOT NULL, status TEXT NOT NULL, "
                       "closed_at TEXT, result TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS round_trips ("
                       "round_id TEXT NOT NULL, trip_id TEXT NOT NULL, position INTEGER NOT NULL, "
                       "PRIMARY KEY (round_id, trip_id))")
            db.execute("CREATE TABLE IF NOT EXISTS trip_votes ("
                       "round_id TEXT NOT NULL, trip_id TEXT NOT NULL, user_id INTEGER NOT NULL, "
                       "rating TEXT NOT NULL, comment TEXT, updated_at TEXT NOT NULL, "
                       "PRIMARY KEY (round_id, trip_id, user_id))")
            db.execute("CREATE TABLE IF NOT EXISTS stay_votes ("
                       "round_id TEXT NOT NULL, trip_id TEXT NOT NULL, stay_id TEXT NOT NULL, "
                       "user_id INTEGER NOT NULL, rating TEXT NOT NULL, comment TEXT, updated_at TEXT NOT NULL, "
                       "PRIMARY KEY (round_id, trip_id, stay_id, user_id))")
            db.execute("CREATE TABLE IF NOT EXISTS round_participants ("
                       "round_id TEXT NOT NULL, user_id INTEGER NOT NULL, joined_at TEXT NOT NULL, "
                       "PRIMARY KEY (round_id, user_id))")
            db.execute("CREATE INDEX IF NOT EXISTS rounds_status ON rounds (status)")

    def __repr__(self) -> str:
        return f"RoundStore(path={str(self.path)!r})"

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self):
        """A write transaction: lock first, then settle overdue rounds, all in one transaction."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._settle(db)
            yield db

    @contextmanager
    def _read(self):
        """A read: settles first, taking the write lock only when a round is actually overdue."""
        with self._db() as db:
            if self._overdue(db):
                db.execute("BEGIN IMMEDIATE")
                self._settle(db)  # re-checks under the lock
            yield db

    # --- settling / freezing -------------------------------------------------

    def _overdue(self, db) -> list[str]:
        return [r[0] for r in db.execute("SELECT id FROM rounds WHERE status = 'open' AND closes_at <= ?",
                                         (_stamp(self.now()),)).fetchall()]

    def _settle(self, db) -> None:
        for round_id in self._overdue(db):
            db.execute("UPDATE rounds SET status = 'closed', closed_at = closes_at WHERE id = ?", (round_id,))
            self._freeze(db, round_id)

    def _freeze(self, db, round_id: str) -> None:
        view = self._live_view(db, self._row(db, round_id))  # no viewer: me / my_rating stay null
        db.execute("UPDATE rounds SET result = ? WHERE id = ?", (view.model_dump_json(), round_id))

    # --- reading -----------------------------------------------------------

    @staticmethod
    def _row(db, round_id: str):
        return db.execute(f"SELECT {_ROUND_COLS} FROM rounds r LEFT JOIN users u ON u.id = r.opener_id "
                          "WHERE r.id = ?", (round_id,)).fetchone()

    def _live_view(self, db, row) -> RoundView:
        round_id, title, opener, _opener_id, created_at, closes_at, status, closed_at, _result = row
        trip_votes: dict[str, list[VoteOut]] = {}
        for trip_id, username, rating, comment in db.execute(
                "SELECT v.trip_id, u.username, v.rating, v.comment FROM trip_votes v "
                "JOIN users u ON u.id = v.user_id WHERE v.round_id = ?", (round_id,)):
            trip_votes.setdefault(trip_id, []).append(VoteOut(username=username, rating=rating, comment=comment))
        stay_votes: dict[tuple[str, str], list[VoteOut]] = {}
        for trip_id, stay_id, username, rating, comment in db.execute(
                "SELECT v.trip_id, v.stay_id, u.username, v.rating, v.comment FROM stay_votes v "
                "JOIN users u ON u.id = v.user_id WHERE v.round_id = ?", (round_id,)):
            stay_votes.setdefault((trip_id, stay_id), []).append(
                VoteOut(username=username, rating=rating, comment=comment))

        standings: list[tuple[int, TripStanding]] = []
        for trip_id, position in db.execute("SELECT trip_id, position FROM round_trips WHERE round_id = ? "
                                            "ORDER BY position", (round_id,)).fetchall():
            trip = self.trips._load(db, trip_id)  # same connection: one consistent snapshot
            if trip is None:
                continue  # deleted trip: drops out, its votes are ignored
            stays = []
            for index, stay in enumerate(trip.stays):
                votes = sorted(stay_votes.get((trip_id, stay.id), []), key=_vote_key)
                counts = _counts(votes)
                stays.append((index, StayStanding(stay_id=stay.id, name=stay.name, rank=0, score=score(counts),
                                                  counts=counts, votes=votes)))
            ranked_stays = rank(stays, lambda s: s[1].counts, lambda s: s[0])
            votes = sorted(trip_votes.get(trip_id, []), key=_vote_key)
            counts = _counts(votes)
            standings.append((position, TripStanding(
                trip_id=trip_id, name=trip.name, rank=0, score=score(counts), counts=counts, votes=votes,
                position=position, stays=[s.model_copy(update={"rank": i}) for i, (_, s) in enumerate(ranked_stays, 1)])))
        ranked = [t.model_copy(update={"rank": i}) for i, (_, t) in
                  enumerate(rank(standings, lambda t: t[1].counts, lambda t: t[0]), 1)]

        won = None
        if status == "closed":
            top = winner(ranked, lambda t: t.counts)
            if top is not None:
                stay = winner(top.stays, lambda s: s.counts)
                won = Winner(trip_id=top.trip_id, trip_name=top.name,
                             stay_id=stay.stay_id if stay else None, stay_name=stay.name if stay else None)
        participants = self._participants(db, round_id, opener, created_at)
        waiting = []
        if status == "open":
            rated: dict[str, int] = {}
            for t in ranked:
                for v in t.votes:
                    rated[v.username] = rated.get(v.username, 0) + 1
            waiting = sorted((n for n in participants if rated.get(n, 0) < len(ranked)),
                             key=lambda n: (n.lower(), n))
        return RoundView(id=round_id, title=title, opener=opener or "—", status=status,
                         created_at=_parse(created_at), closes_at=_parse(closes_at), closed_at=_parse(closed_at),
                         trips=ranked, waiting_for=waiting, participants=participants, me=None, winner=won)

    @staticmethod
    def _participants(db, round_id: str, opener: str | None, created_at: str) -> list[str]:
        """Opener first, then everyone else by join time: table rows ∪ voters (rounds from before joining existed)."""
        when: dict[str, str] = {}
        queries = (
            "SELECT u.username, MIN(p.joined_at) FROM round_participants p JOIN users u ON u.id = p.user_id "
            "WHERE p.round_id = ? GROUP BY u.username",
            "SELECT u.username, MIN(v.updated_at) FROM trip_votes v JOIN users u ON u.id = v.user_id "
            "WHERE v.round_id = ? GROUP BY u.username",
            "SELECT u.username, MIN(v.updated_at) FROM stay_votes v JOIN users u ON u.id = v.user_id "
            "WHERE v.round_id = ? GROUP BY u.username")
        for query in queries:
            for name, at in db.execute(query, (round_id,)):
                if name not in when or at < when[name]:
                    when[name] = at
        if opener:
            when[opener] = ""  # sorts before every timestamp
        return sorted(when, key=lambda n: (when[n], n.lower(), n))

    def _view(self, db, row, viewer: User | None) -> RoundView:
        result = row[8]
        view = RoundView.model_validate_json(result) if result else self._live_view(db, row)
        if result and not view.participants:  # frozen before participants existed
            view = view.model_copy(update={"participants": _voters(view)})
        return _personalize(view, viewer)

    def _load_row(self, db, round_id: str):
        row = self._row(db, round_id)
        if row is None:
            raise RoundNotFound(round_id)
        return row

    def _open_row(self, db, round_id: str):
        row = self._load_row(db, round_id)
        if row[6] != "open":
            raise RoundClosed()
        return row

    def _opener_row(self, db, round_id: str, user: User):
        row = self._open_row(db, round_id)
        if row[3] != user.id:
            raise NotOpener()
        return row

    def current(self, viewer: User | None) -> RoundView | None:
        with self._read() as db:
            row = db.execute(f"SELECT {_ROUND_COLS} FROM rounds r LEFT JOIN users u ON u.id = r.opener_id "
                             "WHERE r.status = 'open' ORDER BY r.created_at DESC LIMIT 1").fetchone()
            if row is None:
                row = db.execute(f"SELECT {_ROUND_COLS} FROM rounds r LEFT JOIN users u ON u.id = r.opener_id "
                                 "WHERE r.status = 'closed' AND r.closed_at > ? "
                                 "ORDER BY r.closed_at DESC, r.created_at DESC LIMIT 1",
                                 (_stamp(self.now() - CURRENT_AFTER_CLOSE),)).fetchone()
            return self._view(db, row, viewer) if row else None

    def get(self, round_id: str, viewer: User | None) -> RoundView | None:
        with self._read() as db:
            row = self._row(db, round_id)
            return self._view(db, row, viewer) if row else None

    def history(self) -> list[RoundSummary]:
        with self._read() as db:
            rows = db.execute(f"SELECT {_ROUND_COLS} FROM rounds r LEFT JOIN users u ON u.id = r.opener_id "
                              "ORDER BY r.created_at DESC, r.rowid DESC").fetchall()
        out = []
        for round_id, title, opener, _oid, _created, closes_at, status, closed_at, result in rows:
            won = RoundView.model_validate_json(result).winner if result else None
            out.append(RoundSummary(id=round_id, title=title, status=status, opener=opener or "—",
                                    closes_at=_parse(closes_at), closed_at=_parse(closed_at),
                                    winner_name=won.trip_name if won else None))
        return out

    # --- opening / opener actions -------------------------------------------------

    def open(self, user: User, data: RoundIn) -> RoundView:
        now = self.now()
        with self._write() as db:
            if db.execute("SELECT 1 FROM rounds WHERE status = 'open'").fetchone():
                raise RoundAlreadyOpen()
            if not now + MIN_OPEN <= data.closes_at <= now + MAX_OPEN:
                raise ValueError("The round must close between 1 hour and 30 days from now")
            self._check_trips(db, data.trip_ids)
            round_id = uuid.uuid4().hex
            db.execute("INSERT INTO rounds (id, title, opener_id, created_at, closes_at, status) "
                       "VALUES (?, ?, ?, ?, ?, 'open')",
                       (round_id, data.title, user.id, _stamp(now), _stamp(data.closes_at)))
            db.executemany("INSERT INTO round_trips (round_id, trip_id, position) VALUES (?, ?, ?)",
                           [(round_id, trip_id, i) for i, trip_id in enumerate(data.trip_ids)])
            self._join(db, round_id, user)
            return self._view(db, self._row(db, round_id), user)

    def _join(self, db, round_id: str, user: User) -> None:
        db.execute("INSERT OR IGNORE INTO round_participants (round_id, user_id, joined_at) VALUES (?, ?, ?)",
                   (round_id, user.id, _stamp(self.now())))

    def join(self, round_id: str, user: User) -> RoundView:
        with self._write() as db:
            self._open_row(db, round_id)
            self._join(db, round_id, user)
            return self._view(db, self._row(db, round_id), user)

    def _check_trips(self, db, trip_ids: list[str]) -> None:
        unknown = [t for t in trip_ids if self.trips._load(db, t) is None]
        if unknown:
            raise ValueError(f"Unknown trip: {', '.join(unknown)}")

    def patch(self, round_id: str, user: User, data: RoundPatch) -> RoundView:
        now = self.now()
        with self._write() as db:
            row = self._opener_row(db, round_id, user)
            if data.closes_at is not None:
                if data.closes_at <= _parse(row[5]):
                    raise ValueError("The new deadline must be later than the current one")
                if data.closes_at > now + MAX_OPEN:
                    raise ValueError("The round must close within 30 days from now")
            existing = {r[0]: r[1] for r in db.execute("SELECT trip_id, position FROM round_trips "
                                                       "WHERE round_id = ?", (round_id,))}
            new = [t for t in dict.fromkeys(data.add_trip_ids) if t not in existing]
            self._check_trips(db, new)
            if len(existing) + len(new) > MAX_ROUND_TRIPS:
                raise ValueError(f"A round holds at most {MAX_ROUND_TRIPS} trips")
            if data.closes_at is not None:
                db.execute("UPDATE rounds SET closes_at = ? WHERE id = ?", (_stamp(data.closes_at), round_id))
            start = max(existing.values(), default=-1) + 1
            db.executemany("INSERT INTO round_trips (round_id, trip_id, position) VALUES (?, ?, ?)",
                           [(round_id, t, start + i) for i, t in enumerate(new)])
            return self._view(db, self._row(db, round_id), user)

    def close(self, round_id: str, user: User) -> RoundView:
        with self._write() as db:
            self._opener_row(db, round_id, user)
            db.execute("UPDATE rounds SET status = 'closed', closed_at = ? WHERE id = ?",
                       (_stamp(self.now()), round_id))
            self._freeze(db, round_id)
            return self._view(db, self._row(db, round_id), user)

    def cancel(self, round_id: str, user: User) -> RoundView:
        with self._write() as db:
            self._opener_row(db, round_id, user)
            db.execute("UPDATE rounds SET status = 'cancelled', closed_at = ? WHERE id = ?",
                       (_stamp(self.now()), round_id))
            return self._view(db, self._row(db, round_id), user)

    # --- votes -----------------------------------------------------------------

    def _trip_in_round(self, db, round_id: str, trip_id: str):
        if not db.execute("SELECT 1 FROM round_trips WHERE round_id = ? AND trip_id = ?",
                          (round_id, trip_id)).fetchone():
            raise NotInRound(trip_id)
        trip = self.trips._load(db, trip_id)
        if trip is None:
            raise NotInRound(trip_id)  # deleted since the round opened
        return trip

    def _stay_in_round(self, db, round_id: str, trip_id: str, stay_id: str) -> None:
        trip = self._trip_in_round(db, round_id, trip_id)
        if not any(s.id == stay_id for s in trip.stays):
            raise NotInRound(stay_id)

    def vote_trip(self, round_id: str, trip_id: str, user: User, vote: VoteIn) -> VoteResult:
        with self._write() as db:
            self._open_row(db, round_id)
            self._trip_in_round(db, round_id, trip_id)
            moved = None
            if vote.rating == "love":
                old = db.execute("SELECT trip_id FROM trip_votes WHERE round_id = ? AND user_id = ? "
                                 "AND rating = 'love' AND trip_id != ?", (round_id, user.id, trip_id)).fetchall()
                if old:
                    moved = old[0][0]
                    db.execute("UPDATE trip_votes SET rating = 'ok', updated_at = ? WHERE round_id = ? "
                               "AND user_id = ? AND rating = 'love' AND trip_id != ?",
                               (_stamp(self.now()), round_id, user.id, trip_id))
            self._join(db, round_id, user)
            db.execute("INSERT OR REPLACE INTO trip_votes (round_id, trip_id, user_id, rating, comment, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (round_id, trip_id, user.id, vote.rating, vote.comment, _stamp(self.now())))
            return VoteResult(round=self._view(db, self._row(db, round_id), user), moved_love_from=moved)

    def clear_trip_vote(self, round_id: str, trip_id: str, user: User) -> VoteResult:
        with self._write() as db:
            self._open_row(db, round_id)
            self._trip_in_round(db, round_id, trip_id)
            db.execute("DELETE FROM trip_votes WHERE round_id = ? AND trip_id = ? AND user_id = ?",
                       (round_id, trip_id, user.id))
            return VoteResult(round=self._view(db, self._row(db, round_id), user), moved_love_from=None)

    def vote_stay(self, round_id: str, trip_id: str, stay_id: str, user: User, vote: VoteIn) -> VoteResult:
        with self._write() as db:
            self._open_row(db, round_id)
            self._stay_in_round(db, round_id, trip_id, stay_id)
            moved = None
            if vote.rating == "love":
                where = ("round_id = ? AND trip_id = ? AND user_id = ? AND rating = 'love' AND stay_id != ?")
                args = (round_id, trip_id, user.id, stay_id)
                old = db.execute(f"SELECT stay_id FROM stay_votes WHERE {where}", args).fetchall()
                if old:
                    moved = old[0][0]
                    db.execute(f"UPDATE stay_votes SET rating = 'ok', updated_at = ? WHERE {where}",
                               (_stamp(self.now()), *args))
            self._join(db, round_id, user)
            db.execute("INSERT OR REPLACE INTO stay_votes "
                       "(round_id, trip_id, stay_id, user_id, rating, comment, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (round_id, trip_id, stay_id, user.id, vote.rating, vote.comment, _stamp(self.now())))
            return VoteResult(round=self._view(db, self._row(db, round_id), user), moved_love_from=moved)

    def clear_stay_vote(self, round_id: str, trip_id: str, stay_id: str, user: User) -> VoteResult:
        with self._write() as db:
            self._open_row(db, round_id)
            self._stay_in_round(db, round_id, trip_id, stay_id)
            db.execute("DELETE FROM stay_votes WHERE round_id = ? AND trip_id = ? AND stay_id = ? AND user_id = ?",
                       (round_id, trip_id, stay_id, user.id))
            return VoteResult(round=self._view(db, self._row(db, round_id), user), moved_love_from=None)


def _voters(view: RoundView) -> list[str]:
    names = {v.username for t in view.trips for v in [*t.votes, *(x for s in t.stays for x in s.votes)]}
    names.add(view.opener)
    names.discard("—")
    return sorted(names, key=lambda n: (n != view.opener, n.lower(), n))


def _personalize(view: RoundView, viewer: User | None) -> RoundView:
    """Fill `me` and every `my_rating` for the viewer from the view's own votes lists (null when logged out)."""
    if viewer is None:
        return view

    def mine(votes: list[VoteOut]):
        return next((v.rating for v in votes if v.username == viewer.username), None)

    trips = []
    for t in view.trips:
        stays = [s.model_copy(update={"my_rating": mine(s.votes)}) for s in t.stays]
        trips.append(t.model_copy(update={"my_rating": mine(t.votes), "stays": stays}))
    me = Me(rated=sum(1 for t in trips if t.my_rating is not None), total=len(trips),
            love_trip_id=next((t.trip_id for t in trips if t.my_rating == "love"), None),
            joined=viewer.username in view.participants)
    return view.model_copy(update={"trips": trips, "me": me})
