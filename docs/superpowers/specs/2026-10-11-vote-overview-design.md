# Vote overview grid — design

Date: 2026-10-11 · Status: UX chosen in chat ("Who-voted-what grid")

## Goal

One screen that shows what is actually happening in a round: who voted what on every trip and stay, who
hasn't voted, and what's leading — so the group can see where they agree and where the No-gos are.

## Decisions

- **Where:** the 🗳️ Vote dialog gets two tabs at the top: `🗳️ Vote` (today's screen) and `📊 Overview`. The
  choice is remembered per browser (localStorage `hick-toors:vote-tab`, wrapped in try/catch). Works for open
  and closed rounds (closed = final grid) and for past rounds opened from the history. Visible logged out.
- **Header:** round title · `closes in …` (or `Closed …` / 🏆 winner line) · `Leader: <trip> (+score)` ·
  `<n>/<m> people done` (done = rated every trip; m = all accounts, like Waiting for).
- **Grid:** rows = trips in rank order: `#rank`, trip name, score at the end. Columns = people: every account
  (from `GET /api/users`), header cell = their emoji (👤 if none) with the name as title/tooltip and under the
  emoji in small text; people who are done first, then the rest; the viewer's own column highlighted.
  Cell = 😍 / 🙂 / 🚫 or `·` (not rated). A cell with a comment shows a small dot; tapping/clicking it opens
  a small popover: `<emoji> <name>: <rating label> – “comment”` (Escape / tap outside closes).
- **Stays:** under each trip, its stays in their own rank order, indented with 🏡, same cells; the leading stay
  always visible, further stays behind `▸ n more stays` (expanded state kept across refreshes).
- **No-go emphasis:** 🚫 cells get a red tint; a row with any No-go shows a ⚠ next to the score.
- **Phones:** the grid scrolls horizontally inside its own box (the page itself never scrolls sideways); the
  trip-name column stays pinned (`position: sticky; left: 0`); cells ≥ 40 px.
- **Live:** re-renders from the same RoundView the Vote tab uses (10 s polling while the dialog is open); no
  backend changes. Clicking a trip name switches to the Vote tab at that trip.
- All user text via textContent.

## Testing

UI check: two users vote on 2 trips (+ a stay) with a comment; Overview shows rows in rank order with
scores, columns for both users (emoji headers), the right symbol in every cell, `·` for unrated, the
comment popover, `1/2 people done` progress, leader line; the tab choice persists across reopen; phone
viewport: grid scrolls inside its box, no page side-scroll, name column pinned, cells ≥ 40 px; closed round
shows the final grid with the winner line.
