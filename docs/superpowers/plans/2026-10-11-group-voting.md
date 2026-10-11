# hick-toors — Group Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Self-made accounts, trips editable only when logged in, voting rounds with a deadline where people rate trips and their stays 😍/🙂/🚫, a live ranking and an automatic 🏆 winner.

**Architecture:** Two new SQLite stores in the existing `TRIPS_DB` file — `app/accounts.py` (`AccountStore`: users, scrypt hashes, sessions, lockout, reset CLI) and `app/votes_store.py` (`RoundStore`: rounds, votes, lazy close, frozen results) with the pure ranking in `app/vote_ranking.py`. Routes live in FastAPI routers `app/auth_routes.py` and `app/rounds_routes.py` (keeps `app/main.py` from growing). UI: `static/auth.js` (login state + dialog + `requireLogin`) and `static/vote.js` (vote dialog, badge, polling).

**Tech Stack:** Python 3.10, FastAPI, pydantic v2, stdlib `sqlite3`/`hashlib.scrypt`/`secrets`/`hmac`, pytest; vanilla JS; Playwright UI check.

**Spec:** `docs/superpowers/specs/2026-10-11-group-voting-design.md` — the binding authority for every exact value (limits, messages, cookie flags, scores, table columns, RoundView shape). Each task must be read together with the spec sections it names.

## Global Constraints

- Project folder `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors`, branch `feat/hick-toors`. Never read/print/commit `.env`. The sandbox refuses compound git commands (`cd x && git`, `git -C`): plain git commands only, run from the project folder.
- `env -u VIRTUAL_ENV uv run pytest -q` stays green (577 passing at start). Tests never touch the network.
- Python 3.10; **no new dependencies** (stdlib crypto only).
- Messages verbatim from the spec: `"Log in first"`, `"Wrong username or password"`, `"Too many attempts – wait a minute"`, `"That username is taken"`, `"No more accounts can be created"`, `"A round is already open"`, `"This round is closed"`, `"Only the opener can do this"`.
- Stored trips: only add optional fields (old documents must keep loading). `created_by` is server-set only.
- Time is injectable (`now: Callable[[], datetime]`, aware UTC) in both stores so tests never sleep.
- Passwords and session tokens never appear in logs, reprs, error messages or responses; only the SHA-256 of a token is stored.
- UI: user-provided text (usernames, comments, titles) via `textContent` only; no `window.confirm/alert/prompt` (two-click confirm like the existing Delete); never run UI checks against port 8000 — own server on 8765 with a throwaway `TRIPS_DB`.
- Commits end with a blank line + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. Two people voting at the same moment, one of them moving their Love → still exactly one Love per person per round (BEGIN IMMEDIATE). (Task 3 test with two threads.)
2. A round whose deadline passed while nobody had the page open → the first request of any kind (GET current, a vote) sees it closed with its frozen result; a vote sent after the deadline gets 409. (Task 3 + Task 4 tests.)
3. A trip deleted or a stay removed after votes were cast → ranking and winner ignore them without errors; a closed round's result stays as it was. (Task 3 test.)
4. Logged-out visitor clicking ＋ Trip / vote / Save → the login dialog opens and the original action continues after login; an expired cookie (401) does the same. (Task 5 UI check.)
5. Usernames/comments containing `<script>`, quotes or emoji → rendered as text in every view and in the MCP JSON. (Task 4 test + Task 6 UI check.)

---

### Task 1: Accounts store (users, hashing, sessions, lockout, reset CLI)

**Files:** Create `app/accounts.py`, `tests/test_accounts.py`.

