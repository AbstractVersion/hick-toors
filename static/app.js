"use strict";

const DRIVE_STEPS = ["auto", 60, 90, 120, 180, 240, 300, 360, 480, "unlimited"];
const BADGES = {
  pets: "🐕 Pets allowed",
  pets_garden: "🐕🌳 Pets allowed + garden",
  garden: "🌳 Garden",
  garden_unconfirmed: "⚠️ Garden – pets not confirmed",
};
const $ = (id) => document.getElementById(id);
const state = { config: null, hikes: new Map(), stays: new Map(), markers: new Map(), items: new Map(), comboView: false, staysRequest: 0 };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtMinutes(m, estimated) {
  if (m == null) return "–";
  const h = Math.floor(m / 60), mm = m % 60;
  return `${estimated ? "≈ " : ""}${h ? h + "h " : ""}${mm}m`;
}
function money(price, currency) {
  if (price == null) return "";
  return `${!currency || currency === "EUR" ? "€" : currency + " "}${Math.round(price)}`;
}
function fmtDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function driveText(step) {
  if (step === "auto") return "Auto (from days)";
  if (step === "unlimited") return "Unlimited";
  return `≤ ${fmtMinutes(step)}`;
}

// ---- map ----
const map = L.map("map").setView([38.5, 23.5], 7);
const tiles = {
  Topo: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)" }),
  Streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
  Satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 18, attribution: "Tiles © Esri" }),
};
tiles.Topo.addTo(map);
L.control.layers(tiles).addTo(map);
const hikeLayer = L.markerClusterGroup();
const stayLayer = L.markerClusterGroup();
const overlay = L.layerGroup();
map.addLayer(hikeLayer);
map.addLayer(stayLayer);
overlay.addTo(map);
let reachCircle = null;

function safeUrl(u) { return /^https?:\/\//i.test(String(u ?? "")) ? u : "#"; }
function pin(emoji, cls, label) {
  return L.divIcon({ className: `pin ${cls}`, iconSize: null,
    html: `<span>${emoji}</span>${label ? `<b>${esc(label)}</b>` : ""}` });
}
function hikePopup(h) {
  return `<div class="popup">${h.thumbnail ? `<img src="${esc(safeUrl(h.thumbnail))}" alt="">` : ""}
    <strong>${esc(h.name)}</strong><br>
    ${esc(h.near || "")}<br>
    ${h.distance_km ?? "?"} km · +${h.elevation_gain_m ?? "?"} m · ${esc(h.difficulty || "?")} · TrailRank ${h.trailrank ?? "?"}<br>
    🚗 ${h.ferry_needed ? "⛴️ ferry needed" : fmtMinutes(h.drive_minutes, h.drive_estimated)}<br>
    <a href="${esc(safeUrl(h.url))}" target="_blank" rel="noopener">Open on Wikiloc ↗</a></div>`;
}
function stayPopup(s) {
  return `<div class="popup">${s.photo ? `<img src="${esc(safeUrl(s.photo))}" alt="">` : ""}
    <strong>${esc(s.name)}</strong><br>
    ${s.source === "airbnb" ? "Airbnb" : "Booking.com"} · ${esc(money(s.total_price, s.currency))} total
    ${s.rating != null ? ` · ★ ${s.rating}` : ""}<br>
    ${s.badge ? `<span class="badge ${esc(s.badge)}">${esc(BADGES[s.badge])}</span><br>` : ""}
    ${s.distance_km != null ? `${s.distance_km} km from trailhead<br>` : ""}
    <a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">Open listing ↗</a></div>`;
}

// ---- notices / api ----
function notices(list) {
  const box = $("notices");
  box.replaceChildren();
  for (const w of list || []) {
    const div = document.createElement("div");
    div.className = "notice";
    div.textContent = w;
    box.appendChild(div);
  }
}
async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = Array.isArray(data.detail)
      ? data.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join("; ")
      : data.detail || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}
const mode = () => document.querySelector("input[name=mode]:checked").value;
const driveLimit = () => DRIVE_STEPS[Number($("drive").value)];
const dates = () => ({ start: $("start").value, end: $("end").value });

