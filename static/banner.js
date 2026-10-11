"use strict";
// 🗳️ Active-round banner + auto-open. Loaded after guide.js; uses $ (app.js), auth (auth.js), closesIn /
// openVoteOverview (vote.js) and openJoin / openGuide / guideLinkPending (guide.js). vote.js fires "round-changed" with every GET /api/rounds/current answer it applies (the 60 s badge poll,
// the dialog's polls, votes), so the banner needs no timer of its own.
// While a round is open the banner shows "🗳️ Voting open: <title> · closes in <time>" plus one action:
//   Join (not a participant) · Continue voting · N left (participant with unrated trips) · See results ✓ (done).
// Desktop: a strip under the header; phone: at the top of the Map tab, below the top bar (style.css).
// Auto-open: the first round answer after a page load / log in opens the round once per round per browser session
// (sessionStorage) when the user isn't a participant yet (the Join screen) or still has unrated trips (the guide).
// The actions and the auto-open go through bannerAction → openJoin / openGuide (guide.js) / openVoteOverview.
// The title is user input: textContent only.
const banner = {
  round: null,
  armed: true,  // the next round answer while logged in may auto-open (re-armed by every log in)
};
const AUTO_OPEN_KEY = "hick-toors:auto-opened:";

// What the banner offers for this round: "join" | "continue" | "done" (null: no banner).
function bannerKind(round) {
  if (!round || round.status !== "open" || !auth.user || !round.me) return null;
  if (!round.me.joined) return "join";
  return round.me.rated < round.me.total ? "continue" : "done";
}
// Join → the Join screen; Continue voting → the guide at the first trip still to rate; See results → the Overview.
function bannerAction(kind) {
  const round = banner.round;
  if (kind === "join" && round) openJoin(round);
  else if (kind === "continue" && round) openGuide(round);
  else openVoteOverview();
}

function renderBanner(round) {
  banner.round = round;
  const kind = bannerKind(round);
  const box = $("round-banner");
  const wasHidden = box.hidden;
  box.hidden = !kind;
  if (kind) {
    $("round-banner-text").textContent = `🗳️ Voting open: ${round.title} · ${closesIn(round.closes_at)}`;
    const left = Math.max(0, round.me.total - round.me.rated);
    $("round-banner-action").textContent = kind === "join" ? "Join"
      : kind === "continue" ? `Continue voting · ${left} left` : "See results ✓";
    $("round-banner-action").dataset.kind = kind;
  }
  if (wasHidden !== box.hidden) window.mapShown?.();  // the map got shorter / taller: Leaflet re-measures
}
// Whatever changes the map's height – the banner wrapping onto another line, a notice ("Showing trip …") above it –
// Leaflet re-measures (otherwise a grey band, e.g. after the guide's 🗺️ See on map).
if (window.ResizeObserver) new ResizeObserver(() => window.mapShown?.()).observe($("map"));

function autoOpened(roundId) {
  try { return sessionStorage.getItem(AUTO_OPEN_KEY + roundId) === "1"; } catch { return false; }
}
function markAutoOpened(roundId) {
  try { sessionStorage.setItem(AUTO_OPEN_KEY + roundId, "1"); } catch { /* storage blocked: opens once per load */ }
}
// Marked only once it really opened: skipped because another dialog was open (or a share link is being handled,
// which opens what it links to), it opens on a later page load / log in.
function maybeAutoOpen(round) {
  if (!banner.armed || !auth.user) return;
  banner.armed = false;  // only the first answer after a page load / log in
  const kind = bannerKind(round);
  if (kind !== "join" && kind !== "continue") return;
  if (autoOpened(round.id) || guideLinkPending() || document.querySelector("dialog[open]")) return;
  markAutoOpened(round.id);
  bannerAction(kind);
}

document.addEventListener("round-changed", (event) => {
  renderBanner(event.detail);
  maybeAutoOpen(event.detail);
});
document.addEventListener("auth-changed", () => {
  banner.armed = true;
  if (!auth.user) renderBanner(null);
});
$("round-banner-action").addEventListener("click", (event) => bannerAction(event.currentTarget.dataset.kind));
