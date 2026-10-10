"use strict";
// Saved trips: ＋ Trip menu on hikes/stays, 💾 Save on Trip picks, and the 🧳 My trips dialog.
// Loaded after app.js and uses its globals ($, esc, state, api, notices, dates, guests, clearAll, ...).
const trips = { list: [], current: null, deleteTimer: null, handPriced: new Set() };  // handPriced: stay ids
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
function itemOf(kind, id) { return (kind === "hike" ? state.hikes : state.stays).get(id); }

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
    hikes: kind === "hike" ? [item] : [], stays: kind === "stay" ? [item] : [] });
  rememberTrip(trip.id);
  await refreshTrips();
  notices([`Added to “${trip.name}” – rename it in 🧳 My trips`]);
}
async function addToTrip(summary, kind, item) {
  const before = kind === "hike" ? summary.hike_count : summary.stay_count;
  const trip = await api(tripPath(summary.id, "/items"), { [kind]: item });
  rememberTrip(trip.id);
  await refreshTrips();
  const after = (kind === "hike" ? trip.hikes : trip.stays).length;
  notices([after > before ? `Added to “${trip.name}”` : `Already in “${trip.name}”`]);
}
// Capture phase: a ＋ Trip click must not also select the hike / focus the stay underneath it.
document.addEventListener("click", (event) => {
  const button = event.target.closest?.(".add-trip");
  if (button) {
    event.preventDefault();
    event.stopPropagation();
    openTripMenu(button).catch((err) => notices([err.message]));
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
  b.addEventListener("click", async (event) => {
    event.stopPropagation();
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
  });
  return b;
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
    meta.textContent = `${tripDates(t)} · ${plural(t.hike_count, "hike")} · ${plural(t.stay_count, "stay")}${t.source === "suggested" ? " · ⭐" : ""}`;
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
  $("trip-airbnb-url").value = "";
  showAirbnbInfo([]);
  trips.handPriced.clear();
  resetDelete();
  renderTripItems();
  $("trips-list-view").hidden = true;
  $("trip-editor").hidden = false;
}
function renderTripItems() {
  for (const kind of ["hikes", "stays"]) {
    const ul = $(`trip-${kind}`);
    ul.replaceChildren();
    const items = trips.current[kind];
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
      label.textContent = item.name;
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
        b.addEventListener("click", action);
        tools.appendChild(b);
      };
      tool("↑", "Move up", i === 0, () => moveItem(kind, i, -1));
      tool("↓", "Move down", i === items.length - 1, () => moveItem(kind, i, 1));
      tool("✕", "Remove", false, () => { items.splice(i, 1); renderTripItems(); });
      li.append(label);
      if (kind === "stays" && (item.total_price == null || trips.handPriced.has(item.id))) li.append(priceField(item));
      li.append(tools);
      ul.appendChild(li);
    });
  }
}
// A stay without a price gets a "€ total" field; typed prices are EUR and saved with Save changes.
function priceField(item) {
  trips.handPriced.add(item.id);  // keep the field while editing, even once a price is typed
  const label = document.createElement("label");
  label.className = "price-edit";
  label.textContent = "€";
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.step = "1";
  input.placeholder = "total";
  input.title = "Total price for the stay (EUR)";
  input.setAttribute("aria-label", `Total price in EUR: ${item.name}`);
  if (item.total_price != null) input.value = String(item.total_price);
  input.addEventListener("input", () => {
    const value = input.value.trim() === "" ? NaN : Number(input.value);
    if (Number.isFinite(value) && value >= 0) {
      item.total_price = value;
      item.currency = "EUR";
    } else {
      item.total_price = null;
      item.currency = null;
    }
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
async function addAirbnbLink() {
  const url = $("trip-airbnb-url").value.trim();
  const t = trips.current;
  if (!t || !url || !$("trip-airbnb-spin").hidden) return;
  showTripsError("");
  showAirbnbInfo([]);
  $("trip-airbnb-spin").hidden = false;
  $("trip-airbnb-add").disabled = true;
  try {
    const { stay, warnings } = await api("/api/stays/lookup", { url, ...lookupFields() });
    if (trips.current !== t) return;  // the editor moved on while Airbnb answered
    if (t.stays.some((s) => s.id === stay.id)) {
      showAirbnbInfo(["Already in this trip"]);
      return;
    }
    t.stays.push(stay);
    renderTripItems();
    $("trip-airbnb-url").value = "";
    showAirbnbInfo(warnings);
    await persistTrip();
  } catch (err) {
    showTripsError(err.message);
  } finally {
    $("trip-airbnb-spin").hidden = true;
    $("trip-airbnb-add").disabled = false;
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
    notes: $("trip-notes").value, source: t.source, score: t.score, hikes: t.hikes, stays: t.stays,
    expected_updated_at: t.updated_at };  // server answers 409 if another tab/visitor saved in between
}
async function persistTrip() {
  showTripsError("");
  try {
    trips.current = await api(tripPath(trips.current.id), editorBody(), "PUT");
    renderTripItems();  // the rows must act on the saved objects, not the replaced ones
    await refreshTrips();
    return true;
  } catch (err) {
    showTripsError(err.message);
    return false;
  }
}
async function saveTrip(event) {
  event.preventDefault();
  if (!trips.current || !(await persistTrip())) return;
  $("trip-save").textContent = "✓ Saved";
  setTimeout(() => { $("trip-save").textContent = "Save changes"; }, 1500);
}
async function exportTrip() {
  // Export what is on screen: save first, then download the server's Markdown.
  if (!trips.current || !(await persistTrip())) return;
  window.location.href = tripPath(trips.current.id, "/export.md");
}
function showTripOnMap() {
  const t = trips.current;
  if (!t) return;
  const name = $("trip-name").value.trim() || t.name;
  clearAll();
  t.hikes.forEach(addHike);
  t.stays.forEach(addStay);
  state.comboView = true;  // clicking a trip hike must not replace its stays with a new search
  $("hike-count").textContent = `(${state.hikes.size})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("stay-note").textContent = `in trip “${name}”`;
  notices([`Showing trip “${name}”`]);
  $("trips-dialog").close();
  fitAll();
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
$("trip-back").addEventListener("click", () => { showTripsError(""); showTripList(); reloadTripList(); });
$("trip-editor").addEventListener("submit", saveTrip);
$("trip-show").addEventListener("click", showTripOnMap);
$("trip-export").addEventListener("click", exportTrip);
$("trip-delete").addEventListener("click", deleteTrip);
$("trip-airbnb-add").addEventListener("click", addAirbnbLink);
$("trip-airbnb-url").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();  // Enter here adds the link; it must not submit (save) the editor form
  addAirbnbLink();
});
refreshTrips().catch(() => { $("trips-count").textContent = ""; });
