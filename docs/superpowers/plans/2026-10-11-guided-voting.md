# hick-toors — Guided Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make voting usable for an indecisive group: round participants, a who-voted-what overview, a login-first welcome screen with an always-visible active-round banner, and a share link that guides each friend through the round step by step.

**Architecture:** Backend: `round_participants` + join route in `app/votes_store.py` / `app/rounds_routes.py`, `RoundView.participants` + `me.joined`. Frontend: Overview tab in `static/vote.js`; welcome wall + banner + phone account button in `static/auth.js` / `static/mobile.js` / `static/index.html`; new `static/guide.js` for the Join screen, the step-by-step guide, the summary and link handling.

**Tech Stack:** Python 3.10, FastAPI, sqlite3, pytest; vanilla JS; Playwright UI check.

**Specs (binding, read the ones your task names):**
- `docs/superpowers/specs/2026-10-11-guided-round-design.md` (participants, share link, Join screen, guide)
- `docs/superpowers/specs/2026-10-11-vote-overview-design.md` (Overview grid)
- `docs/superpowers/specs/2026-10-11-welcome-and-round-banner-design.md` (login wall, banner, phone top bar, auto-open)
- Existing: `docs/superpowers/specs/2026-10-11-group-voting-design.md`, `…-mobile-design.md`, `…-user-emojis-design.md`

## Global Constraints

- Project folder `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors`, branch `feat/hick-toors`. Never read/print/commit `.env`. Plain git commands only (no `cd x && git`, no `git -C`).
- Existing databases keep working (additive tables only); rounds created before participants existed still work (participants = table ∪ opener ∪ voters).
- Works on desktop and phones: no horizontal page scroll at 360–390 px, tap targets ≥ 40 px, 16 px inputs; desktop layout of existing screens unchanged.
- User text (usernames, comments, titles, notes) via textContent only; no window.confirm/alert/prompt; no new dependencies.
- `env -u VIRTUAL_ENV uv run pytest -q` green; `node --check` on changed JS; UI checks only against your own server on 8765 with a fresh throwaway TRIPS_DB — never port 8000 (the user's live app) and never the VM.
- Commits end with a blank line + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. A friend with no account opens the share link on a phone → wall shows the invitation → sign up with an emoji → Join → guide, with no dead ends. (Task 4 UI check.)
2. "See on map" from the guide and back → returns to the same step with ratings intact; the map shows that trip. (Task 4.)
3. Rounds already in the database (opened before participants) → opener + voters count as participants; waiting_for doesn't list unrelated accounts. (Task 1 test.)
4. The welcome wall never flashes the app before /me answers, and logout brings it back. (Task 3.)
5. Overview grid with many participants / long trip names on a phone → scrolls inside its box, trip column pinned, page doesn't scroll sideways. (Task 2.)

---

### Task 1: Round participants (backend)

**Files:** Modify `app/votes_store.py`, `app/rounds_routes.py`, `app/models.py` (RoundView `participants`, Me `joined`), `app/mcp_tools.py` (participants in vote_standings), `tests/test_votes_store.py`, `tests/test_rounds_api.py`.

**Produces:** `RoundStore.join(round_id, user) -> RoundView`; route `POST /api/rounds/{id}/join`; `RoundView.participants: list[str]` (join order: opener first); `Me.joined: bool`; `waiting_for` over participants; opener auto-joined on open; any vote joins.

- [ ] Step 1 — Failing tests per the guided-round spec §Participants and §Testing (backend): join idempotent, closed/cancelled → RoundClosed (409 "This round is closed"), login required (401), participants union for a round whose table rows are missing (simulate a pre-feature round: insert votes directly without participant rows), waiting_for excludes non-participant accounts, frozen result keeps participants, MCP output includes participants.
- [ ] Step 2 — Implement (table created IF NOT EXISTS in RoundStore init; inserts inside the existing BEGIN IMMEDIATE write paths).
- [ ] Step 3 — Full suite green (update existing expectations that assumed waiting_for = all accounts without weakening them); commit `feat(hick-toors): round participants – join, waiting list over participants`.

### Task 2: 📊 Overview grid

**Files:** Modify `static/vote.js`, `static/index.html`, `static/style.css`, `tests/ui/check_ui.py`.

**Consumes:** Task 1 `participants`; `auth.label` / `auth.emojiOf` from the emoji feature.

- [ ] Step 1 — Implement per the overview spec (tabs in the Vote dialog with persisted choice, header, grid with participants as columns, stays rows with `▸ n more stays`, comment popover, No-go tint/⚠, phone horizontal scroll inside the box with a sticky name column, trip name → Vote tab at that trip, closed/past rounds).
- [ ] Step 2 — UI check per the overview spec §Testing (desktop + phone).
- [ ] Step 3 — `node --check`, pytest, UI check green; commit `feat(hick-toors): 📊 vote overview grid`.

### Task 3: Login-first welcome wall, active-round banner, phone account button

**Files:** Modify `static/auth.js`, `static/mobile.js`, `static/vote.js` (banner data), `static/index.html`, `static/style.css`, `tests/ui/check_ui.py` (all scenarios now log in first), `README.md`.

- [ ] Step 1 — Implement per the welcome spec (wall reusing the auth form code with an invitation slot; inert background; no flash while /me loads; logout → wall; phone top-bar account button with Change emoji / Log out; banner with Join / Continue voting · N left / See results ✓ — all opening the Vote dialog for now; auto-open once per round per session).
- [ ] Step 2 — UI check per the welcome spec §Testing (desktop + phone); adapt every existing scenario to pass through the wall.
- [ ] Step 3 — `node --check`, pytest, UI check green; commit `feat(hick-toors): login-first welcome screen and active-round banner`.

### Task 4: Share link, Join screen, step-by-step guide, summary

**Files:** Create `static/guide.js`. Modify `static/index.html` (guide dialog, back-to-voting pill, script order … vote → guide → mobile last), `static/style.css`, `static/vote.js` (🔗 Share link; Done → Overview), `static/auth.js` (invitation line on the wall), `static/trips.js` (reuse showTripOnMap), `tests/ui/check_ui.py`, `README.md`.

**Consumes:** Task 1 join/participants; Task 2 Overview tab; Task 3 wall slot, banner, auto-open (re-point them to Join screen / guide / Overview).

- [ ] Step 1 — Implement per the guided-round spec §Share link, §Join screen, §Guide (incl. resume, See on map + Back pill, stays section with No-go collapse, dots, summary, Done → Overview, unknown/closed round handling, `?round=` cleanup).
- [ ] Step 2 — UI check per the guided-round spec §Testing (desktop + full phone journey for a brand-new user).
- [ ] Step 3 — `node --check`, pytest, UI check green; commit `feat(hick-toors): 🔗 share link and guided voting journey`.
