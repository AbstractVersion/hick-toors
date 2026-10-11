"use strict";
// 🗳️ Group voting: the header badge and the #vote-dialog (open round, closed round with its 🏆 winner, the Start a
// round form, history). Loaded last; uses $, api, notices (app.js), auth / requireLogin (auth.js) and refreshTrips,
// tripPath, plural, showTripOnMap, openTrip, showTripsError (trips.js).
// Every name, title and comment is user input: textContent only.
const vote = {
  round: null,          // last applied answer of GET /api/rounds/current (or of a write)
  mode: "current",      // "current" | "form" | "past"
  past: null,           // the read-only past round being looked at
  seq: 0, applied: 0,   // request numbers: an answer older than the newest applied one is dropped
  writing: 0, queue: Promise.resolve(),  // votes/opener actions run one after another; polls are ignored meanwhile
  pendingRender: false, // a row was kept as it was because its comment field has the focus
  openStays: new Set(), openNames: new Set(),
  panel: null,          // opener tools: null | "extend" | "add"
  armed: {},            // two-click confirm timers: close / cancel
  pollTimer: null, focusTrip: null, toastTimer: null,
  pollError: false,     // the error on show came from a failed poll: the next applied poll clears it
};
const VOTE_POLL_MS = 10000, BADGE_POLL_MS = 60000;
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
function showVoteError(message, fromPoll = false) {
  vote.pollError = Boolean(message) && fromPoll;
  $("vote-error").textContent = message || "";
  $("vote-error").hidden = !message;
}
function toast(message) {
  clearTimeout(vote.toastTimer);
  $("vote-toast").textContent = message;
  $("vote-toast").hidden = false;
  vote.toastTimer = setTimeout(() => { $("vote-toast").hidden = true; }, 6000);
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
  try {
    const round = await api("/api/rounds/current");
    if (vote.writing > 0) return;
    if (applyRound(round, seq) && vote.pollError) showVoteError("");
  } catch (err) {
    if ($("vote-dialog").open && vote.mode === "current") showVoteError(err.message, true);
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
async function openVote(tripId) {
  vote.focusTrip = tripId || null;
  vote.mode = "current";
  vote.panel = null;
  showVoteError("");
  $("vote-toast").hidden = true;
  const dlg = $("vote-dialog");
  if (!dlg.open) dlg.showModal();
  clearInterval(vote.pollTimer);
  vote.pollTimer = setInterval(refreshVote, VOTE_POLL_MS);
  await refreshVote();  // its answer renders the dialog (the round, or the Start a round form)
}
function onVoteClose() {
  clearInterval(vote.pollTimer);
  vote.pollTimer = null;
  for (const key of Object.keys(vote.armed)) disarm(key);
}

// ---- rendering ----
function render() {
  showViews();
  if (vote.mode === "form") return;
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
  $("vote-round-view").hidden = vote.mode === "form";
  $("vote-start").hidden = vote.mode !== "form";
  $("vote-history-view").hidden = vote.mode !== "form";
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
  status.append(vEl("div", "meta", `${when} · opened by ${round.opener}`));
  if (round.status === "open") {
    status.append(vEl("div", null, round.waiting_for.length ? `Waiting for: ${round.waiting_for.join(", ")}`
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
  if (vote.focusTrip) {
    const row = $("vote-trips").querySelector(`[data-trip="${CSS.escape(vote.focusTrip)}"]`);
    vote.focusTrip = null;
    if (row) {
      row.classList.add("focus");
      row.scrollIntoView({ block: "center" });
    }
  }
}

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

// Trip rows in rank order. A row whose comment field has the focus is left in place untouched (caret kept) and
// redrawn once the field loses the focus.
function renderTrips(round, open) {
  const list = $("vote-trips");
  const active = document.activeElement;
  const keep = active?.matches?.("#vote-trips .vote-comment") ? active.closest(".vote-trip") : null;
  const keepId = keep && round.trips.some((t) => t.trip_id === keep.dataset.trip) ? keep.dataset.trip : null;
  const rows = round.trips.map((t) => (t.trip_id === keepId ? keep : tripRow(round, t, open)));
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
}
function myVote(votes) { return votes.find((v) => sameUser(v.username, auth.user)); }

// Shared by trips and stays: counts with names, ⚠ No-go lines, the three buttons and the 💬 comment field.
function voteControls(round, item, target, open, key) {
  const frag = document.createDocumentFragment();
  const counts = vEl("div", "vote-counts");
  for (const [rating, emoji, label] of RATINGS) {
    const names = item.votes.filter((v) => v.rating === rating).map((v) => v.username);
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
    namesBox.append(vEl("div", null, `${emoji} ${label}: ${votes.map((v) => v.username + (v.comment ? ` (“${v.comment}”)` : "")).join(", ")}`));
  }
  if (!item.votes.length) namesBox.append(vEl("div", null, "No votes yet"));
  frag.append(namesBox);
  for (const v of item.votes.filter((x) => x.rating === "nogo")) {
    frag.append(vEl("div", "vote-nogo", `⚠ ${v.username}: No-go${v.comment ? ` – “${v.comment}”` : ""}`));
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
    const rate = () => requireLogin(() => castVote(target, rating, comment.disabled ? null : comment.value));
    // Mouse: act on the press. Desktop Safari / macOS Firefox never focus a button, so the press blurs the comment
    // (relatedTarget null) and the release's click can land on a row a redraw has already replaced. The rating PUT
    // carries the comment text, so the blur that follows the press must not save the old rating. Touch, pen and
    // keyboard keep the plain click (touch fires its events together on release; keys don't blur).
    const b = vButton(`${emoji} ${label}`, "vote-btn-rate", () => {
      if (b.dataset.pressed) { delete b.dataset.pressed; return; }  // already handled on the press
      rate();
    });
    b.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "mouse" || event.button !== 0 || !auth.user) return;  // logged out: click → dialog
      b.dataset.pressed = "1";
      row.dataset.pressing = "1";
      // Cleared after the release's click (or at once if the release lands elsewhere and no click comes).
      const clear = () => setTimeout(() => { delete b.dataset.pressed; delete row.dataset.pressing; }, 0);
      document.addEventListener("pointerup", clear, { once: true });
      rate();
    });
    b.dataset.rating = rating;
    b.setAttribute("aria-pressed", String(item.my_rating === rating));
    b.setAttribute("aria-label", `${label}: ${item.name}`);
    b.disabled = !open;
    row.append(b);
  }
  // Saved on blur with the current rating – unless the blur is a click on a rating button of this row, which
  // sends the comment itself.
  comment.addEventListener("blur", (event) => {
    if (row.dataset.pressing || event.relatedTarget?.closest?.(".vote-buttons") === row) return;
    const text = comment.value.trim();
    if (!item.my_rating || text === (mine?.comment || "")) return;
    castVote(target, item.my_rating, text);
  });
  comment.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); comment.blur(); }
  });
  row.append(comment);
  frag.append(row);
  return frag;
}
function tripRow(round, t, open) {
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
    for (const s of t.stays) {
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
      vote.mode = "current";
      vote.past = null;
      vote.panel = null;
      applyRound(round, nextSeq());
      render();
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

$("vote-btn").addEventListener("click", () => openVote());
$("vote-close").addEventListener("click", () => $("vote-dialog").close());
$("vote-dialog").addEventListener("close", onVoteClose);
$("vote-start").addEventListener("submit", submitStart);
$("vote-start-back").addEventListener("click", backToCurrent);
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
setInterval(refreshBadge, BADGE_POLL_MS);
refreshBadge();
