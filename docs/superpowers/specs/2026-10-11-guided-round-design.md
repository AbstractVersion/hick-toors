# Guided voting round (share link, participants, step-by-step guide) — design

Date: 2026-10-11 · Status: UX agreed in chat (participants = those who join, planning for people without an
account; stays inside each trip step; same order for everyone; your summary → group overview).

## Goal

The free-form Vote screen is too open for an indecisive group. A round gets a **share link** that takes each
friend through a guided experience: join (signing up first if needed) → see each trip one by one, look at it
on the map, rate it and pick where to sleep → review your picks → see the group overview. Only for round
participation; the rest of the app stays free-form.

## Participants (backend)

- Table `round_participants(round_id TEXT, user_id INTEGER, joined_at TEXT, PRIMARY KEY(round_id, user_id))`
  in the same SQLite file. Participants of a round = rows in that table ∪ the opener ∪ anyone who voted in it
  (so rounds created before this change keep working without a migration).
- `POST /api/rounds/{id}/join` (login required) → `RoundView`; idempotent; a closed/cancelled round → 409
  "This round is closed". Opening a round joins the opener; casting any vote joins the voter.
- `RoundView` gains `participants: [username]` (join order) and `me.joined: bool`. `waiting_for` = participants
  who haven't rated every trip (no longer all accounts); the frozen result of a closed round keeps its
  participants. MCP `vote_standings` includes `participants`.

## Share link

- **The opener gets the link right away (user requirement):** submitting "Start a round" shows a
  `🎉 Round started – invite your friends` screen: the link in a selectable field, `📤 Share` (native share
  sheet, e.g. WhatsApp/Viber, where `navigator.share` exists), `📋 Copy link` (→ "Link copied"), a one-line
  hint ("Send it to everyone who should vote – they can sign up from the link"), and `Start voting myself` (→
  the guide). The opener sees this before anything else after creating the round.
- URL: `<origin>/?round=<round id>`. The Vote dialog (open round) also shows `🔗 Share link` at any time
  (for everyone, so participants can forward it): on phones with
  `navigator.share` → native share sheet (title + text "Vote on '<title>' – closes <date>"); otherwise copy to
  clipboard and show "Link copied" plus the URL in a selectable field.
- Opening the link: logged out → the welcome wall (see the login-first spec) shows the invitation on top:
  `🗳️ <opener emoji> <opener> invites you to vote: <title> · closes <date>` and the Sign up / Log in form
  (new friends sign up there, emoji included). After login: not joined → **Join screen**; joined → the guide
  resumes; closed round → the 📊 Overview of that round with the winner. Unknown id → toast "That voting
  round doesn't exist" and the normal app. The `round` query parameter is removed from the address bar after
  it has been handled (`history.replaceState`).

## Join screen

Title, opener, deadline, who's in (emojis), how it works in three lines ("Rate every trip: 😍 love it (one per
round), 🙂 OK, 🚫 no-go", "A 🚫 counts a lot – use it when you really can't go", "The top trip wins when the
round closes"), buttons `Join and start` / `Not now`.

## Guide (full-screen on phones, large dialog on desktop: `#guide`)

- **Progress:** "Trip 2 of 5" with dots (rated = filled, current = ring); tapping a dot jumps there.
- **Trip step:** name, dates/nights/guests, the opener's notes; hikes (name, distance, duration, ascent,
  difficulty, ↗ link); stays (photo thumb, name, price total + per night, rating, 🐕 badge, ↗ link); nature
  sights count by kind; `🗺️ See on map` → closes the guide, shows the trip on the map (existing Show on map)
  and a floating pill `← Back to voting (Trip 2 of 5)` that reopens the guide at the same step (phones: above
  the tab bar; desktop: top centre).
- **Rate:** 😍 / 🙂 / 🚫 big buttons + optional comment (same rules as the Vote screen: one 😍 per round — moving
  it shows "Moved your Love from X"), saved immediately.
- **Where would you sleep?** right below, the trip's stays with 😍/🙂/🚫 (one 😍 per trip). Collapsed with
  "You said no-go – show anyway" when the trip is rated 🚫. Hidden when the trip has no stays.
- **Navigation:** `← Back`, `Next →` (enabled always; an unrated trip shows "Decide later" instead of Next),
  `✕` closes (progress is the server state — nothing to lose).
- **Summary:** each trip with your rating, comment and your stay pick; tap one to jump to its step; warnings
  for unrated trips; your 😍; `Done` → closes the guide and opens the Vote dialog on the 📊 Overview tab.
- **Resume:** the guide opens at the first trip you haven't rated (or the summary when all are rated).
- **Entry points besides the link:** the active-round banner (`Join` when not joined, `Continue voting · N
  left` when joined with unrated trips, `See results` when done) and the auto-open after login (joined +
  unrated → the guide; not joined → the Join screen), once per round per session.
- Live: the guide uses the same RoundView/polling as the Vote dialog; the stale-answer rules apply.
- All user text via textContent; ≥ 40 px targets; no horizontal page scroll at 360–390 px.

## Out of scope

Notifications/e-mail, per-person deadlines, anonymous voting, inviting specific accounts.

## Testing

Backend: join (idempotent, closed → 409, login required), participants union (opener, voters, joined),
waiting_for over participants, frozen participants, MCP. UI: share link (copy fallback + navigator.share
stub), a NEW user opens the link logged out → invitation on the wall → signs up with emoji → Join screen →
guide; trip step content; See on map → map shows the trip + Back pill → same step; rate trip + stay; No-go
collapses stays; Love move message; dots jump; summary edit → back to step; Done → Overview; reopen link →
resumes at first unrated; closed round link → Overview with winner; unknown id → toast; phone viewport for
the whole journey (no side scroll, ≥ 40 px).