// ---- list + markers ----
function listItem(ul, id, title, meta, badge, onClick) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const div = document.createElement("div");
  div.className = "meta";
  div.textContent = meta;
  li.append(strong, div);
  if (badge) {
    const b = document.createElement("span");
    b.className = `badge ${badge}`;
    b.textContent = BADGES[badge];
    li.appendChild(b);
  }
  li.addEventListener("click", onClick);
  li.addEventListener("mouseenter", () => li.classList.add("active"));
  li.addEventListener("mouseleave", () => li.classList.remove("active"));
  ul.appendChild(li);
  state.items.set(id, li);
  return li;
}
function linkMarker(id, marker, layer) {
  marker.on("mouseover", () => state.items.get(id)?.classList.add("active"));
  marker.on("mouseout", () => state.items.get(id)?.classList.remove("active"));
  layer.addLayer(marker);
  state.markers.set(id, marker);
}
function focusMarker(id) {
  const marker = state.markers.get(id);
  if (!marker) return;
  const layer = state.hikes.has(id) ? hikeLayer : stayLayer;
  layer.zoomToShowLayer(marker, () => marker.openPopup());
}
function clearStays() {
  stayLayer.clearLayers();
  for (const id of state.stays.keys()) { state.markers.delete(id); state.items.delete(id); }
  state.stays.clear();
  $("stay-list").replaceChildren();
  $("stay-count").textContent = "";
}
function clearAll() {
  state.comboView = false;
  hikeLayer.clearLayers();
  overlay.clearLayers();
  clearStays();
  state.hikes.clear();
  state.markers.clear();
  state.items.clear();
  for (const id of ["hike-list", "combo-list"]) $(id).replaceChildren();
  for (const id of ["hike-count", "combo-count"]) $(id).textContent = "";
}
function addHike(h) {
  state.hikes.set(h.id, h);
  const marker = L.marker([h.lat, h.lon], { icon: pin("🥾", "hike") }).bindPopup(hikePopup(h));
  marker.on("click", () => { if (!state.comboView) selectHike(h.id); });
  linkMarker(h.id, marker, hikeLayer);
  const drive = h.ferry_needed ? "⛴️ ferry" : `🚗 ${fmtMinutes(h.drive_minutes, h.drive_estimated)}`;
  listItem($("hike-list"), h.id, h.name,
    `${h.distance_km ?? "?"} km · +${h.elevation_gain_m ?? "?"} m · ${h.difficulty || "?"} · ${drive}`,
    null, () => { if (!state.comboView) selectHike(h.id); focusMarker(h.id); });
}
function addStay(s) {
  state.stays.set(s.id, s);
  const marker = L.marker([s.lat, s.lon], { icon: pin("🏡", s.source, money(s.total_price, s.currency)) })
    .bindPopup(stayPopup(s));
  linkMarker(s.id, marker, stayLayer);
  const rating = s.rating != null ? ` · ★ ${s.rating}` : "";
  const km = s.distance_km != null ? ` · ${s.distance_km} km` : "";
  listItem($("stay-list"), s.id, s.name,
    `${s.source === "airbnb" ? "Airbnb" : "Booking.com"} · ${money(s.total_price, s.currency)}${rating}${km}`,
    s.badge, () => focusMarker(s.id));
}
function fitAll() {
  const layers = [...hikeLayer.getLayers(), ...stayLayer.getLayers()];
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.15));
}
function drawReach(minutes) {
  if (reachCircle) { map.removeLayer(reachCircle); reachCircle = null; }
  if (minutes == null || !state.config) return;
  const home = state.config.home;
  reachCircle = L.circle([home.lat, home.lon], { radius: minutes * 1000, color: "#2f6f4e",
    weight: 1, fillOpacity: 0.06, interactive: true })
    .bindTooltip("≈ straight-line reach").addTo(map);
}

// ---- actions ----
async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (err) { notices([err.message]); } finally { button.disabled = false; }
}
async function searchHikes() {
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit() };
  if (body.mode === "place") body.place = $("place").value;
  const data = await api("/api/hikes", body);
  clearAll();
  notices(data.warnings);
  data.hikes.forEach(addHike);
  $("hike-count").textContent = `(${data.hikes.length})`;
  drawReach(body.mode === "near_home" ? data.drive_limit_minutes : null);
  if (!data.hikes.length && !data.warnings.length) notices(["No hikes found – try more days or a bigger drive limit."]);
  fitAll();
}
async function selectHike(id) {
  const request = ++state.staysRequest;
  try {
    for (const li of document.querySelectorAll("#hike-list li.selected")) li.classList.remove("selected");
    state.items.get(id)?.classList.add("selected");
    const data = await api("/api/stays", { ...dates(), hike_id: id, dog: $("dog").checked,
      guests: state.config?.default_guests ?? 2 });
    if (request !== state.staysRequest) return;
    clearStays();
    notices(data.warnings);
    data.stays.forEach(addStay);
    $("stay-count").textContent = `(${data.stays.length})`;
    const hike = state.hikes.get(id);
    const layers = [state.markers.get(id), ...stayLayer.getLayers()].filter(Boolean);
    if (layers.length > 1) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.2));
    else if (hike) map.setView([hike.lat, hike.lon], 12);
  } catch (err) {
    if (request === state.staysRequest) notices([err.message]);
  }
}

