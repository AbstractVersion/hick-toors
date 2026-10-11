"use strict";
// 🗳️ Group voting: the header badge and the #vote-dialog (open round, closed round with its 🏆 winner, the Start a
// round form → the 🎉 invite screen with the share link, history). The guided round (guide.js, loaded next) reuses
// its round state, polls, writes (castVote) and toast. Loaded after trips.js; uses $, api, notices (app.js), auth / requireLogin (auth.js) and refreshTrips,
// tripPath, plural, showTripOnMap, openTrip, showTripsError (trips.js).
// Every name, title and comment is user input: textContent only.
const vote = {
  round: null,          // last applied answer of GET /api/rounds/current (or of a write)
  mode: "current",      // "current" | "form" | "past" | "invite" (🎉 the link, right after Start a round)
  past: null,           // the read-only past round being looked at
  seq: 0, applied: 0,   // request numbers: an answer older than the newest applied one is dropped
  writing: 0, queue: Promise.resolve(),  // votes/opener actions run one after another; polls are ignored meanwhile
  pendingRender: false, // a row was kept as it was because its comment field has the focus
  // While the dialog is open rows never move by themselves (see renderTrips): the row under the pointer or finger
  // must stay the trip/stay it was, or the next click lands on another one.
  shown: null,          // {round, trips: [ids], stays: {tripId: [ids]}}: the order on screen
  pressed: false,       // a primary pointer is down inside the list: redraws wait for the release (deferred)
  deferred: false, releaseTimer: null,
  openStays: new Set(), openNames: new Set(),
  panel: null,          // opener tools: null | "extend" | "add"
  armed: {},            // two-click confirm timers: close / cancel
  pollTimer: null, focusTrip: null, toastTimer: null,
  pollError: false,     // the error on show came from a failed poll: the next applied poll clears it
};
const VOTE_POLL_MS = 10000, BADGE_POLL_MS = 60000;
const PRESS_GRACE_MS = 300;  // a release with no click (it landed elsewhere) ends the press after this
const MAX_ROUND_TRIPS = 50;  // the API's cap on trips per round
const RATINGS = [["love", "😍", "Love it"], ["ok", "🙂", "OK"], ["nogo", "🚫", "No-go"]];
const enc = encodeURIComponent;
const sameUser = (a, b) => Boolean(a && b) && a.toLowerCase() === b.toLowerCase();
const nextSeq = () => ++vote.seq;

function vEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
function vButton(text, className, onClick) {
  const b = vEl("button", className || "tool-btn", text);
  b.type = "button";
  if (onClick) b.addEventListener("click", onClick);
  return b;
}
// The Vote dialog and the guide (guide.js) share the round, the writes and the polls, so an error shows in both.
function showVoteError(message, fromPoll = false) {
  vote.pollError = Boolean(message) && fromPoll;
  for (const id of ["vote-error", "guide-error"]) {
    const box = $(id);
    if (!box) continue;
    box.textContent = message || "";
    box.hidden = !message;
  }
}
// The toast (Moved your Love…) floats at the bottom of the screen (style.css: fixed, no pointer events), so it is seen
// wherever the dialog is scrolled to and never covers a row or button someone is about to press. It shows in the
// dialog that is open: the Vote dialog or the guide.
function toast(message) {
  clearTimeout(vote.toastTimer);
  const id = $("guide")?.open ? "guide-toast" : "vote-toast";
  for (const other of ["vote-toast", "guide-toast"]) if ($(other)) $(other).hidden = other !== id;
  $(id).textContent = message;
  $(id).hidden = false;
  vote.toastTimer = setTimeout(() => { $(id).hidden = true; }, 6000);
}
// "YYYY-MM-DDTHH:MM" in local time, the value format of <input type=datetime-local>.
function localInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function localWhen(iso) {
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function closesIn(iso) {
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "closing now";
  const min = Math.floor(ms / 60000), d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
  if (d) return `closes in ${d} d ${h} h`;
  return h ? `closes in ${h} h ${m} min` : `closes in ${Math.max(1, m)} min`;
}

// ---- header badge: "🗳️ Vote · N left" for the logged-in user while a round is open ----
function setBadge(round) {
  const open = round && round.status === "open" && auth.user && round.me;
  const left = open ? Math.max(0, round.me.total - round.me.rated) : 0;
  $("vote-left").textContent = open ? ` · ${left} left` : "";
  $("vote-left").hidden = !open;
  $("vote-btn").classList.toggle("has-left", Boolean(open) && left > 0);
  // banner.js: every round answer (60 s badge poll, dialog polls, votes, opener actions) also feeds the banner.
  document.dispatchEvent(new CustomEvent("round-changed", { detail: round || null }));
}
async function refreshBadge() {
  try { setBadge(await api("/api/rounds/current")); } catch { /* keep the last badge */ }
}

// ---- loading / applying answers ----
function applyRound(round, seq) {
  if (seq < vote.applied) return false;
  vote.applied = seq;
  vote.round = round;
  setBadge(round);
  if ($("vote-dialog").open && vote.mode === "current") render();
  return true;
}
// GET current: dropped if a newer answer was applied, or a vote/opener action is in flight or finished after it
// was sent (its answer is newer).
async function refreshVote() {
  const seq = nextSeq();
  auth.refreshUsers();  // a friend who just signed up or changed emoji: "users-changed" redraws the names
  try {
    const round = await api("/api/rounds/current");
    if (vote.writing > 0) return;
    if (applyRound(round, seq) && vote.pollError) showVoteError("");
  } catch (err) {
    if (($("vote-dialog").open && vote.mode === "current") || $("guide")?.open) showVoteError(err.message, true);
  }
}
// Writes (votes, opener actions) run one at a time, in click order; each applies the round it returns.
function write(fn) {
  vote.writing += 1;
  const run = vote.queue.then(fn).finally(() => { vote.writing -= 1; });
  vote.queue = run.catch(() => {});
  return run.catch((err) => showVoteError(err.message));
}

// ---- opening / closing the dialog ----
// tab: "vote" | "overview" for this opening only (not remembered); default: a trip's row on the Vote tab, otherwise
// the tab used last.
async function openVote(tripId, tab) {
  vote.focusTrip = tripId || null;
  setVoteTab(tab || (tripId ? "vote" : storedVoteTab()), false);
  vote.mode = "current";
  vote.panel = null;
  showVoteError("");
  $("vote-toast").hidden = true;
  vote.shown = null;  // (re)opening shows the rows in rank order
  const dlg = $("vote-dialog");
  if (!dlg.open) dlg.showModal();
  clearInterval(vote.pollTimer);
  vote.pollTimer = setInterval(refreshVote, VOTE_POLL_MS);
  await refreshVote();  // its answer renders the dialog (the round, or the Start a round form)
}
// The 📊 Overview of the current round, or of the round roundId when that one is over (a closed round's share link,
// the guide's Done, the banner's See results).
async function openVoteOverview(roundId) {
  await openVote(null, "overview");
  if (roundId && vote.round?.id !== roundId) await openPast(roundId);
}
function onVoteClose() {
  clearInterval(vote.pollTimer);
  vote.pollTimer = null;
  clearTimeout(vote.releaseTimer);
  Object.assign(vote, { pressed: false, deferred: false, shown: null });
  for (const key of Object.keys(vote.armed)) disarm(key);
  hideOvPop();
}

// ---- rendering ----
function render() {
  if (vote.pressed) { vote.deferred = true; return; }  // the release must land on the element that was pressed
  showViews();
  if (vote.mode === "form" || vote.mode === "invite") return;
  const round = vote.mode === "past" ? vote.past : vote.round;
  if (!round) {
    vote.mode = "form";
    showViews();
    renderForm();
    return;
  }
  renderRound(round, vote.mode === "past");
}
function showViews() {
  $("vote-round-view").hidden = vote.mode === "form" || vote.mode === "invite";
  $("vote-start").hidden = vote.mode !== "form";
  $("vote-history-view").hidden = vote.mode !== "form";
  $("vote-invite").hidden = vote.mode !== "invite";
  $("vote-title").textContent = vote.mode === "form" ? "🗳️ Start a round" : "🗳️ Vote";
}
function isOpener(round) { return sameUser(round.opener, auth.user); }
function tripName(round, tripId) { return round.trips.find((t) => t.trip_id === tripId)?.name; }

function renderRound(round, readOnly) {
  const open = round.status === "open" && !readOnly;
  // Banner (closed / cancelled)
  const banner = $("vote-banner");
  banner.replaceChildren();
  banner.hidden = round.status === "open";
  if (round.status === "closed") {
    const w = round.winner;
    banner.append(vEl("strong", null, w ? `🏆 ${w.trip_name} wins${w.stay_name ? ` – stay: ${w.stay_name}` : ""}`
      : "No winner – nobody voted"));
    if (w) banner.append(" ", vButton("🗺️ Show on map", "tool-btn", () => showRoundTrip(w.trip_id)));
  } else if (round.status === "cancelled") {
    banner.append(vEl("strong", null, "This round was cancelled"));
  }
  // Status lines
  const status = $("vote-status");
  status.replaceChildren();
  status.append(vEl("h3", "vote-round-title", round.title));
  const when = round.status === "open"
    ? `${closesIn(round.closes_at)} (${localWhen(round.closes_at)})`
    : `${round.status === "closed" ? "Closed" : "Cancelled"} ${localWhen(round.closed_at || round.closes_at)}`;
  status.append(vEl("div", "meta", `${when} · opened by ${auth.label(round.opener)}`));
  if (round.status === "open") {
    status.append(vEl("div", null, round.waiting_for.length ? `Waiting for: ${round.waiting_for.map(auth.label).join(", ")}`
      : "Everyone has rated every trip ✓"));
  }
  if (round.me && open) {
    status.append(vEl("div", null, `You: ${round.me.rated} of ${plural(round.me.total, "trip")} rated`));
    const love = round.me.love_trip_id && tripName(round, round.me.love_trip_id);
    status.append(vEl("div", null, `😍 Your Love: ${love || "not given yet"}`));
  } else if (open && !auth.user) {
    const line = vEl("div", null, "Everyone can watch – ");
    line.append(vButton("log in to vote", "link-btn", () => requireLogin(() => {})));
    status.append(line);
  }
  renderShare(round, open);
  renderTools(round, open);
  renderTrips(round, open);
  // Footer
  const footer = $("vote-footer");
  footer.replaceChildren();
  if (readOnly) footer.append(vButton("← Back", "tool-btn", backToCurrent));
  else if (round.status !== "open") {
    // Past rounds is open to everyone (the form's Start button stays behind the login).
    footer.append(vButton("Start a new round", "primary", () => requireLogin(showForm)), " ",
      vButton("Past rounds", "tool-btn", showForm));
  }
  if (vote.tab === "overview") renderOverview(round);  // the hidden Overview is drawn when its tab is picked
  else setVoteTab(vote.tab, false);
  focusTripRow();
}
function focusTripRow() {
  if (!vote.focusTrip) return;
  if (vote.tab !== "vote") { vote.focusTrip = null; return; }
  const row = $("vote-trips").querySelector(`[data-trip="${CSS.escape(vote.focusTrip)}"]`);
  vote.focusTrip = null;
  if (row) {
    row.classList.add("focus");
    row.scrollIntoView({ block: "center" });
  }
}

// ---- 🔗 share link: <origin>/?round=<id> (guide.js handles opening it) ----
// The public site is plain http: no navigator.share / navigator.clipboard there (not a secure context), so the link
// is copied with execCommand and shared through 💬 WhatsApp / 🟣 Viber links instead of the share sheet.
const roundLink = (id) => `${location.origin}/?round=${enc(id)}`;
const shareText = (round) => `Vote on '${round.title}' – closes ${localWhen(round.closes_at)}`;
const canShare = () => typeof navigator.share === "function";
// The native share sheet (WhatsApp, Viber, …). True when it was shown (also when the user just closed it).
async function nativeShare(round) {
  try {
    await navigator.share({ title: round.title, text: shareText(round), url: roundLink(round.id) });
    return true;
  } catch (err) {
    return err?.name === "AbortError";
  }
}
// No share sheet: 💬 WhatsApp / 🟣 Viber links with the text + link (getRound: the round as it is when tapped).
function shareApps(getRound) {
  const box = vEl("div", "share-apps");
  const apps = [
    ["💬 WhatsApp", "share-whatsapp", "https://wa.me/?text=", "_blank"],
    ["🟣 Viber", "share-viber", "viber://forward?text=", "_self"],  // an app link: a new tab would stay blank
  ];
  for (const [label, cls, base, target] of apps) {
    const a = vEl("a", `tool-btn share-app ${cls}`, label);
    a.target = target;
    a.rel = "noopener";
    const update = () => {
      const round = getRound();
      if (round) a.href = base + encodeURIComponent(`${shareText(round)} ${roundLink(round.id)}`);
    };
    update();
    a.addEventListener("click", update);  // before the browser follows it: a renamed round, a newer closing time
    box.append(a);
  }
  return box;
}
// No navigator.clipboard (http): the field made editable for a moment, selected and copied with execCommand.
function execCopy(input) {
  input.readOnly = false;
  input.focus({ preventScroll: true });
  input.setSelectionRange(0, input.value.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  input.readOnly = true;
  return ok;
}
// Copies the link and shows it in a field; when nothing could copy it the field stays selected for a long-press.
async function copyLink(round, box) {
  const url = roundLink(round.id);
  box.replaceChildren();
  const msg = vEl("div", "share-msg");
  const input = vEl("input", "share-url");
  input.readOnly = true;
  input.value = url;
  input.setAttribute("aria-label", "Link to this round");
  input.addEventListener("focus", () => input.select());
  box.append(msg, input);
  box.hidden = false;
  let copied = false;
  if (typeof navigator.clipboard?.writeText === "function") {
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch { /* permissions: execCommand below */ }
  }
  if (!copied) copied = execCopy(input);
  msg.textContent = copied ? "✓ Link copied" : "Long-press to copy";
  if (!copied) {
    input.focus({ preventScroll: true });
    input.setSelectionRange(0, url.length);
  }
  return copied;
}
// 🔗 Share link in the Vote dialog (open round, everyone): phones with a share sheet use it, the rest copy; without
// a share sheet the WhatsApp / Viber links sit under the button.
function renderShare(round, open) {
  const box = $("vote-share");
  const key = open ? round.id : "";
  box.hidden = !key;
  if (box.dataset.key === key) return;  // a poll keeps the copied-link field as it is
  box.dataset.key = key;
  box.replaceChildren();
  if (!key) return;
  const out = vEl("div", "share-out");
  out.hidden = true;
  box.append(vButton("🔗 Share link", "tool-btn share-btn", async () => {
    const current = vote.round;
    if (!current) return;
    if (canShare() && window.mobile?.isPhone() && await nativeShare(current)) return;
    copyLink(current, out);
  }));
  if (!canShare()) box.append(shareApps(() => vote.round));
  box.append(out);
}

// ---- 🎉 Round started – invite your friends (right after Start a round) ----
function showInvite(round) {
  vote.mode = "invite";
  vote.inviteRound = round;
  showVoteError("");
  showViews();
  $("vote-invite-round").textContent = `${round.title} · ${closesIn(round.closes_at)} (${localWhen(round.closes_at)})`;
  $("vote-invite-url").value = roundLink(round.id);
  $("vote-invite-share").hidden = !canShare();
  const apps = $("vote-invite-apps");
  apps.replaceChildren();
  apps.hidden = canShare();
  if (!apps.hidden) apps.append(...shareApps(() => vote.inviteRound).children);
  $("vote-invite-copied").replaceChildren();
  $("vote-invite-copied").hidden = true;
  $("vote-dialog").scrollTop = 0;
  $("vote-invite-start").focus({ preventScroll: true });
}
$("vote-invite-url").addEventListener("focus", (event) => event.currentTarget.select());
$("vote-invite-share").addEventListener("click", () => { if (vote.inviteRound) nativeShare(vote.inviteRound); });
$("vote-invite-copy").addEventListener("click", () => {
  if (vote.inviteRound) copyLink(vote.inviteRound, $("vote-invite-copied"));
});
$("vote-invite-start").addEventListener("click", () => {
  const round = vote.round?.id === vote.inviteRound?.id ? vote.round : vote.inviteRound;
  $("vote-dialog").close();
  if (round) openGuide(round);
});

// Opener tools: built only when what they show changes, so a poll never resets a date being typed.
function renderTools(round, open) {
  const box = $("vote-tools");
  const key = open && isOpener(round) ? `${round.id}|${vote.panel}` : "";
  box.hidden = !key;
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren();
  if (!key) return;
  const row = vEl("div", "dlg-actions");
  const closeBtn = vButton(armedLabel("close"), "tool-btn", () => confirmTwice("close", () => roundAction("close")));
  closeBtn.dataset.arm = "close";
  const cancelBtn = vButton(armedLabel("cancel"), "tool-btn danger", () => confirmTwice("cancel", () => roundAction("cancel")));
  cancelBtn.dataset.arm = "cancel";
  row.append(closeBtn, vButton("Extend", "tool-btn", () => togglePanel("extend")),
    vButton("Add trips", "tool-btn", () => togglePanel("add")), cancelBtn);
  box.append(row);
  if (vote.panel === "extend") {
    const p = vEl("div", "vote-panel");
    const input = vEl("input");
    input.type = "datetime-local";
    input.id = "vote-extend-at";
    input.value = localInputValue(new Date(round.closes_at));
    input.min = localInputValue(new Date(round.closes_at));
    const label = vEl("label", null, "New deadline ");
    label.append(input);
    p.append(label, " ", vButton("Save", "primary", () => {
      const at = new Date(input.value);
      if (!input.value || Number.isNaN(at.getTime())) return showVoteError("Pick a date and time");
      patchRound({ closes_at: at.toISOString() });
    }));
    box.append(p);
  } else if (vote.panel === "add") {
    const p = vEl("div", "vote-panel");
    p.append(vEl("span", "list-note", "Loading trips…"));
    box.append(p);
    refreshTrips().then((list) => {
      if (box.dataset.key !== key) return;
      const inRound = new Set(round.trips.map((t) => t.trip_id));
      const rest = list.filter((t) => !inRound.has(t.id));
      p.replaceChildren();
      if (!rest.length) {
        p.append(vEl("span", "list-note", "Every saved trip is already in the round"));
        return;
      }
      const boxes = rest.map((t) => {
        const label = vEl("label", "vote-check");
        const cb = vEl("input");
        cb.type = "checkbox";
        cb.value = t.id;
        label.append(cb, ` ${t.name}`);
        p.append(label);
        return cb;
      });
      p.append(vButton("Add", "primary", () => {
        const ids = boxes.filter((cb) => cb.checked).map((cb) => cb.value);
        if (!ids.length) return showVoteError("Tick at least one trip");
        patchRound({ add_trip_ids: ids });
      }));
    }).catch((err) => showVoteError(err.message));
  }
}
function togglePanel(name) {
  vote.panel = vote.panel === name ? null : name;
  if (vote.round) renderTools(vote.round, true);
}
const ARM_LABELS = { close: ["Close now", "Confirm close"], cancel: ["Cancel round", "Confirm cancel"] };
function armedLabel(key) { return ARM_LABELS[key][vote.armed[key] ? 1 : 0]; }
function updateArmed(key) {
  const b = $("vote-tools").querySelector(`[data-arm="${key}"]`);
  if (b) b.textContent = armedLabel(key);
}
function disarm(key) {
  clearTimeout(vote.armed[key]);
  delete vote.armed[key];
  updateArmed(key);
}
// First click arms (4 s), second click runs – like the trip editor's Delete.
function confirmTwice(key, run) {
  if (!vote.armed[key]) {
    vote.armed[key] = setTimeout(() => disarm(key), 4000);
    updateArmed(key);
    return;
  }
  disarm(key);
  requireLogin(run);
}
function roundAction(action) {
  const round = vote.round;
  if (!round) return;
  showVoteError("");
  return write(async () => {
    const view = await api(`/api/rounds/${enc(round.id)}/${action}`, {}, "POST");
    vote.panel = null;
    if (action === "cancel") {
      // A cancelled round is never shown as current: ask for what is current now.
      vote.applied = nextSeq();
      vote.round = await api("/api/rounds/current");
      setBadge(vote.round);
      notices(["Round cancelled"]);
      render();
      return;
    }
    applyRound(view, nextSeq());
  });
}
function patchRound(body) {
  const round = vote.round;
  if (!round) return;
  showVoteError("");
  requireLogin(() => write(async () => {
    const view = await api(`/api/rounds/${enc(round.id)}`, body, "PATCH");
    vote.panel = null;
    applyRound(view, nextSeq());
  }));
}

// items sorted by their place in shown (ids); new ones go last, in rank order (sort is stable).
function keepOrder(items, shown, idOf) {
  if (!shown) return items;
  const at = new Map(shown.map((id, i) => [id, i]));
  const place = (x) => (at.has(idOf(x)) ? at.get(idOf(x)) : shown.length);
  return [...items].sort((x, y) => place(x) - place(y));
}

// Trip rows. Shown in rank order when the dialog opens; after that a vote's or a poll's redraw keeps every row (and
// every stay) where it is – ranks, scores and counts update in place – and "↻ Sort by rank" appears once the order on
// screen differs from the ranking. Otherwise the trip you just rated jumps away and the next click or tap lands on
// whichever trip moved under it. A row whose comment field has the focus is left in place untouched (caret kept) and
// redrawn once the field loses the focus.
function renderTrips(round, open) {
  const list = $("vote-trips");
  const active = document.activeElement;
  const keep = active?.matches?.("#vote-trips .vote-comment") ? active.closest(".vote-trip") : null;
  const keepId = keep && round.trips.some((t) => t.trip_id === keep.dataset.trip) ? keep.dataset.trip : null;
  // Keyboard users get the focus back on the same button (a mouse or tap focus is not restored: it would scroll).
  const focusKey = active?.closest?.("#vote-trips") && active.matches(":focus-visible") ? active.dataset.voteKey : null;
  const prev = vote.shown?.round === round.id ? vote.shown : null;
  const trips = keepOrder(round.trips, prev?.trips, (t) => t.trip_id);
  const shown = { round: round.id, trips: trips.map((t) => t.trip_id), stays: {} };
  const stayOrder = {};
  let moved = trips.some((t, i) => t !== round.trips[i]);
  for (const t of trips) {
    const stays = keepOrder(t.stays, prev?.stays[t.trip_id], (s) => s.stay_id);
    moved ||= stays.some((s, i) => s !== t.stays[i]);
    shown.stays[t.trip_id] = stays.map((s) => s.stay_id);
    stayOrder[t.trip_id] = stays;
  }
  vote.shown = shown;
  // Its place is kept while the round is open (shown or not), so it appearing never shifts the rows.
  const sort = sortButton();
  sort.hidden = !open;
  sort.style.visibility = moved ? "visible" : "hidden";
  const rows = trips.map((t) => (t.trip_id === keepId ? keep : tripRow(round, t, open, stayOrder[t.trip_id])));
  vote.pendingRender = Boolean(keepId);
  if (!keepId) {
    list.replaceChildren(...rows);
  } else {
    for (const child of [...list.children]) if (child !== keep) child.remove();
    let before = true;
    for (const row of rows) {
      if (row === keep) before = false;
      else if (before) list.insertBefore(row, keep);
      else list.appendChild(row);
    }
  }
  if (!round.trips.length) list.replaceChildren(vEl("li", "list-note", "No trips left in this round."));
  if (focusKey && !list.contains(document.activeElement)) {
    [...list.querySelectorAll("[data-vote-key]")].find((b) => b.dataset.voteKey === focusKey)
      ?.focus({ preventScroll: true });
  }
}
// "↻ Sort by rank" above the list (made here: the page markup stays as it is).
function sortButton() {
  let b = $("vote-sort");
  if (!b) {
    b = vButton("↻ Sort by rank", "tool-btn vote-sort", () => {
      vote.shown = null;
      render();
    });
    b.id = "vote-sort";
    b.hidden = true;
    b.style.margin = "4px 0 6px";
    $("vote-trips").before(b);
  }
  return b;
}
// A press inside the list ends after its click (or PRESS_GRACE_MS after a release that brought none): then the
// rating buttons' "pressing" marks go and a redraw that waited runs. A press on a rating button that slid off (no
// click) saves that row's typed comment, which its blur skipped.
function endPress(delay, clicked) {
  clearTimeout(vote.releaseTimer);
  vote.releaseTimer = setTimeout(() => {
    vote.pressed = false;
    for (const row of $("vote-trips").querySelectorAll(".vote-buttons[data-pressing]")) {
      delete row.dataset.pressing;
      if (!clicked) row.saveComment?.();
    }
    if (vote.deferred && $("vote-dialog").open) { vote.deferred = false; render(); }
    vote.deferred = false;
  }, delay);
}
function myVote(votes) { return votes.find((v) => sameUser(v.username, auth.user)); }

// Shared by trips and stays: counts with names, ⚠ No-go lines, the three buttons and the 💬 comment field.
function voteControls(round, item, target, open, key) {
  const frag = document.createDocumentFragment();
  const counts = vEl("div", "vote-counts");
  for (const [rating, emoji, label] of RATINGS) {
    const names = item.votes.filter((v) => v.rating === rating).map((v) => auth.label(v.username));
    const c = vButton(`${emoji}${item.counts[rating]}`, "count-btn", () => {
      vote.openNames.has(key) ? vote.openNames.delete(key) : vote.openNames.add(key);
      namesBox.hidden = !vote.openNames.has(key);
    });
    c.title = names.length ? `${label}: ${names.join(", ")}` : `${label}: nobody`;
    counts.append(c);
  }
  frag.append(counts);
  const namesBox = vEl("div", "vote-names");
  namesBox.hidden = !vote.openNames.has(key);
  for (const [rating, emoji, label] of RATINGS) {
    const votes = item.votes.filter((v) => v.rating === rating);
    if (!votes.length) continue;
    namesBox.append(vEl("div", null, `${emoji} ${label}: ${votes.map((v) => auth.label(v.username) + (v.comment ? ` (“${v.comment}”)` : "")).join(", ")}`));
  }
  if (!item.votes.length) namesBox.append(vEl("div", null, "No votes yet"));
  frag.append(namesBox);
  for (const v of item.votes.filter((x) => x.rating === "nogo")) {
    frag.append(vEl("div", "vote-nogo", `⚠ ${auth.label(v.username)}: No-go${v.comment ? ` – “${v.comment}”` : ""}`));
  }
  const mine = myVote(item.votes);
  const row = vEl("div", "vote-buttons");
  const comment = vEl("input", "vote-comment");
  comment.type = "text";
  comment.maxLength = 140;
  comment.placeholder = "💬 Why? (optional)";
  comment.value = mine?.comment || "";
  comment.disabled = !open || !item.my_rating;
  comment.title = item.my_rating ? "Saved with your rating" : "Rate first, then add a comment";
  comment.setAttribute("aria-label", `Comment: ${item.name}`);
  for (const [rating, emoji, label] of RATINGS) {
    // Cast on click only (mouse, touch, pen, keyboard alike). Between press and release no redraw replaces the
    // pressed button (render waits while vote.pressed), so the release's click lands on it.
    const b = vButton(`${emoji} ${label}`, "vote-btn-rate",
      () => requireLogin(() => castVote(target, rating, comment.disabled ? null : comment.value)));
    b.dataset.rating = rating;
    b.dataset.voteKey = `${key}:${rating}`;
    b.setAttribute("aria-pressed", String(item.my_rating === rating));
    b.setAttribute("aria-label", `${label}: ${item.name}`);
    b.disabled = !open;
    row.append(b);
  }
  // Saved on blur with the current rating – unless the blur is a click on a rating button of this row, which
  // sends the comment itself. Desktop Safari / macOS Firefox / iOS never focus a button (relatedTarget null), so
  // the press marks the row "pressing" (list pointerdown below) until its click has run.
  const save = () => {
    const text = comment.value.trim();
    if (!item.my_rating || text === (mine?.comment || "")) return;
    castVote(target, item.my_rating, text);
  };
  row.saveComment = () => { if (document.activeElement !== comment) save(); };
  comment.addEventListener("blur", (event) => {
    if (row.dataset.pressing || event.relatedTarget?.closest?.(".vote-buttons") === row) return;
    save();
  });
  comment.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); comment.blur(); }
  });
  row.append(comment);
  frag.append(row);
  return frag;
}
function tripRow(round, t, open, stayList = t.stays) {
  const li = vEl("li", "vote-trip");
  li.dataset.trip = t.trip_id;
  const head = vEl("div", "vote-head");
  head.append(vEl("strong", null, `#${t.rank} ${t.name}`), vEl("span", "vote-score", ` · ${t.score} pts`));
  if (round.winner?.trip_id === t.trip_id) head.append(vEl("span", "vote-score", " 🏆"));
  li.append(head, voteControls(round, t, { tripId: t.trip_id }, open, `t:${t.trip_id}`));
  const actions = vEl("div", "vote-actions");
  const stays = vEl("ol", "vote-stays");
  stays.hidden = !vote.openStays.has(t.trip_id);
  if (t.stays.length) {
    const label = () => `${vote.openStays.has(t.trip_id) ? "▾" : "▸"} Stays (${t.stays.length})`;
    const toggle = vButton(label(), "link-btn", () => {
      vote.openStays.has(t.trip_id) ? vote.openStays.delete(t.trip_id) : vote.openStays.add(t.trip_id);
      stays.hidden = !vote.openStays.has(t.trip_id);
      toggle.textContent = label();
    });
    actions.append(toggle);
    for (const s of stayList) {
      const sli = vEl("li", "vote-stay");
      sli.dataset.stay = s.stay_id;
      const shead = vEl("div", "vote-head");
      shead.append(vEl("span", null, `#${s.rank} ${s.name}`), vEl("span", "vote-score", ` · ${s.score} pts`));
      if (round.winner?.trip_id === t.trip_id && round.winner?.stay_id === s.stay_id) shead.append(vEl("span", "vote-score", " 🏆"));
      sli.append(shead, voteControls(round, s, { tripId: t.trip_id, stayId: s.stay_id }, open, `s:${t.trip_id}:${s.stay_id}`));
      stays.append(sli);
    }
  }
  actions.append(vButton("🗺️ Show on map", "link-btn", () => showRoundTrip(t.trip_id)),
    vButton("Open trip", "link-btn", () => openRoundTrip(t.trip_id)));
  li.append(actions, stays);
  return li;
}