**Interfaces — Produces:**
- `hash_password(password: str) -> str`, `verify_password(password: str, stored: str) -> bool` (format `scrypt$16384$8$1$<salt b64>$<hash b64>`).
- `class AccountStore(path: Path, now=utcnow, max_users=200, signups_per_ip_hour=10)`:
  - `signup(username: str, password: str, ip: str) -> str` (returns the stored username) — raises `UsernameTaken`, `TooManyAccounts`, `SignupRateLimited`, `ValueError(message)` for rule violations.
  - `login(username: str, password: str) -> str` (stored username) — raises `BadCredentials`, `LoginLocked`.
  - `create_session(username: str) -> str` (raw token; stores SHA-256, expires now + 30 days), `session_user(token: str | None) -> User | None`, `delete_session(token)`, `sweep_sessions() -> int`.
  - `usernames() -> list[str]` (all accounts, sorted case-insensitively), `user_id(username) -> int | None`, `reset_password(username, password)` (also deletes that user's sessions).
- `@dataclass(frozen=True) class User: id: int; username: str`.
- `SESSION_DAYS = 30`, `COOKIE_NAME = "hick_session"`.
- CLI: `python -m app.accounts reset-password <username>` (reads `Settings().trips_db`; `getpass` twice; mismatch / unknown user → message + exit code 1).

- [ ] Step 1 — Failing tests (`tests/test_accounts.py`, store on `tmp_path / "trips.db"`, a fake clock):
  - hash format has 6 `$`-parts starting `scrypt$16384$8$1$`; two hashes of the same password differ (salt); verify true/false; a malformed stored string → False (no exception).
  - signup returns `"Nikos"`; signup `"nikos"` again → `UsernameTaken`; login `"NIKOS"` with the right password → `"Nikos"`.
  - rules: `"ab"`, 21 chars, `"bad name"`, `"νίκος"` → ValueError; password of 7 chars / 201 chars → ValueError; 8 chars ok.
  - lockout: 5 wrong passwords → 6th attempt (even with the right password) raises `LoginLocked`; after the clock moves 61 s the right password works; a success resets the counter (4 wrong, 1 right, 4 wrong → no lock). Unknown username → `BadCredentials` (and counts toward that key's lockout).
  - sign-up limits: 10 sign-ups from ip `1.2.3.4` ok, 11th → `SignupRateLimited`, another IP ok; after 1 h the first IP works again; `max_users=3` → 4th → `TooManyAccounts`.
  - sessions: token from `create_session` resolves to the user; stored value in `sessions.token_hash` is not the token (is its sha256 hex); after 30 days + 1 s → None; `delete_session` → None; `sweep_sessions` removes only expired rows; `session_user(None)` / garbage → None.
  - `reset_password` changes login and drops that user's sessions; unknown user → `KeyError`.
  - CLI: `main(["reset-password", "Nikos"], getpass=fake)` with matching inputs → 0; mismatch → 1; unknown user → 1 (pass the store path in via a parameter for the test).
  - `repr()` of the store and exceptions never contains the password.
- [ ] Step 2 — Run, see them fail (ImportError).
- [ ] Step 3 — Implement. Tables exactly as the spec (§Accounts). Connection helper like `TripStore._db` (timeout 10, `with conn`); `BEGIN IMMEDIATE` around signup (count + insert). Lockout and IP counters in memory (dicts guarded by a `threading.Lock`). Unknown-username login runs `verify_password` against a module-level dummy hash so timing matches.
- [ ] Step 4 — Full suite green; commit `feat(hick-toors): accounts store – scrypt passwords, sessions, lockout, reset command`.

### Task 2: Auth API, trips behind the login, `created_by`

**Files:** Create `app/auth_routes.py`, `tests/test_auth_api.py`. Modify `app/main.py` (create_app gets `accounts: AccountStore | None = None`; include router; protect trip routes; sweep sessions at startup and in the daily sweep), `app/models.py` (`SavedTrip.created_by`, `SavedTripSummary.created_by`), `app/trips_store.py` (`create(data, created_by=None)`, `replace` keeps `created_by`), existing tests that POST/PUT/DELETE trips (`tests/test_api_saved_trips.py`, `tests/test_nearby_sights_api.py`, any other hitting protected routes) — give them a logged-in client.

**Interfaces — Consumes:** Task 1 `AccountStore`, `User`, `COOKIE_NAME`, `SESSION_DAYS`.
**Produces:**
- `auth_router(accounts: AccountStore) -> APIRouter` with the four routes of spec §Auth API.
- Dependencies in `app/auth_routes.py`: `current_user(request) -> User | None`, `require_user(request) -> User` (401 `"Log in first"`); they read `request.app.state.accounts`.
- `tests/builders.py`: `login_client(client, username="nikos", password="password1") -> TestClient` (signs up, cookie kept by the TestClient).

- [ ] Step 1 — Failing tests (`tests/test_auth_api.py`):
  - signup → 201 `{"username": "Nikos"}` and `Set-Cookie` with `hick_session=`, `HttpOnly`, `SameSite=lax`, `Path=/`, `Max-Age=2592000`, **no** `Secure` over http; with `X-Forwarded-Proto` ignored (only the request scheme counts — TestClient `base_url="https://testserver"` → `Secure` present).
  - `GET /api/auth/me` → `{"username": "Nikos"}`; after logout 204 → `{"username": null}` and the old cookie no longer works.
  - login wrong → 401 `"Wrong username or password"`; 6th try → 429 `"Too many attempts – wait a minute"`; taken → 409; rules → 422 with the rule message; IP limit → 429; cap → 409 `"No more accounts can be created"`.
  - Every protected route without a cookie → 401 `"Log in first"`: `POST /api/saved-trips`, `PUT/DELETE /api/saved-trips/{id}`, `POST …/items`, `POST …/nearby-sights`. Still open without a cookie: `GET /api/saved-trips`, `GET /api/saved-trips/{id}`, `export.md`, `print`, `POST /api/stays/lookup`.
  - `created_by`: create while logged in as Nikos → `"Nikos"` in the trip and in the list summary; a body with `"created_by": "eve"` is ignored on create and on PUT; a PUT by another user keeps `"Nikos"`; an old trip JSON without the key loads with `None`.
  - Response bodies never contain the password or the token (check signup/login JSON).
- [ ] Step 2 — Run, see them fail.
- [ ] Step 3 — Implement. Cookie: `response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS*86400, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")`. Client IP: `request.client.host` (or `"unknown"`). Sync route handlers (sqlite in the threadpool) like the trip routes. Protect trip routes with `user: User = Depends(require_user)` and pass `user.username` into `trip_store.create`. `create_app` builds `AccountStore(settings.trips_db)` when none is given and stores it on `app.state.accounts`; call `accounts.sweep_sessions()` in the lifespan start and inside the daily sweep loop (errors only logged).
- [ ] Step 4 — Update existing tests that hit protected routes to use `login_client` (no assertion weakened). Full suite green; commit `feat(hick-toors): sign up / log in, trips editable only when logged in, created_by`.

### Task 3: Rounds store and ranking

**Files:** Create `app/vote_ranking.py`, `app/votes_store.py`, `tests/test_vote_ranking.py`, `tests/test_votes_store.py`. Modify `app/models.py` (round models below).

**Interfaces — Consumes:** `TripStore.get(trip_id) -> SavedTrip | None` (names, stays), `AccountStore` (`user_id`, `usernames`), both on the same SQLite file.
**Produces (app/models.py):**
- `Rating = Literal["love", "ok", "nogo"]`; `VoteIn(rating: Rating, comment: str | None = None)` (trimmed, ≤ 140, empty → None).
- `RoundIn(title: str 1–80 trimmed, closes_at: datetime (aware), trip_ids: list[str] 1–50 unique)`; `RoundPatch(closes_at: datetime | None = None, add_trip_ids: list[str] = [])`.
- `VoteOut(username, rating, comment)`, `Counts(love, ok, nogo)`, `StayStanding(stay_id, name, rank, score, counts, votes, my_rating)`, `TripStanding(trip_id, name, rank, score, counts, votes, my_rating, stays)`, `Me(rated, total, love_trip_id)`, `Winner(trip_id, trip_name, stay_id, stay_name)`, `RoundView(id, title, opener, status, created_at, closes_at, closed_at, trips, waiting_for, me, winner)`, `RoundSummary(id, title, status, opener, closes_at, closed_at, winner_name)`, `VoteResult(round: RoundView, moved_love_from: str | None)` — fields exactly as spec §Rounds API.
**Produces (app/vote_ranking.py, pure):** `score(counts) -> int`; `rank(items: list[T], counts_of, position_of) -> list[T]` implementing spec order (score desc, nogos asc, loves desc, position asc); `winner(...)` per spec.
**Produces (app/votes_store.py):** `class RoundStore(path, trips: TripStore, accounts: AccountStore, now=utcnow)` with `open(user: User, data: RoundIn) -> RoundView`, `current(viewer: User | None) -> RoundView | None`, `get(round_id, viewer) -> RoundView | None`, `history() -> list[RoundSummary]`, `vote_trip(round_id, trip_id, user, vote: VoteIn) -> VoteResult`, `clear_trip_vote(...) -> VoteResult`, `vote_stay(round_id, trip_id, stay_id, user, vote) -> VoteResult`, `clear_stay_vote(...) -> VoteResult`, `patch(round_id, user, data: RoundPatch) -> RoundView`, `close(round_id, user) -> RoundView`, `cancel(round_id, user) -> RoundView`. Exceptions: `RoundNotFound`, `RoundAlreadyOpen`, `RoundClosed`, `NotOpener`, `NotInRound` (trip or stay), `ValueError(message)` for deadline/trip-id rules.

- [ ] Step 1 — Failing ranking tests (`tests/test_vote_ranking.py`): scores 2/1/−3; every tie-break in order (equal score → fewer nogos first; then more loves; then lower position); empty votes rank last by position with score 0; winner = first item with ≥ 1 vote, None when nobody voted; stay winner None when the winning trip's stays have no votes.
- [ ] Step 2 — Failing store tests (`tests/test_votes_store.py`; real `TripStore` + `AccountStore` on one `tmp_path` file; fake clock; users Nikos, Maria, Kostas; 3 trips with 2 stays each):
  - open: second open → `RoundAlreadyOpen`; closes_at now+59 min / now+30 d+1 s → ValueError; unknown trip id → ValueError; positions follow the given order.
  - vote/replace/clear; comment trimmed, 141 chars rejected by `VoteIn`; Love on trip B while Love on A → A becomes `ok`, `moved_love_from == A`; same at stay level within one trip (Love on a stay in another trip does not move it).
  - `me.rated/total/love_trip_id`; `waiting_for` lists accounts that haven't rated every trip (stays don't count), sorted.
  - ranking in the view matches `vote_ranking`; `votes` list each voter's username/rating/comment; `my_rating` per viewer; logged-out viewer → `me is None`, `my_rating None`.
  - opener-only patch/close/cancel (`NotOpener` for others); extend must be later than current and ≤ now+30 d; add_trip_ids appended at the end, duplicates ignored, unknown → ValueError.
  - lazy close: move the clock past `closes_at` → `current()` shows `status == "closed"`, `closed_at == closes_at`, winner set; a vote now → `RoundClosed`; deleting a trip afterwards does not change `get()` of that round (frozen result); `my_rating` in the frozen view is the viewer's.
  - before close: deleted trip disappears from the view; removed stay's votes ignored; voting on it → `NotInRound`.
  - `current()`: open round, else a closed one < 7 days old, else None; cancelled never current; `history()` newest first with `winner_name`.
  - concurrency: two threads, same user, Love on A and Love on B at once → exactly one `love` row for that user in the round.
- [ ] Step 3 — Run, see them fail.
- [ ] Step 4 — Implement per spec §Rounds and votes (tables, rules, settle-first under `BEGIN IMMEDIATE`, frozen `result` JSON of the RoundView with `me`/`my_rating` stripped and recomputed per viewer from the frozen votes).
- [ ] Step 5 — Full suite green; commit `feat(hick-toors): voting rounds store – Love/OK/No-go, ranking, lazy close, frozen results`.

### Task 4: Rounds API, MCP `vote_standings`, Ask prompt

**Files:** Create `app/rounds_routes.py`, `tests/test_rounds_api.py`. Modify `app/main.py` (create_app gets `rounds: RoundStore | None = None`, builds it from the trip store + accounts, includes the router), `app/mcp_tools.py` (`vote_standings`), `app/ask.py` (one sentence), `tests/test_mcp_tools.py` or `tests/test_rounds_api.py` for the MCP tool.

**Interfaces — Consumes:** Task 3 `RoundStore` + models; Task 2 `current_user` / `require_user`.
**Produces:** `rounds_router(rounds: RoundStore) -> APIRouter` with every route of spec §Rounds API; MCP `vote_standings() -> str`.

- [ ] Step 1 — Failing tests: every route's happy path; 401 for writes logged out; GETs open logged out (`me` null); error mapping per spec §Errors (403 / 404 / 409 / 422 with the verbatim messages); `PUT …/vote` returns `{round, moved_love_from}`; a vote after the (faked) deadline → 409 `"This round is closed"`; comments and titles with `<script>"` come back verbatim in JSON (no server-side HTML); MCP `vote_standings()` → current RoundView JSON without `me`, or `{"round": null}`.
- [ ] Step 2 — Run, see them fail.
- [ ] Step 3 — Implement (sync handlers). The MCP tool builds its `RoundStore` from `Settings().trips_db` lazily like the trip tools do. Ask system prompt: "For 'what's winning / who hasn't voted', use vote_standings."
- [ ] Step 4 — Full suite green; commit `feat(hick-toors): rounds API and vote_standings MCP tool`.

### Task 5: UI — login, header, gating

**Files:** Create `static/auth.js`. Modify `static/index.html` (header `#auth-area`, `#auth-dialog`, script order … app → nature → auth → trips), `static/app.js` (the `api()` helper: a 401 response calls `window.onUnauthorized?.()` before throwing), `static/trips.js` (gating + "Created by"), `static/style.css`, `tests/ui/check_ui.py` (existing trip scenarios sign up first; new auth scenario), `README.md` (accounts bullet).

**Interfaces — Consumes:** Task 2 routes. **Produces (globals in auth.js):** `auth.user` (string | null), `auth.refresh() -> Promise`, `requireLogin(then: () => void)`, event `document.dispatchEvent(new CustomEvent("auth-changed"))` after login/logout.

- [ ] Step 1 — Implement per spec §UI (Header, Login dialog, Logged out, Trip editor "Created by"). Gated actions: ＋ Trip menu items, 💾 Save on picks, editor Save / Delete / ＋ Add (link) / Find nearby sights / item remove & reorder (they save) — each calls `requireLogin(() => <original action>)`. A 401 from any call opens the dialog (and, after login, the user repeats the click — no automatic replay of the failed request). Login dialog: tabs Log in / Sign up, Enter submits, errors from `detail` shown inline, focus on username, closes on success.
- [ ] Step 2 — UI check (`tests/ui/check_ui.py`): existing trip scenarios start by signing up (`nikos` / `password1`) so they keep passing; new scenario: logged out → ＋ Trip on a hike opens the login dialog → Sign up → the trip menu appears (action continued); header shows `👤 nikos · Log out`; logout → header `👤 Log in`; wrong password shows the message; editor shows "Created by nikos".
- [ ] Step 3 — `node --check` on changed JS, pytest green, UI check green; commit `feat(hick-toors): log in / sign up UI, trip editing behind the login`.

### Task 6: UI — vote dialog, badge, polling, editor line

**Files:** Create `static/vote.js`. Modify `static/index.html` (`#vote-btn` with `#vote-left`, `#vote-dialog`, script after trips.js), `static/trips.js` (editor round line → `openVote(tripId)`), `static/style.css`, `tests/ui/check_ui.py` (vote scenario), `README.md` (voting bullet).

**Interfaces — Consumes:** Task 4 routes, Task 5 `auth`/`requireLogin`/`auth-changed`, existing `showTripOnMap`-style helper used by the trips editor's Show on map (reuse, don't duplicate). **Produces:** `openVote(tripId?: string)`.

