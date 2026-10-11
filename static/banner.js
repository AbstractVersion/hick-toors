"use strict";
// 🗳️ Active-round banner + auto-open. Loaded after vote.js; uses $ (app.js), auth (auth.js) and closesIn / openVote
// (vote.js). vote.js fires "round-changed" with every GET /api/rounds/current answer it applies (the 60 s badge poll,
// the dialog's polls, votes), so the banner needs no timer of its own.
// While a round is open the banner shows "🗳️ Voting open: <title> · closes in <time>" plus one action:
//   Join (not a participant) · Continue voting · N left (participant with unrated trips) · See results ✓ (done).
// Desktop: a strip under the header; phone: at the top of the Map tab, below the top bar (style.css).
// Auto-open: the first round answer after a page load / log in opens the round once per round per browser session
// (sessionStorage) when the user isn't a participant yet or still has unrated trips.
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
// Every action opens the Vote dialog for now (the guided round points them at the Join screen / guide / Overview).
function bannerAction(kind) {
  openVote();
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

function autoOpened(roundId) {
  try { return sessionStorage.getItem(AUTO_OPEN_KEY + roundId) === "1"; } catch { return false; }
}
function markAutoOpened(roundId) {
  try { sessionStorage.setItem(AUTO_OPEN_KEY + roundId, "1"); } catch { /* storage blocked: opens once per load */ }
}
function maybeAutoOpen(round) {
  if (!banner.armed || !auth.user) return;
  banner.armed = false;  // only the first answer after a page load / log in
  const kind = bannerKind(round);
  if (kind !== "join" && kind !== "continue") return;
  if (autoOpened(round.id)) return;
  markAutoOpened(round.id);
  if (!document.querySelector("dialog[open]")) bannerAction(kind);
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