// ---- votes ----
function votePath(roundId, target) {
  const base = `/api/rounds/${enc(roundId)}/trips/${enc(target.tripId)}`;
  return `${base}${target.stayId ? `/stays/${enc(target.stayId)}` : ""}/vote`;
}
function castVote(target, rating, comment) {
  const round = vote.round;
  if (!round || round.status !== "open") return;
  showVoteError("");
  return write(async () => {
    const text = comment == null ? null : String(comment).trim().slice(0, 140);
    const res = await api(votePath(round.id, target), { rating, comment: text || null }, "PUT");
    applyRound(res.round, nextSeq());
    if (res.moved_love_from) {
      const trip = res.round.trips.find((t) => t.trip_id === target.tripId);
      const from = target.stayId ? trip?.stays.find((s) => s.stay_id === res.moved_love_from)?.name
        : tripName(res.round, res.moved_love_from);
      const to = target.stayId ? trip?.stays.find((s) => s.stay_id === target.stayId)?.name : trip?.name;
      if (from && to) toast(`Moved your Love from ${from} to ${to}`);
    }
  });
}

// ---- trip actions ----
async function showRoundTrip(tripId) {
  try {
    const trip = await api(tripPath(tripId));
    $("vote-dialog").close();
    showTripOnMap(trip, trip.name);
  } catch (err) {
    showVoteError(err.message);
  }
}
function openRoundTrip(tripId) {
  $("vote-dialog").close();
  openTripsDialog().then(() => openTrip(tripId));  // the list is behind it, so ← All trips works as usual
}

