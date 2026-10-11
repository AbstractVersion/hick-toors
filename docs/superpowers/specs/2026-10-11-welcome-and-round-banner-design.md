# Login first + active-round visibility — design

Date: 2026-10-11 · Status: requested in chat ("you need to login or sign up before you do anything else, and if
there is an active round you should be able to see it … on the phone it's not easy to login and know there is
a voting happening")

## Goal

Nobody uses the site anonymously, and nobody misses an open voting round — especially on phones.

## Decisions

- **Welcome / login wall (UI):** on page load, until `auth.user` is set, a full-screen `#welcome` screen covers
  the app (desktop and phone): `🥾 hick-toors` + one line ("Plan hikes and vote on trips with your friends"),
  and the existing Log in / Sign up form (with the emoji picker) embedded — the same code as `#auth-dialog`,
  not a copy. The rest of the page is `inert` behind it. After login/sign-up the wall disappears and the app
  starts; after logout it comes back. No ✕ on the wall. While `/api/auth/me` is loading, show the wall with a
  spinner (no flash of the app).
- **Server unchanged:** write routes already require a login; read routes stay open (a later change could
  lock them down). The wall is a UX gate.
- **Account in the phone top bar:** on phones the top bar shows the user's emoji (tap → Change emoji / Log out
  menu) next to ☰; the login button is no longer only inside the ☰ panel.
- **Active-round banner:** while a round is open, a banner is visible on the main screen — desktop: under the
  header; phone: floating at the top of the map, below the top bar — `🗳️ Voting open: <title> · <N> left ·
  closes in <time>` (or `· you're done ✓` when N = 0). Tapping it opens the Vote dialog. Refreshes with the
  existing 60 s badge timer and after votes. Hidden when there is no open round.
- **Auto-open:** right after login (or page load while logged in), if a round is open and the user has unrated
  trips, open the Vote dialog once per round per browser session (sessionStorage key with the round id,
  try/catch).
- All user text via textContent; works at 390 px with no horizontal scroll; tap targets ≥ 40 px.

## Testing

UI check: logged out → wall covers the app (map/search not reachable), sign up through the wall (with emoji)
→ app usable; logout → wall again; open round + unrated trips → Vote dialog opens by itself once (reload in
the same session doesn't reopen; a new round does); banner text and tap → Vote; banner shows "you're done ✓"
after rating all; phone viewport: wall full-screen and usable, top bar shows the emoji, banner visible on the
map tab, no side scroll. Existing scenarios adapted to log in first.
