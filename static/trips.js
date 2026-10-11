"use strict";
// Saved trips: ＋ Trip menu on hikes/stays/nature sights, 💾 Save on Trip picks, and the 🧳 My trips dialog.
// Loaded after app.js, nature.js and auth.js and uses their globals ($, esc, state, api, notices, dates, guests,
// clearAll, showTripSights, SIGHT_EMOJI, requireLogin, ...). Every action that writes to the server goes through
// requireLogin; fields that only change what is on screen (name, dates, notes, prices) are not gated.
// The editor's 🗳️ round line calls openVote (vote.js, loaded after this file); vote.js reuses showTripOnMap/openTrip.
const trips = { list: [], current: null, deleteTimer: null, busy: 0, saving: Promise.resolve() };
const LAST_TRIP_KEY = "hick-toors:last-trip";

function lastTripId() { try { return localStorage.getItem(LAST_TRIP_KEY); } catch { return null; } }
function rememberTrip(id) { try { localStorage.setItem(LAST_TRIP_KEY, id); } catch { /* storage blocked */ } }
const tripPath = (id, rest = "") => `/api/saved-trips/${encodeURIComponent(id)}${rest}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

async function refreshTrips() {
  trips.list = await api("/api/saved-trips");
  $("trips-count").textContent = `(${trips.list.length})`;
  return trips.list;
}
function formTripFields() {
  const { start, end } = dates();
  return { start: start || null, end: end || null, guests: guests(), dog: $("dog").checked };
}
const ITEM_LIST = { hike: "hikes", stay: "stays", sight: "sights" };
const ITEM_COUNT = { hike: "hike_count", stay: "stay_count", sight: "sight_count" };
function itemOf(kind, id) { return ({ hike: state.hikes, stay: state.stays, sight: state.sights })[kind]?.get(id); }

// ---- ＋ Trip (list items and map popups) ----
function tripButton(kind, id) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "add-trip link-btn";
  b.dataset.kind = kind;
  b.dataset.id = id;
  b.title = "Add to a saved trip";
  b.textContent = "＋ Trip";
  return b;
}
function tripButtonHtml(kind, id) {
  return `<button type="button" class="add-trip link-btn" data-kind="${esc(kind)}" data-id="${esc(id)}" title="Add to a saved trip">＋ Trip</button>`;
}
function closeTripMenu() { $("trip-menu").hidden = true; }
async function openTripMenu(button) {
  const { kind, id } = button.dataset;
  const item = itemOf(kind, id);
  if (!item) return;
  await refreshTrips();
  const last = lastTripId();
  const ordered = [...trips.list].sort((a, b) => (b.id === last) - (a.id === last));
  const menu = $("trip-menu");
  menu.replaceChildren();
  const entry = (label, action) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.textContent = label;
    b.addEventListener("click", async () => {
      closeTripMenu();
      try { await action(); } catch (err) { notices([err.message]); }
    });
    menu.appendChild(b);
  };
  entry("➕ New trip", () => newTripWith(kind, item));
  for (const t of ordered) entry(t.name, () => addToTrip(t, kind, item));
  const r = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 260))}px`;
  menu.style.top = `${r.bottom + 4}px`;
  menu.hidden = false;
  const height = menu.offsetHeight;  // measurable only once shown
  if (r.bottom + 4 + height > window.innerHeight) menu.style.top = `${Math.max(8, r.top - 4 - height)}px`;
  menu.querySelector("button")?.focus();
}
async function newTripWith(kind, item) {
  const trip = await api("/api/saved-trips", { name: String(item.name || "New trip").slice(0, 120), ...formTripFields(),
    hikes: kind === "hike" ? [item] : [], stays: kind === "stay" ? [item] : [], sights: kind === "sight" ? [item] : [] });
  rememberTrip(trip.id);
  await refreshTrips();
  notices([`Added to “${trip.name}” – rename it in 🧳 My trips`]);
}
async function addToTrip(summary, kind, item) {
  const before = summary[ITEM_COUNT[kind]] ?? 0;
  const trip = await api(tripPath(summary.id, "/items"), { [kind]: item });
  rememberTrip(trip.id);
  await refreshTrips();
  const after = (trip[ITEM_LIST[kind]] || []).length;
  notices([after > before ? `Added to “${trip.name}”` : `Already in “${trip.name}”`]);
}
// Capture phase: a ＋ Trip click must not also select the hike / focus the stay underneath it.
document.addEventListener("click", (event) => {
  const button = event.target.closest?.(".add-trip");
  if (button) {
    event.preventDefault();
    event.stopPropagation();
    requireLogin(() => openTripMenu(button).catch((err) => notices([err.message])));
    return;
  }
  if (!event.target.closest?.("#trip-menu")) closeTripMenu();
}, true);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeTripMenu(); });