// ---- Start a round + history ----
function showForm() {
  vote.mode = "form";
  showVoteError("");
  showViews();
  renderForm();
}
function backToCurrent() {
  vote.mode = "current";
  vote.past = null;
  showVoteError("");
  render();
}
async function renderForm() {
  const token = vote.formToken = (vote.formToken || 0) + 1;  // a newer call owns the form
  $("vote-start-back").hidden = !vote.round;
  const at = new Date(Date.now() + 3 * 86400000);
  at.setSeconds(0, 0);
  $("vote-start-at").value = localInputValue(at);
  $("vote-start-at").min = localInputValue(new Date(Date.now() + 3600000));
  const box = $("vote-start-trips");
  box.replaceChildren(vEl("span", "list-note", "Loading trips…"));
  const history = $("vote-history");
  history.replaceChildren();
  try {
    const list = await refreshTrips();
    if (token !== vote.formToken) return;
    box.replaceChildren();
    list.forEach((t, i) => {
      const label = vEl("label", "vote-check");
      const cb = vEl("input");
      cb.type = "checkbox";
      cb.value = t.id;
      cb.checked = i < MAX_ROUND_TRIPS;
      label.append(cb, ` ${t.name}`);
      box.append(label);
    });
    if (list.length > MAX_ROUND_TRIPS) {
      box.append(vEl("span", "list-note",
        `A round holds at most ${MAX_ROUND_TRIPS} trips – the first ${MAX_ROUND_TRIPS} are ticked.`));
    }
    if (!list.length) box.append(vEl("span", "list-note", "No saved trips yet – save some in 🧳 My trips first."));
    $("vote-start-submit").disabled = !list.length;
  } catch (err) {
    box.replaceChildren();
    showVoteError(err.message);
  }
  try {
    const rounds = await api("/api/rounds");
    if (token !== vote.formToken) return;
    $("vote-history-empty").hidden = rounds.length > 0;
    for (const r of rounds) {
      const li = vEl("li");
      const status = r.status === "open" ? "open" : r.status === "cancelled" ? "cancelled"
        : r.winner_name ? `🏆 ${r.winner_name}` : "no winner";
      const b = vButton(`${r.title} · ${status} · ${localWhen(r.closed_at || r.closes_at)}`, "link-btn",
        () => openPast(r.id));
      li.append(b);
      history.append(li);
    }
  } catch { /* history is optional */ }
}
async function openPast(roundId) {
  showVoteError("");
  try {
    const round = await api(`/api/rounds/${enc(roundId)}`);
    if (round.status === "open") {
      backToCurrent();
      return;
    }
    vote.past = round;
    vote.mode = "past";
    render();
  } catch (err) {
    showVoteError(err.message);
  }
}
function submitStart(event) {
  event.preventDefault();
  requireLogin(startRound);
}
function startRound() {
  const title = $("vote-start-title").value.trim();
  const value = $("vote-start-at").value;
  const at = new Date(value);
  const ids = [...$("vote-start-trips").querySelectorAll("input:checked")].map((cb) => cb.value);
  if (!title) return showVoteError("Give the round a title");
  if (!value || Number.isNaN(at.getTime())) return showVoteError("Pick a closing date and time");
  if (!ids.length) return showVoteError("Tick at least one trip");
  showVoteError("");
  $("vote-start-submit").disabled = true;
  return write(async () => {
    try {
      const round = await api("/api/rounds", { title, closes_at: at.toISOString(), trip_ids: ids });
      vote.past = null;
      vote.panel = null;
      vote.mode = "invite";  // the opener gets the link first: 🎉 Round started – invite your friends
      applyRound(round, nextSeq());
      showInvite(round);
    } catch (err) {
      if (/already open/.test(err.message)) {  // someone was quicker: show their round
        vote.mode = "current";
        refreshVote();
      }
      throw err;
    } finally {
      $("vote-start-submit").disabled = false;
    }
  });
}

