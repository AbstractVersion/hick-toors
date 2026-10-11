# Group voting — design

Date: 2026-10-11 · Status: design approved in chat (parts 1–3); written spec awaiting review

## Goal

Help a group of friends agree on one trip. People create their own accounts, vote on saved trips and
on the stays inside each trip during a **voting round with a deadline**, watch a live ranking, and get
a clear 🏆 winner (trip + stay) when the round closes.

## Decisions (from the brainstorm)

| Topic | Decision |
|---|---|
| Vote style | 😍 **Love it** / 🙂 **OK with it** / 🚫 **No-go** per trip, and per stay within a trip |
| Limits | One rating per person per trip (and per stay). **1 Love per round** among trips; **1 Love per trip** among its stays. OK and No-go unlimited |
| Scoring | Love +2, OK +1, No-go −3. Ties: fewer No-gos → more Loves → earlier in the round |
| Login needed for | voting, rounds, creating/editing/deleting trips. Searching, map, viewing trips and results stay open |
| Sign-up | fully open: username + password, no code, no approval |
| Deciding | voting rounds with a deadline; the top trip wins automatically at the deadline |
| Visibility | everyone (also logged out) sees the live ranking and who voted what |
| Storage | new tables in the existing SQLite file (`TRIPS_DB`), stdlib only — no new dependency |

## Accounts (`app/accounts.py`)

- **Username:** 3–20 chars `[A-Za-z0-9_.-]`, stored as typed; uniqueness and login on the lowercase key
  (`Nikos` and `nikos` are the same account). **Password:** 8–200 chars, any characters.
- **Hashing:** `hashlib.scrypt(n=2**14, r=8, p=1, dklen=32)`, 16-byte random salt, stored as
  `scrypt$16384$8$1$<salt b64>$<hash b64>`; compare with `hmac.compare_digest`. A login for an unknown
  username still runs scrypt on a dummy hash (same timing).
- **Sessions:** `secrets.token_urlsafe(32)`; only its SHA-256 is stored. Cookie `hick_session`:
  `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` 30 days, `Secure` when the request came over https.
  Logout deletes the row and the cookie. Expired rows are deleted at startup and by the daily sweep.
- **CSRF:** SameSite=Lax keeps the cookie off cross-site POST/PUT/DELETE, and every state-changing
  endpoint takes a JSON body (a cross-site form cannot send `application/json` without a CORS preflight,
  which the app never allows).
- **Brute force:** 5 wrong passwords in a row for a username → that username is locked for 60 s
  (429 "Too many attempts – wait a minute"); counter in memory, reset on success.
- **Open sign-up guard rails:** at most 10 sign-ups per client IP per hour (in memory) and 200 accounts
  in total (409 "No more accounts can be created").
- **Password reset:** no e-mail. Server command `python -m app.accounts reset-password <username>`
  (prompts twice with `getpass`; in Docker: `docker compose exec hick-toors python -m app.accounts
  reset-password nikos`). It also logs that user out everywhere.

Tables:
```
users(id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
      pw_hash TEXT NOT NULL, created_at TEXT NOT NULL)
sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL)
```

### Auth API

| Route | Body | Result |
|---|---|---|
| `POST /api/auth/signup` | `{username, password}` | 201 `{username}` + cookie (logged in). 409 "That username is taken"; 422 rules; 429 IP limit |
| `POST /api/auth/login` | `{username, password}` | 200 `{username}` + cookie. 401 "Wrong username or password"; 429 locked |
| `POST /api/auth/logout` | – | 204, cookie cleared |
| `GET /api/auth/me` | – | `{username}` or `{username: null}` |

A FastAPI dependency `current_user` (optional) / `require_user` (401 "Log in first") reads the cookie.

### Trips behind the login

- Logged in required (401 "Log in first"): `POST/PUT/DELETE /api/saved-trips…`, `/items`,
  `/nearby-sights`. Open: list, get, `export.md`, `print`, `/api/stays/lookup` and everything else.
- `SavedTrip` gains `created_by: str | None = None` (username), set by the server on create, kept on
  replace (the client cannot set or change it). Old trips load with `None` ("—" in the UI).
- Any logged-in user may edit any trip (a group of friends). Ask Claude / MCP stay as they are.

## Rounds and votes (`app/votes_store.py`)

