# User emojis — design

Date: 2026-10-11 · Status: requested in chat ("allow users to choose their emojis … funny emojis like clowns,
eggplants … 2 users can't have the same emoji; emoji + name is how we identify them")

## Goal

Every account has a unique funny emoji shown next to its username everywhere, so friends recognise each
other at a glance.

## Decisions

- **Curated set** `EMOJIS` (app/accounts.py), ~100 single emojis, funny/food/animals/creatures, e.g.
  🤡 🍆 🍑 💩 👽 🤖 👻 🦄 🐸 🐔 🦆 🐙 🦖 🌶️ 🥑 🍕 🌮 🧀 🦩 🐷 🙈 🦥 🦦 🐌 🦀 🍄 🌵 🎃 🤠 🥸 🤓 😈 👹 💀 🧟 🧛 🧙
  🥷 🐉 🦈 🐊 🦔 🦇 🐝 🔥 💣 🎸 🛸 🚀 🍌 🥥 🥒 🍔 🍩 🍺 … (no duplicates, no flags, no skin-tone variants).
  Only emojis from the set are accepted (422 "Pick an emoji from the list").
- **Unique:** `users.emoji TEXT` (nullable) with a partial unique index (`WHERE emoji IS NOT NULL`); added
  with `ALTER TABLE` when missing (existing databases keep working). Taken → 409 "That emoji is taken –
  pick another". All taken → sign-up 409 "All emojis are taken".
- **Sign-up** requires `emoji` (`{username, password, emoji}`). **Change** later: `PUT /api/auth/emoji
  {emoji}` (logged in). Existing accounts without one: after login a picker opens once per page load
  (skippable); they show 👤 until they pick.
- **API:** `GET /api/auth/me` → `{username, emoji}` (emoji null when none); `GET /api/auth/emojis` →
  `{emojis: [...], taken: [...]}` (public); `GET /api/users` → `[{username, emoji}]` (public, sorted) — the UI
  decorates names client-side, so RoundView and trip JSON stay unchanged.
- **Shown as** `<emoji> <name>` (👤 when none) in: header, vote dialog (voters, counts' names, ⚠ No-go lines,
  Waiting for, opener, Your Love line unaffected), trip editor "Created by", trips list "by". The users map
  refreshes on load, after login/sign-up/emoji change and with each vote poll.
- **Picker UI:** a grid of buttons (≥ 40 px, phone-friendly, wraps), taken ones disabled with a tooltip
  "taken by <name>"; the current pick highlighted; in the sign-up tab and in a small "Change emoji" dialog
  opened by clicking your name in the header.
- MCP `vote_standings` unchanged.

## Testing

Store: emoji required on signup, membership check, uniqueness incl. two concurrent sign-ups with the same
emoji (one wins), change emoji (own current allowed, taken rejected), migration on a database created
without the column. API: me/emojis/users shapes, 409/422 messages, PUT requires login. UI check: sign-up
picks an emoji, a second user sees it disabled, header and vote names show the emoji, change emoji,
existing account without emoji gets the picker and can skip; phone viewport picker usable (≥ 40 px, no
horizontal scroll).