// ---- 💾 Save on a Trip pick ----
function saveComboButton(combo) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "save-combo link-btn";
  b.textContent = "💾 Save";
  b.addEventListener("click", (event) => {
    event.stopPropagation();
    requireLogin(() => saveCombo(b, combo));
  });
  return b;
}
async function saveCombo(b, combo) {
  if (b.disabled) return;
  b.disabled = true;
  try {
    const trip = await api("/api/saved-trips", { name: `${combo.hike.name} + ${combo.stay.name}`.slice(0, 120),
      ...formTripFields(), source: "suggested", score: combo.score ?? null, hikes: [combo.hike], stays: [combo.stay] });
    rememberTrip(trip.id);
    await refreshTrips();
    b.textContent = "✓ Saved";
    notices([`Saved “${trip.name}” – see 🧳 My trips`]);
  } catch (err) {
    b.disabled = false;
    notices([err.message]);
  }
}

// ---- 🧳 My trips dialog ----
function showTripsError(message) {
  $("trips-error").textContent = message || "";
  $("trips-error").hidden = !message;
}
function showTripList() {
  trips.current = null;
  resetDelete();
  $("trip-editor").hidden = true;
  $("trips-list-view").hidden = false;
}
async function reloadTripList() {
  try { renderTripList(await refreshTrips()); } catch (err) { showTripsError(err.message); }
}
async function openTripsDialog() {
  showTripsError("");
  showTripList();
  if (!$("trips-dialog").open) $("trips-dialog").showModal();
  await reloadTripList();
}
function tripDates(t) {
  if (t.start && t.end) return `${t.start} → ${t.end}`;
  if (t.start) return `From ${t.start}`;
  return t.end ? `Until ${t.end}` : "Dates not set";
}
function renderTripList(list) {
  const ul = $("trips-list");
  ul.replaceChildren();
  $("trips-empty").hidden = list.length > 0;
  for (const t of list) {
    const li = document.createElement("li");
    li.tabIndex = 0;
    const strong = document.createElement("strong");
    strong.textContent = t.name;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${tripDates(t)} · ${plural(t.hike_count, "hike")} · ${plural(t.stay_count, "stay")}${t.sight_count > 0 ? ` · ${plural(t.sight_count, "sight")}` : ""}${t.source === "suggested" ? " · ⭐" : ""}`;
    if (t.created_by) meta.textContent += ` · by ${t.created_by}`;
    li.append(strong, meta);
    li.addEventListener("click", () => openTrip(t.id));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter") openTrip(t.id); });
    ul.appendChild(li);
  }
}
async function openTrip(id) {
  showTripsError("");
  try { trips.current = await api(tripPath(id)); } catch (err) { showTripsError(err.message); return; }
  const t = trips.current;
  $("trip-name").value = t.name;
  $("trip-start").value = t.start || "";
  $("trip-end").value = t.end || "";
  $("trip-guests").value = t.guests;
  $("trip-dog").checked = t.dog;
  $("trip-notes").value = t.notes || "";
  $("trip-created-by").textContent = t.created_by ? `Created by ${t.created_by}` : "";
  $("trip-created-by").hidden = !t.created_by;
  showRoundLine(t.id);
  $("trip-airbnb-url").value = "";
  showAirbnbInfo([]);
  showSightsInfo([]);
  resetDelete();
  renderTripItems();
  $("trips-list-view").hidden = true;
  $("trip-editor").hidden = false;
}
// "🗳️ #2 in '<title>' · 😍1 🙂2 🚫1 → Vote" when the trip is in the open round; Vote opens vote.js's dialog there.
async function showRoundLine(tripId) {
  const line = $("trip-round");
  line.hidden = true;
  line.replaceChildren();
  let round;
  try { round = await api("/api/rounds/current"); } catch { return; }
  const standing = round?.status === "open" && round.trips.find((x) => x.trip_id === tripId);
  if (!standing || trips.current?.id !== tripId) return;
  const { love, ok, nogo } = standing.counts;
  const go = document.createElement("button");
  go.type = "button";
  go.className = "link-btn";
  go.textContent = "Vote";
  go.addEventListener("click", () => { $("trips-dialog").close(); openVote(tripId); });
  line.replaceChildren(`🗳️ #${standing.rank} in '${round.title}' · 😍${love} 🙂${ok} 🚫${nogo} → `, go);
  line.hidden = false;
}
function renderTripItems() {
  for (const kind of ["hikes", "stays", "sights"]) {
    const ul = $(`trip-${kind}`);
    ul.replaceChildren();
    const items = trips.current[kind] || (trips.current[kind] = []);  // trips saved before sights existed
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "list-note";
      li.textContent = "None yet.";
      ul.appendChild(li);
      continue;
    }
    items.forEach((item, i) => {
      const li = document.createElement("li");
      const safe = /^https?:\/\//i.test(String(item.url ?? ""));
      const label = document.createElement(safe ? "a" : "span");
      label.textContent = kind === "sights" ? `${SIGHT_EMOJI[item.kind] || "🌿"} ${item.name}` : item.name;
      if (safe) { label.href = item.url; label.target = "_blank"; label.rel = "noopener"; }
      const tools = document.createElement("span");
      tools.className = "item-tools";
      const tool = (text, title, disabled, action) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "link-btn";
        b.textContent = text;
        b.title = title;
        b.setAttribute("aria-label", `${title}: ${item.name}`);
        b.disabled = disabled;
        b.addEventListener("click", action);  // only the editor changes; Save changes (gated) stores it
        tools.appendChild(b);
      };
      tool("↑", "Move up", i === 0, () => moveItem(kind, i, -1));
      tool("↓", "Move down", i === items.length - 1, () => moveItem(kind, i, 1));
      tool("✕", "Remove", false, () => { items.splice(i, 1); renderTripItems(); });
      li.append(label);
      if (kind === "stays") li.append(priceField(item));
      li.append(tools);
      ul.appendChild(li);
    });
  }
  lockEditor();
}
// Every stay shows its total price; EUR (or unpriced) stays can be edited – typed prices are EUR, saved with
// Save changes. Prices in another currency are shown read-only.
function priceField(item) {
  const label = document.createElement("label");
  label.className = "price-edit";
  const editable = item.currency == null || item.currency === "EUR";
  label.textContent = editable ? "€" : String(item.currency);
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.step = "any";  // search prices can have cents; step=1 would make them invalid
  input.placeholder = "total";
  input.title = "Total price for the stay";
  input.setAttribute("aria-label", `Total price${editable ? " in EUR" : ""}: ${item.name}`);
  input.disabled = !editable;
  if (item.total_price != null) input.value = String(item.total_price);
  input.addEventListener("input", () => {
    input.setCustomValidity("");
    const text = input.value.trim();
    const value = Number(text);
    if (input.validity.badInput || (text !== "" && !(Number.isFinite(value) && value >= 0))) {
      input.setCustomValidity("Total price: a number of 0 or more");
      input.classList.add("invalid");  // keep the last good price on the item until this is fixed
      return;
    }
    input.classList.remove("invalid");
    item.total_price = text === "" ? null : value;
    item.currency = text === "" ? null : "EUR";
  });
  label.appendChild(input);
  return label;
}
function showAirbnbInfo(messages) {
  const text = (messages || []).join(" · ");
  $("trip-airbnb-info").textContent = text;
  $("trip-airbnb-info").hidden = !text;
}
// Dates/guests/dog come from the trip editor (header dates as a fallback), never from the pasted link.
function lookupFields() {
  let start = $("trip-start").value, end = $("trip-end").value;
  if (!start && !end) ({ start, end } = dates());
  return { start: start || null, end: end || null,
    guests: Math.min(16, Math.max(1, Math.round(Number($("trip-guests").value)) || 1)), dog: $("trip-dog").checked };
}
// Save / Export / PDF / ＋ Add / Find nearby sights are disabled while a save, a lookup or a sights search runs,
// and the editor is locked (fields read-only, item buttons and prices disabled) so nothing typed is lost when
// the answer replaces the trip.
function setBusy(delta) {
  trips.busy += delta;
  for (const id of ["trip-save", "trip-export", "trip-pdf", "trip-airbnb-add", "trip-find-sights"]) {
    $(id).disabled = trips.busy > 0;
  }
  lockEditor();
}
const LOCKED_FIELDS = ["trip-name", "trip-start", "trip-end", "trip-guests", "trip-notes"];
function lockEditor() {
  const locked = trips.busy > 0;
  for (const el of document.querySelectorAll("#trip-editor .trip-items button, #trip-editor .trip-items input")) {
    if (locked && !el.disabled) {
      el.disabled = true;
      el.dataset.locked = "1";  // only these are re-enabled: ↑ on the first row etc. stay disabled
    } else if (!locked && el.dataset.locked) {
      el.disabled = false;
      delete el.dataset.locked;
    }
  }
  for (const id of LOCKED_FIELDS) $(id).readOnly = locked;
  $("trip-dog").disabled = locked;
}
const editorOpen = (id) => trips.current?.id === id && !$("trip-editor").hidden && $("trips-dialog").open;
async function addAirbnbLink() {
  const url = $("trip-airbnb-url").value.trim();
  if (!trips.current || !url || trips.busy) return;
  const tripId = trips.current.id;
  showTripsError("");
  showAirbnbInfo([]);
  $("trip-airbnb-spin").hidden = false;
  setBusy(1);
  try {
    const { stay, warnings } = await api("/api/stays/lookup", { url, ...lookupFields() });
    // A save may have replaced trips.current meanwhile: merge into whatever is current for the same trip.
    if (!editorOpen(tripId)) {
      showAirbnbInfo(["The trip was closed before the lookup finished – not added"]);
      return;
    }
    if (trips.current.stays.some((s) => s.id === stay.id)) {
      showAirbnbInfo(["Already in this trip"]);
      return;
    }
    trips.current.stays.push(stay);
    renderTripItems();
    $("trip-airbnb-url").value = "";
    showAirbnbInfo(warnings);
    await persistTrip();
  } catch (err) {
    showTripsError(err.message);
  } finally {
    $("trip-airbnb-spin").hidden = true;
    setBusy(-1);
  }
}
// 🌿 Find nearby sights: save what is on screen, then let the server add every named sight near the trip.
const NEARBY_KM = 5;
const NEARBY_TIMEOUT_MS = 150000;  // the server gives up on Overpass after 120 s
function showSightsInfo(messages) {
  const text = (messages || []).join(" · ");
  $("trip-sights-info").textContent = text;
  $("trip-sights-info").hidden = !text;
}
async function findNearbySights() {
  if (!trips.current || trips.busy) return;
  // Validate before locking: read-only and disabled fields are skipped by the browser's validity check.
  if (!$("trip-editor").checkValidity()) {
    $("trip-editor").reportValidity();
    showTripsError("Fix the highlighted field first");
    return;
  }
  const tripId = trips.current.id;
  showTripsError("");
  showSightsInfo([]);
  $("trip-sights-spin").hidden = false;
  setBusy(1);
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), NEARBY_TIMEOUT_MS);
  try {
    if (!(await persistTrip()) || !editorOpen(tripId)) return;
    let res;
    try {
      res = await api(tripPath(tripId, "/nearby-sights"), { radius_km: NEARBY_KM }, "POST", abort.signal);
    } catch (err) {
      throw err.name === "AbortError" ? new Error("Search took too long – try again") : err;
    }
    if (!editorOpen(tripId)) return;  // the editor moved to another trip or closed meanwhile
    trips.current = res.trip;
    renderTripItems();
    showSightsInfo([res.added > 0 ? `Added ${plural(res.added, "new sight")} (${res.found} found within ${NEARBY_KM} km)`
      : `No new named sights within ${NEARBY_KM} km`, ...(res.warnings || [])]);
    refreshTrips().catch(() => {});  // only the list count: the sights are saved, don't report a failure here
  } catch (err) {
    if (editorOpen(tripId)) showTripsError(err.message);
  } finally {
    clearTimeout(timer);
    $("trip-sights-spin").hidden = true;
    setBusy(-1);
  }
}
function moveItem(kind, i, delta) {
  const items = trips.current[kind];
  [items[i], items[i + delta]] = [items[i + delta], items[i]];
  renderTripItems();
}
function editorBody() {
  const t = trips.current;
  return { name: $("trip-name").value, start: $("trip-start").value || null, end: $("trip-end").value || null,
    guests: Math.min(16, Math.max(1, Math.round(Number($("trip-guests").value)) || 1)), dog: $("trip-dog").checked,
    notes: $("trip-notes").value, source: t.source, score: t.score, hikes: t.hikes, stays: t.stays, sights: t.sights || [],
    expected_updated_at: t.updated_at };  // server answers 409 if another tab/visitor saved in between
}
// Saves run one after another (each PUT carries the updated_at the previous one returned).
function persistTrip() {
  const tripId = trips.current?.id;
  const run = trips.saving.then(() => persistNow(tripId));
  trips.saving = run.catch(() => {});
  return run;
}
async function persistNow(tripId) {
  if (!tripId || trips.current?.id !== tripId) return false;  // the editor moved to another trip
  if (!$("trip-editor").checkValidity()) {
    $("trip-editor").reportValidity();
    showTripsError("Fix the highlighted field first");
    return false;
  }
  showTripsError("");
  setBusy(1);
  try {
    const saved = await api(tripPath(tripId), editorBody(), "PUT");
    if (trips.current?.id !== tripId) return false;
    trips.current = saved;
    renderTripItems();  // the rows must act on the saved objects, not the replaced ones
    await refreshTrips();
    return true;
  } catch (err) {
    showTripsError(err.message);
    return false;
  } finally {
    setBusy(-1);
  }
}
function saveTrip(event) {
  event.preventDefault();
  requireLogin(saveNow);
}
async function saveNow() {
  if (!trips.current || !(await persistTrip())) return;
  $("trip-save").textContent = "✓ Saved";
  setTimeout(() => { $("trip-save").textContent = "Save changes"; }, 1500);
}
async function exportTrip() {
  // Export what is on screen: save first, then download the server's Markdown. Logged out nothing can be saved
  // (the export itself is open), so the stored trip is downloaded as it is.
  if (!trips.current || (auth.user && !(await persistTrip()))) return;
  window.location.href = tripPath(trips.current.id, "/export.md");
}
async function printTrip() {
  // Same as export, but opens the printable page (its print dialog offers "Save as PDF"). The tab is opened
  // right in the click: Safari blocks a window.open that comes after an await (the save) as a pop-up.
  if (!trips.current || trips.busy) return;
  if (!auth.user) {  // logged out: no save (it would get a 401) – open the stored trip's page straight away
    const direct = window.open(tripPath(trips.current.id, "/print"), "_blank");
    if (direct) direct.opener = null;
    else showTripsError("Allow pop-ups to open the PDF page");
    return;
  }
  const tab = window.open("", "_blank");
  if (tab) {
    tab.opener = null;
    try { tab.document.title = "Preparing PDF…"; } catch { /* not ours to touch */ }
  }
  if (!(await persistTrip())) {
    tab?.close();
    return;
  }
  const url = tripPath(trips.current.id, "/print");
  if (tab) {
    tab.location.replace(url);
    return;
  }
  // No "noopener" here: with it window.open always returns null, so a blocked pop-up couldn't be told apart.
  const late = window.open(url, "_blank");
  if (late) late.opener = null;
  else showTripsError("Allow pop-ups to open the PDF page");
}
// Shows a trip (default: the one in the editor, under the name typed there) on the map; the vote dialog passes its own.
function showTripOnMap(t = trips.current, name = $("trip-name").value.trim() || t?.name) {
  if (!t) return;
  window.mobile?.showMap();  // phone: from the 📋 List tab too, the trip is shown on the map
  clearAll();
  t.hikes.forEach(addHike);
  t.stays.forEach(addStay);
  const sightMarkers = showTripSights(t.sights || []);  // after clearAll, which empties the previous trip's sights
  state.comboView = true;  // clicking a trip hike must not replace its stays with a new search
  $("hike-count").textContent = `(${state.hikes.size})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("stay-note").textContent = `in trip “${name}”`;
  notices([`Showing trip “${name}”`]);
  $("trips-dialog").close();
  fitLayers([...hikeLayer.getLayers(), ...stayLayer.getLayers(), ...sightMarkers]);
}
function resetDelete() {
  clearTimeout(trips.deleteTimer);
  trips.deleteTimer = null;
  $("trip-delete").textContent = "Delete";
}
async function deleteTrip() {
  if (!trips.current) return;
  if (!trips.deleteTimer) {  // first click arms, second click (within 4 s) deletes
    $("trip-delete").textContent = "Confirm delete";
    trips.deleteTimer = setTimeout(resetDelete, 4000);
    return;
  }
  resetDelete();
  try {
    await api(tripPath(trips.current.id), undefined, "DELETE");
    showTripList();
    await reloadTripList();
  } catch (err) {
    showTripsError(err.message);
  }
}

$("trips-btn").addEventListener("click", () => openTripsDialog());
$("trips-close").addEventListener("click", () => $("trips-dialog").close());
$("trips-dialog").addEventListener("close", () => window.mobile?.dialogClosed());  // phone: back to the previous tab
$("trip-back").addEventListener("click", () => { showTripsError(""); showTripList(); reloadTripList(); });
$("trip-editor").addEventListener("submit", saveTrip);
$("trip-show").addEventListener("click", () => showTripOnMap());
$("trip-export").addEventListener("click", exportTrip);
$("trip-pdf").addEventListener("click", printTrip);
$("trip-delete").addEventListener("click", () => requireLogin(deleteTrip));
$("trip-airbnb-add").addEventListener("click", () => requireLogin(addAirbnbLink));
$("trip-find-sights").addEventListener("click", () => requireLogin(findNearbySights));
$("trip-airbnb-url").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();  // Enter here adds the link; it must not submit (save) the editor form
  requireLogin(addAirbnbLink);
});
refreshTrips().catch(() => { $("trips-count").textContent = ""; });