Tables (same file):
```
rounds(id TEXT PRIMARY KEY, title TEXT NOT NULL, opener_id INTEGER NOT NULL, created_at TEXT NOT NULL,
       closes_at TEXT NOT NULL, status TEXT NOT NULL,          -- open | closed | cancelled
       closed_at TEXT, result TEXT)                            -- result: frozen JSON RoundView at close
round_trips(round_id TEXT, trip_id TEXT, position INTEGER, PRIMARY KEY(round_id, trip_id))
trip_votes(round_id, trip_id, user_id, rating, comment, updated_at, PRIMARY KEY(round_id, trip_id, user_id))
stay_votes(round_id, trip_id, stay_id, user_id, rating, comment, updated_at,
           PRIMARY KEY(round_id, trip_id, stay_id, user_id))
```
`rating ∈ {love, ok, nogo}`; `comment` ≤ 140 chars (optional, trimmed, empty → NULL).

### Rules

- **One open round at a time.** Opening another → 409 "A round is already open".
- **Open:** title 1–80 chars; `closes_at` between now + 1 h and now + 30 days; 1–50 trip ids that exist
  (unknown ids → 422). Positions follow the given order.
- **Vote:** `PUT …/trips/{trip_id}/vote {rating, comment?}` sets or replaces your rating. Love on a trip
  while you already Love another trip in the round turns the old one into **OK** and the answer says
  `moved_love_from: <trip_id>`. Same for stays within one trip. `DELETE …/vote` clears it. Voting on a
  trip not in the round → 404; on a stay not (any more) in the trip → 404; on a closed/cancelled round →
  409 "This round is closed".
- **Opener only** (others → 403 "Only the opener can do this"): `PATCH /api/rounds/{id}`
  `{closes_at?, add_trip_ids?}` (new deadline must be later than the current one and ≤ now + 30 days;
  added trips go to the end), `POST /api/rounds/{id}/close` (close now), `POST /api/rounds/{id}/cancel`
  (status cancelled, no winner, kept in history).
- **Closing:** no background job. Every rounds read/write first *settles*: an open round whose
  `closes_at` has passed is closed (`closed_at = closes_at`) inside `BEGIN IMMEDIATE`, and its final
  `RoundView` is frozen into `result`, so later trip edits/deletes don't change a past result.
- **Trips changing during a round:** a deleted trip drops out of the ranking (its votes are ignored);
  a removed stay's votes are ignored; trips created later aren't in the round unless the opener adds them.

### Ranking and winner

- Trip score = 2·loves + 1·oks − 3·nogos over the trip's votes; unrated = no contribution.
- Order: score desc, nogos asc, loves desc, position asc. Stays within a trip ranked the same way
  (position = order in the trip).
- **Winner** at close: the first trip with at least one vote, and its first stay with at least one vote
  (none if its stays got no votes). No votes at all → closed with no winner.