// ---- 📊 Overview: who voted what – participants as columns, trips (and their stays) as rows ----
// Re-rendered from the same RoundView as the Vote tab (polls, writes, past rounds). The tab choice is kept per
// browser; storage can be missing or throw (private mode), then the Vote tab is the default.
const VOTE_TAB_KEY = "hick-toors:vote-tab";
const RATING_INFO = Object.fromEntries(RATINGS.map(([rating, emoji, label]) => [rating, { emoji, label }]));
function storedVoteTab() {
  try { return localStorage.getItem(VOTE_TAB_KEY) === "overview" ? "overview" : "vote"; } catch { return "vote"; }
}
vote.tab = storedVoteTab();
vote.ovOpenStays = new Set();  // trips whose further stays are shown (kept across refreshes)
vote.ovPopKey = null;          // the cell whose comment popover is open
vote.ovScrollLeft = 0;         // the grid's sideways scroll (kept while hidden)

function setVoteTab(tab, remember = true) {
  vote.tab = tab === "overview" ? "overview" : "vote";
  if (remember) {
    try { localStorage.setItem(VOTE_TAB_KEY, vote.tab); } catch { /* not remembered: fine */ }
  }
  for (const b of $("vote-tabs").querySelectorAll("[data-vtab]")) {
    b.setAttribute("aria-selected", String(b.dataset.vtab === vote.tab));
  }
  $("vote-pane").hidden = vote.tab !== "vote";
  $("vote-overview").hidden = vote.tab !== "overview";
}
function ratedEverything(round, name) {
  return round.trips.every((t) => t.votes.some((v) => sameUser(v.username, name)));
}
// The round's participants (join order); a round from an older server: opener + whoever voted.
function ovParticipants(round) {
  if (round.participants?.length) return round.participants;
  const names = [round.opener];
  for (const t of round.trips) {
    for (const v of [...t.votes, ...t.stays.flatMap((s) => s.votes)]) {
      if (!names.some((n) => sameUser(n, v.username))) names.push(v.username);
    }
  }
  return names.filter(Boolean);
}
function signed(score) { return score > 0 ? `+${score}` : score < 0 ? `−${-score}` : "0"; }

