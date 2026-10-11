# hick-toors — User Emojis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Each account picks a unique funny emoji, shown next to its name everywhere.

**Architecture:** `users.emoji` column (partial unique index) + `EMOJIS` set in `app/accounts.py`; auth routes gain emoji on sign-up, `PUT /api/auth/emoji`, `GET /api/auth/emojis`, `GET /api/users`; the UI keeps a username→emoji map (`static/auth.js`) and decorates names in header, vote dialog and trips.

**Tech Stack:** Python 3.10, FastAPI, sqlite3, pytest; vanilla JS; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-11-user-emojis-design.md` — binding for values and messages.

## Global Constraints

- Project folder `/Users/abstractversion/Documents/fun-staff/hiking-mcp/.claude/worktrees/hick-toors/hick-toors`, branch `feat/hick-toors`. Never read/print/commit `.env`. Plain git commands only.
- Existing databases (users without the column, the user's 6 real trips) keep working; migration is additive.
- Messages verbatim: "Pick an emoji from the list", "That emoji is taken – pick another", "All emojis are taken".
- Works on phones (the mobile layout from the mobile plan is already in place): picker buttons ≥ 40 px, wraps, no horizontal scroll at 390 px.
- User text via textContent; no window.confirm/alert/prompt; no new dependencies.
- `env -u VIRTUAL_ENV uv run pytest -q` green; `node --check`; UI checks only on your own server on 8765 with a fresh TRIPS_DB, never port 8000.
- Commits end with a blank line + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. Two friends signing up at the same moment with the same emoji → exactly one gets it, the other a 409 and a refreshed picker. (Task 1 test; Task 2 UI handles 409 by reloading taken emojis.)
2. Existing account (created before this feature) logs in → no crash anywhere names are shown; 👤 until picked. (Task 1 migration test; Task 2 UI check.)
3. Emoji with variation selectors (🌶️, ☠️) stored and compared exactly as in the set. (Task 1 test.)

---

### Task 1: Backend — emoji column, set, validation, routes

**Files:** Modify `app/accounts.py`, `app/auth_routes.py`, `tests/test_accounts.py`, `tests/test_auth_api.py`, `tests/builders.py` (`login_client` passes an emoji — pick a distinct one per username), `README.md` (accounts bullet: emoji).

**Interfaces — Produces:** `EMOJIS: tuple[str, ...]`; `AccountStore.signup(username, password, ip, emoji)`; `AccountStore.set_emoji(username, emoji)`; `AccountStore.emoji_of(username) -> str | None`; `AccountStore.users() -> list[tuple[str, str | None]]`; `AccountStore.taken_emojis() -> list[str]`; exceptions `EmojiTaken`, `NoEmojisLeft`, `ValueError("Pick an emoji from the list")`; routes per spec §API (`me` gains `emoji`).

- [ ] Step 1 — Failing tests per spec §Testing (store + API), incl. a database file created by the old schema (users table without `emoji`) opening fine and accepting `set_emoji`; concurrent same-emoji sign-ups (threads) → one `EmojiTaken`.
- [ ] Step 2 — Implement (additive migration in `AccountStore.__init__`; unique violation → `EmojiTaken`).
- [ ] Step 3 — Full suite green (update existing tests that sign up to pass an emoji without weakening assertions); commit `feat(hick-toors): unique user emojis – column, set, routes`.

### Task 2: UI — picker, names with emojis everywhere

**Files:** Modify `static/auth.js` (users map `auth.emojiOf(name)`, `auth.label(name)` → "🤡 nikos" / "👤 nikos", picker component, sign-up emoji step, Change emoji dialog from the header name, first-login prompt for accounts without one), `static/index.html`, `static/style.css`, `static/vote.js` and `static/trips.js` (use `auth.label` wherever usernames are shown), `tests/ui/check_ui.py`.

**Interfaces — Consumes:** Task 1 routes. **Produces:** `auth.label(username)`, `auth.emojiOf(username)`, `auth.refreshUsers()`; event `auth-changed` also after an emoji change.

- [ ] Step 1 — Implement per spec §Decisions (picker grid, taken disabled with "taken by <name>", 409 → reload taken + message, Change emoji dialog, first-login prompt skippable, decorate names in header, vote dialog, trip editor, trips list; refresh map on load / auth-changed / vote poll).
- [ ] Step 2 — UI check per spec §Testing (desktop and phone viewport).
- [ ] Step 3 — `node --check`, pytest, UI check green; commit `feat(hick-toors): emoji picker and names with emojis everywhere`.