- **Waiting for:** every account that has not rated every trip still in the round (stays don't count).
  With open sign-up this lists all accounts — fine for a group of friends.

### Rounds API

| Route | Result |
|---|---|
| `GET /api/rounds/current` | the open round's `RoundView`, else the most recently closed one if it closed < 7 days ago, else `null` |
| `GET /api/rounds` | history: `[{id, title, status, opener, closes_at, closed_at, winner_name}]`, newest first |
| `GET /api/rounds/{id}` | `RoundView` (frozen `result` for closed rounds) |
| `POST /api/rounds` | `{title, closes_at, trip_ids}` → 201 `RoundView` |
| `PATCH / close / cancel` | as above → `RoundView` |
| `PUT/DELETE …/trips/{trip_id}/vote`, `…/trips/{trip_id}/stays/{stay_id}/vote` | `{round: RoundView, moved_love_from: str \| null}` |

`RoundView`:
```
{id, title, opener, status, created_at, closes_at, closed_at,
 trips: [{trip_id, name, rank, score, counts: {love, ok, nogo},
          votes: [{username, rating, comment}], my_rating,
          stays: [{stay_id, name, rank, score, counts, votes, my_rating}]}],
 waiting_for: [username], me: {rated, total, love_trip_id} | null,
 winner: {trip_id, trip_name, stay_id, stay_name} | null}
```
`my_rating` / `me` are for the caller (null when logged out; in a frozen result they are recomputed for
the caller from the frozen votes).

### Ask Claude / MCP

Read-only `vote_standings()` → JSON of the current `RoundView` (no `me`), or `{"round": null}`. System
prompt: mention it for "what's winning / who hasn't voted".

## UI

New files `static/auth.js` (login state, dialog, `requireLogin(then)`) and `static/vote.js` (vote
dialog); script order … app → nature → auth → trips → vote.

- **Header:** `🗳️ Vote` button (`#vote-btn`) next to 🧳 My trips; when a round is open and you are logged
  in with unrated trips it reads `🗳️ Vote · N left` with a dot. Right side: `👤 Log in`, or
  `👤 <name> · Log out`.
- **Login dialog** (`#auth-dialog`): Log in / Sign up tabs, username + password (+ repeat on sign-up),
  errors inline. `requireLogin(then)` opens it and runs `then` after success.
- **Vote dialog** (`#vote-dialog`), open round: title, "closes in 2 d 4 h" (+ local date/time), "Waiting
  for: …", "You: 3 of 5 trips rated", "😍 Your Love: …"; trips in live rank order: `#rank name score`,
  counts `😍2 🙂1 🚫0` (click/hover → names), `⚠ <name>: No-go` for every No-go, your three buttons
  (current one highlighted), 💬 comment field (saved with the vote, ≤ 140), `▸ Stays (n)` expanding to
  the stays with the same controls, `Show on map`, `Open trip`. A moved Love shows "Moved your Love from
  X to Y". Opener: `Close now` (asks twice), `Extend`, `Add trips`, `Cancel round` (asks twice).
- **No open round:** "Start a round" form — title, closing date/time (`datetime-local`, default now +
  3 days), a checkbox per saved trip (all ticked). Below: history list; a past round opens read-only.
- **Closed:** 🏆 banner "<trip> wins – stay: <stay>" (or "No winner – nobody voted"), Show on map, final
  table, buttons disabled, and a `Start a new round` button (opens the form above). A cancelled round is
  never shown as current.
- **Polling:** while the vote dialog is open, `GET /api/rounds/current` every 10 s (stale answers
  ignored; a newer local vote is not overwritten); the header badge refreshes every 60 s and after
  login/logout/vote.
- **Trip editor:** "Created by <name>" line; when the trip is in the open round: "🗳️ #2 in '<title>' ·
  😍1 🙂2 🚫1 → Vote" (opens the vote dialog at that trip).
- **Logged out:** everything visible; vote buttons, ＋ Trip, 💾 Save, Save/Delete in the editor, ＋ Add,
  Find nearby sights and Start a round call `requireLogin` first, then carry on. A 401 from any call opens
  the login dialog.
- All text via `textContent`; names and comments are user input — never `innerHTML`.

## Errors

| Case | Answer |
|---|---|
| not logged in on a protected route | 401 "Log in first" |
| wrong password / unknown user | 401 "Wrong username or password" |
| locked username / sign-up IP limit | 429 |
| username taken / account cap / round already open / round closed | 409 |
| not the opener | 403 |
| unknown round, trip not in round, stay not in trip | 404 |

## Out of scope

Several groups, private trips, e-mail, password reset by e-mail, admin roles, notifications, rounds
with more than one winner, live push (polling is enough).

## Testing

- Accounts: hash format + verify, wrong password, case-insensitive usernames, rules, taken, lockout
  after 5 and reset on success, IP sign-up limit, 200 cap, sessions (create, expiry, logout), reset CLI.
- API: cookie flags, `me`, 401 on every protected trip route and open GETs still open, `created_by` set
  and not changeable.
- Rounds: open rules (one open, deadline bounds, trip ids), votes (replace, clear, comment trim/limit),
  Love moves (trip and stay level), opener-only actions, extend/add trips, lazy close + frozen result,
  deleted trip / removed stay ignored, ranking order incl. every tie-break, winner rules, waiting_for,
  history, `current` 7-day rule, concurrency (two votes at once keep one Love).
- MCP `vote_standings`.
- Playwright (own server on 8765, throwaway TRIPS_DB, never 8000): sign up, logged-out gating opens the
  login dialog and continues, start a round, vote incl. a moved Love and a stay vote, ranking order,
  badge count, opener close → 🏆 banner, second user (second browser context) sees the votes by polling.