function renderOverview(round) {
  setVoteTab(vote.tab, false);
  const people = ovParticipants(round);
  const done = people.filter((p) => ratedEverything(round, p));
  const columns = [...done, ...people.filter((p) => !done.includes(p))];
  // Header: title · closes in / closed + 🏆 · leader · n/m people done
  const head = $("vote-ov-head");
  head.replaceChildren(vEl("h3", "vote-round-title", round.title));
  const when = round.status === "open" ? closesIn(round.closes_at)
    : `${round.status === "closed" ? "Closed" : "Cancelled"} ${localWhen(round.closed_at || round.closes_at)}`;
  head.append(vEl("div", "meta", when));
  if (round.status === "closed") {
    const w = round.winner;
    head.append(vEl("div", "ov-winner", w ? `🏆 ${w.trip_name} wins${w.stay_name ? ` – stay: ${w.stay_name}` : ""}`
      : "No winner – nobody voted"));
  }
  const top = round.trips[0];
  const anyVote = round.trips.some((t) => t.votes.length);
  head.append(vEl("div", "ov-leader", top && anyVote ? `Leader: ${top.name} (${signed(top.score)})` : "Leader: none yet"));
  head.append(vEl("div", "ov-done", `${done.length}/${people.length} people done`));

  const box = $("vote-ov-scroll");
  if (!round.trips.length) {
    box.replaceChildren(vEl("p", "list-note", "No trips left in this round."));
    return;
  }
  const table = vEl("table", "ov-grid");
  const thead = vEl("thead");
  const hr = vEl("tr");
  hr.append(vEl("th", "ov-name ov-corner", "Trip"));
  for (const p of columns) {
    const th = vEl("th", "ov-person");
    th.scope = "col";
    th.title = done.includes(p) ? `${p} – done` : p;
    th.append(vEl("span", "ov-emoji", auth.emojiOf(p) || NO_EMOJI), vEl("span", "ov-pname", p));
    if (sameUser(p, auth.user)) th.classList.add("me");
    if (!done.includes(p)) th.classList.add("ov-pending");
    hr.append(th);
  }
  hr.append(vEl("th", "ov-score", "Score"));
  thead.append(hr);
  const tbody = vEl("tbody");
  for (const t of round.trips) {
    const won = round.status === "closed" && round.winner?.trip_id === t.trip_id;
    const name = vEl("th", "ov-name");
    name.scope = "row";
    const link = vButton(t.name, "link-btn ov-trip", () => goToTrip(t.trip_id));
    link.title = "Rate it in the 🗳️ Vote tab";
    name.append(vEl("span", "ov-rank", `#${t.rank} `), link);
    if (won) name.append(" 🏆");
    const row = ovRow(t, name, columns, `t:${t.trip_id}`, "ov-trip-row");
    row.dataset.trip = t.trip_id;
    tbody.append(row);
    const stays = [...t.stays].sort((a, b) => a.rank - b.rank);
    const open = vote.ovOpenStays.has(t.trip_id);
    stays.forEach((s, i) => {
      if (i > 0 && !open) return;
      const sname = vEl("th", "ov-name ov-stay");
      sname.scope = "row";
      sname.append(vEl("span", null, `🏡 #${s.rank} `), vEl("span", null, s.name));
      if (won && round.winner?.stay_id === s.stay_id) sname.append(" 🏆");
      const srow = ovRow(s, sname, columns, `s:${t.trip_id}:${s.stay_id}`, "ov-stay-row");
      srow.dataset.trip = t.trip_id;
      srow.dataset.stay = s.stay_id;
      tbody.append(srow);
    });
    if (stays.length > 1) {
      const more = stays.length - 1;
      const tr = vEl("tr", "ov-more-row");
      const th = vEl("th", "ov-name ov-stay");
      th.append(vButton(open ? `▾ hide ${plural(more, "stay")}` : `▸ ${more} more ${more === 1 ? "stay" : "stays"}`,
        "link-btn ov-more", () => {
          vote.ovOpenStays.has(t.trip_id) ? vote.ovOpenStays.delete(t.trip_id) : vote.ovOpenStays.add(t.trip_id);
          renderOverview((vote.mode === "past" ? vote.past : vote.round) || round);  // the newest answer
        }));
      const filler = vEl("td");
      filler.colSpan = columns.length + 1;
      tr.append(th, filler);
      tbody.append(tr);
    }
  }
  table.append(thead, tbody);
  box.replaceChildren(table);
  box.scrollLeft = vote.ovScrollLeft || 0;  // a poll or a tab switch must not jump a phone's sideways scroll back
}
// One row: the name cell, a cell per participant (😍 / 🙂 / 🚫 or · ; a comment adds a dot + popover), the score.
function ovRow(item, nameCell, columns, key, className) {
  const tr = vEl("tr", className);
  tr.append(nameCell);
  let nogo = false;
  for (const p of columns) {
    const v = item.votes.find((x) => sameUser(x.username, p));
    const td = vEl("td", "ov-cell");
    td.dataset.user = p;
    if (sameUser(p, auth.user)) td.classList.add("me");
    if (!v) {
      td.append(vEl("span", "ov-none", "·"));
      td.title = `${p}: not rated`;
    } else {
      const info = RATING_INFO[v.rating];
      td.dataset.rating = v.rating;
      if (v.rating === "nogo") { nogo = true; td.classList.add("nogo"); }
      if (v.comment) {
        const cellKey = `${key}:${p}`;
        const b = vButton(info.emoji, "ov-cell-btn", (event) => toggleOvPop(event.currentTarget, cellKey, p, info.label, v.comment));
        b.append(vEl("span", "ov-dot"));
        b.setAttribute("aria-label", `${p}: ${info.label} – comment`);
        td.append(b);
      } else {
        td.append(vEl("span", null, info.emoji));
        td.title = `${p}: ${info.label}`;
      }
    }
    tr.append(td);
  }
  const score = vEl("td", "ov-score", String(item.score));
  if (nogo) {
    const warn = vEl("span", "ov-warn", " ⚠");
    warn.title = "Someone said No-go";
    score.append(warn);
  }
  tr.append(score);
  return tr;
}
function toggleOvPop(anchor, key, name, label, comment) {
  if (vote.ovPopKey === key) return hideOvPop();
  const pop = $("vote-ov-pop");
  pop.textContent = `${auth.emojiOf(name) || NO_EMOJI} ${name}: ${label} – “${comment}”`;
  pop.hidden = false;
  vote.ovPopKey = key;
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
  pop.style.top = `${r.bottom + 4 + h > innerHeight - 8 ? Math.max(8, r.top - h - 4) : r.bottom + 4}px`;
}
function hideOvPop() {
  vote.ovPopKey = null;
  $("vote-ov-pop").hidden = true;
}
// Trip name → the Vote tab at that trip.
function goToTrip(tripId) {
  hideOvPop();
  setVoteTab("vote", false);  // a jump to one trip, not a choice of tab
  vote.focusTrip = tripId;
  focusTripRow();
}
$("vote-tabs").addEventListener("click", (event) => {
  const b = event.target.closest("[data-vtab]");
  if (!b) return;
  hideOvPop();
  setVoteTab(b.dataset.vtab);
  const round = vote.mode === "past" ? vote.past : vote.round;
  if (vote.tab === "overview" && round && vote.mode !== "form" && vote.mode !== "invite") renderOverview(round);
});
// Its sideways scroll survives redraws and the hidden tab (a display: none box reads scrollLeft 0).
$("vote-ov-scroll").addEventListener("scroll", (event) => { vote.ovScrollLeft = event.currentTarget.scrollLeft; });
// Escape closes the popover (not the dialog); so do a press outside it (other than on a comment cell) and a scroll.
$("vote-dialog").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && vote.ovPopKey) { event.preventDefault(); event.stopPropagation(); hideOvPop(); }
});
$("vote-dialog").addEventListener("cancel", (event) => {
  if (vote.ovPopKey) { event.preventDefault(); hideOvPop(); }
});
document.addEventListener("pointerdown", (event) => {
  if (vote.ovPopKey && !event.target.closest?.("#vote-ov-pop, .ov-cell-btn")) hideOvPop();
}, true);
$("vote-dialog").addEventListener("scroll", () => { if (vote.ovPopKey) hideOvPop(); }, true);