function renderCombos(combos) {
  combos.forEach((c, i) => {
    if (!state.hikes.has(c.hike.id)) addHike(c.hike);
    if (!state.stays.has(c.stay.id)) addStay(c.stay);
    const label = c.score != null ? `${c.score}/100` : c.note || "";
    L.polyline([[c.hike.lat, c.hike.lon], [c.stay.lat, c.stay.lon]],
      { dashArray: "6 6", color: "#2f6f4e", weight: 2 }).bindTooltip(esc(label)).addTo(overlay);
    const b = c.breakdown;
    const meta = b ? `hike ${b.hike} · stay ${b.stay} · near ${b.proximity} · price ${b.price}` : (c.note || "");
    const li = listItem($("combo-list"), `combo:${i}`, `${i + 1}. ${c.hike.name} + ${c.stay.name}`,
      `${c.score != null ? c.score + "/100 · " : ""}${meta}`, c.stay.badge,
      () => map.fitBounds(L.latLngBounds([[c.hike.lat, c.hike.lon], [c.stay.lat, c.stay.lon]]).pad(0.3)));
    li.title = meta;
  });
  $("combo-count").textContent = `(${combos.length})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("hike-count").textContent = `(${state.hikes.size})`;
  if (combos.length > 0) state.comboView = true;
}
async function planTrip() {
  const data = await api("/api/trips", { ...dates(), drive_limit: driveLimit(), dog: $("dog").checked,
    guests: state.config?.default_guests ?? 2 });
  clearAll();
  notices(data.warnings);
  renderCombos(data.combos);
  if (!data.combos.length && !data.warnings.length) notices(["No combos found – try more days or turn off the dog filter."]);
  fitAll();
}
async function ask(event) {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (question.length < 3) return;
  const started = Date.now();
  const timer = setInterval(() => {
    $("ask-status").textContent = `Claude is planning… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  await withBusy($("ask-button"), async () => {
    try {
      const data = await api("/api/ask", { question, context: { ...dates(), dog: $("dog").checked,
        drive_limit: driveLimit(), home: state.config?.home?.name } });
      $("ask-answer").textContent = data.summary || "";
      notices(data.warnings);
      if (data.hikes.length || data.stays.length || data.combos.length) {
        clearAll();
        data.hikes.forEach(addHike);
        data.stays.forEach(addStay);
        renderCombos(data.combos);
        fitAll();
      }
    } finally {
      clearInterval(timer);
      $("ask-status").textContent = "";
    }
  });
}

// ---- init ----
async function init() {
  const today = new Date();
  const friday = new Date(today);
  friday.setDate(today.getDate() + ((5 - today.getDay() + 7) % 7));
  const sunday = new Date(friday);
  sunday.setDate(friday.getDate() + 2);
  $("start").value = fmtDate(friday);
  $("end").value = fmtDate(sunday);
  $("drive-label").textContent = driveText(driveLimit());
  $("drive").addEventListener("input", () => { $("drive-label").textContent = driveText(driveLimit()); });
  for (const radio of document.querySelectorAll("input[name=mode]")) {
    radio.addEventListener("change", () => { $("place").hidden = mode() !== "place"; });
  }
  $("search").addEventListener("click", () =>
    withBusy($("search"), () => (mode() === "trip" ? planTrip() : searchHikes())));
  $("ask-form").addEventListener("submit", ask);
  try {
    state.config = await api("/api/config");
    const home = state.config.home;
    $("home-label").textContent = `🏠 ${home.name}`;
    L.marker([home.lat, home.lon], { icon: pin("🏠", "home") }).bindPopup(esc(home.name)).addTo(map);
    if (!state.config.ask_available) {
      $("ask-input").disabled = true;
      $("ask-button").disabled = true;
      $("ask-input").placeholder = "Ask Claude unavailable – run `claude` once to log in";
    }
  } catch (err) {
    notices([err.message]);
  }
}
init();