- [ ] Step 1 — Implement per spec §UI (Vote dialog open/closed/no-round states, Start a round form with `datetime-local` default now + 3 days and a checkbox per trip, opener controls with two-click confirm for Close now / Cancel round, Extend = a `datetime-local` + Save, Add trips = checkboxes of trips not yet in the round, `▸ Stays` expanders kept open across re-renders, counts → names tooltip/click, `⚠ name: No-go` lines with comments, "Moved your Love from X to Y" toast, 🏆 banner + Show on map + Start a new round). Polling: every 10 s while open, request sequence numbers so a stale answer never overwrites a newer one; badge `🗳️ Vote · N left` refresh every 60 s and on `auth-changed` / after each vote. Comment field saves with the next rating click or on blur (PUT with the current rating; no rating yet → disabled).
- [ ] Step 2 — UI check: user A signs up, starts a round with 2 trips (seed 2 trips via the API while logged in), Loves trip 1, Loves trip 2 → toast "Moved your Love…", trip 1 shows 🙂; votes a stay; ranking order shown; badge shows `· 0 left` for A; a second browser context signs up user B, sees A's votes within one poll (route the clock-free 10 s by calling the dialog's refresh or waiting ≤ 12 s), rates both trips with a No-go + comment `<b>no</b>` rendered as text; A closes the round (two clicks) → 🏆 banner names the top trip; vote buttons disabled. Own server on 8765 + throwaway TRIPS_DB.
- [ ] Step 3 — `node --check`, pytest, UI check green; commit `feat(hick-toors): 🗳️ voting rounds UI – ranking, badge, live updates`.