$("vote-btn").addEventListener("click", () => openVote());
$("vote-close").addEventListener("click", () => $("vote-dialog").close());
$("vote-dialog").addEventListener("close", onVoteClose);
$("vote-dialog").addEventListener("close", () => window.mobile?.dialogClosed());  // phone: back to the previous tab
$("vote-start").addEventListener("submit", submitStart);
$("vote-start-back").addEventListener("click", backToCurrent);
// A press in the list (see endPress). Only the primary button: a right-click brings no click to end it.
$("vote-trips").addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  vote.pressed = true;
  clearTimeout(vote.releaseTimer);
  // A press on a rating button: that row's comment blur must not save the old rating (the click's PUT carries it).
  if (event.target.closest?.(".vote-btn-rate")) event.target.closest(".vote-buttons").dataset.pressing = "1";
});
document.addEventListener("pointerup", () => { if (vote.pressed) endPress(PRESS_GRACE_MS); }, true);
document.addEventListener("pointercancel", () => { if (vote.pressed) endPress(PRESS_GRACE_MS); }, true);
// Bubbles after the button's own click. A press and release on different elements still brings a click on their
// common ancestor: only a click on the pressed row's rating button counts as rated.
document.addEventListener("click", (event) => {
  if (vote.pressed) endPress(0, Boolean(event.target.closest?.(".vote-buttons[data-pressing] .vote-btn-rate")));
});
// A row kept because its comment had the focus is redrawn after the focus leaves it – unless the focus went to a
// button (its click answers with a fresh round and redraws then; redrawing now would swallow the click).
$("vote-trips").addEventListener("focusout", (event) => {
  if (event.relatedTarget?.closest?.("#vote-dialog button")) return;
  if ($("vote-trips").querySelector(".vote-buttons[data-pressing]")) return;  // a rating press: its answer redraws
  setTimeout(() => {
    if (vote.pendingRender && $("vote-dialog").open && vote.mode !== "form") render();
  }, 0);
});
document.addEventListener("auth-changed", () => {
  if ($("vote-dialog").open && vote.mode === "current") refreshVote();
  else refreshBadge();
});
document.addEventListener("users-changed", () => {
  if ($("vote-dialog").open && vote.mode !== "form" && vote.mode !== "invite") render();
});
setInterval(refreshBadge, BADGE_POLL_MS);
refreshBadge();
