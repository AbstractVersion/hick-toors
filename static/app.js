"use strict";

const DRIVE_STEPS = ["auto", 60, 90, 120, 180, 240, 300, 360, 480, "unlimited"];
const BADGES = {
  pets: "🐕 Pets allowed",
  pets_garden: "🐕🌳 Pets allowed + garden",
  garden: "🌳 Garden",
  garden_unconfirmed: "⚠️ Garden – pets not confirmed",
};
const $ = (id) => document.getElementById(id);
const state = { config: null, hikes: new Map(), stays: new Map(), markers: new Map(), items: new Map(), comboView: false, staysRequest: 0,
  stayWarnings: {}, bookingFailed: false, staySpec: null, lastHikeBody: null, nextPageOffset: null, hikeTotal: null, hikeGen: 0, hikeWarnings: [], catalogue: null, filters: null, dialogs: {} };

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

// ---- drawn search area (Leaflet.draw: rectangle + circle only, one shape at a time) ----
const SHAPE_STYLE = { color: "#b5562f", weight: 2, fillOpacity: 0.08 };
const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    polyline: false, polygon: false, marker: false, circlemarker: false,
    rectangle: { shapeOptions: SHAPE_STYLE, showArea: false },
    circle: { shapeOptions: SHAPE_STYLE },
  },
  edit: { featureGroup: drawnItems },
});
map.addControl(drawControl);
let activeDraw = null;
function stopDrawing() {
  if (activeDraw) { activeDraw.disable(); activeDraw = null; }
  $("draw-hint").hidden = true;
  $("draw-rect-btn").classList.remove("active");
  $("draw-circle-btn").classList.remove("active");
}
function startDrawing(shape) {
  stopDrawing();
  activeDraw = shape === "circle"
    ? new L.Draw.Circle(map, drawControl.options.draw.circle)
    : new L.Draw.Rectangle(map, drawControl.options.draw.rectangle);
  activeDraw.enable();
  $("draw-hint").textContent = `Press and drag on the map to draw a ${shape} – Esc to cancel`;
  $("draw-hint").hidden = false;
  $(shape === "circle" ? "draw-circle-btn" : "draw-rect-btn").classList.add("active");
}
map.on(L.Draw.Event.CREATED, (event) => {
  drawnItems.clearLayers();
  drawnItems.addLayer(event.layer);
  stopDrawing();
  setMode("drawn");
});
map.on(L.Draw.Event.DRAWSTOP, stopDrawing);
map.on(L.Draw.Event.DELETED, () => {
  if (drawnItems.getLayers().length === 0 && mode() === "drawn") {
    setMode("near_home");
    notices(["Drawn area removed – back to Near home"]);
  }
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") stopDrawing(); });
function drawnArea() {
  const layer = drawnItems.getLayers()[0];
  if (!layer) return null;
  if (layer instanceof L.Circle) {
    const c = layer.getLatLng();
    return { shape: "circle", lat: c.lat, lon: c.lng, radius_m: Math.round(layer.getRadius()) };
  }
  const b = layer.getBounds();
  return { shape: "rectangle", south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() };
}
function areaProblem(area) {
  if (!area) return "Draw a rectangle or circle first";
  if (area.shape === "circle" && (area.radius_m < 200 || area.radius_m > 300000)) {
    return "Draw a circle with a radius between 200 m and 300 km";
  }
  if (area.shape === "rectangle" && (area.south >= area.north || area.west >= area.east)) {
    return "Draw a bigger rectangle";
  }
  return null;
}

// ---- loading indicators: the newest request for a key owns its spinner; stale ones never touch it ----
const SPIN_SECTION = { hikes: "hike-section", "hikes-more": "hike-section", "stay:airbnb": "stay-section",
  "stay:booking": "stay-section", "stay:retry": "stay-section", combos: "combo-section", ask: "ask" };
const loading = { owners: {}, seq: 0 };
function paintLoading(key) {
  const on = key in loading.owners;
  for (const el of document.querySelectorAll(`[data-spin="${key}"]`)) el.hidden = !on;
  const section = SPIN_SECTION[key];
  if (section) {
    const busy = Object.keys(loading.owners).some((k) => SPIN_SECTION[k] === section);
    $(section)?.setAttribute("aria-busy", busy ? "true" : "false");
  }
}
function startLoading(key) {
  const id = ++loading.seq;
  loading.owners[key] = id;
  paintLoading(key);
  return id;
}
function stopLoading(key, id) {
  if (loading.owners[key] !== id) return;  // a newer request owns this spinner now
  delete loading.owners[key];
  paintLoading(key);
}

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
      ? data.detail.map((d) => {
        const loc = (d.loc || []).filter((part) => part !== "body").join(".");
        const msg = String(d.msg).replace(/^Value error, /, "");
        return loc ? `${loc} : ${msg}` : msg;
      }).join("; ")
      : data.detail || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}
const mode = () => document.querySelector("input[name=mode]:checked").value;
const driveLimit = () => DRIVE_STEPS[Number($("drive").value)];
const dates = () => ({ start: $("start").value, end: $("end").value });
const guests = () => state.filters?.guests ?? state.config?.default_guests ?? 2;
function setMode(value) {
  document.querySelector(`input[name=mode][value="${value}"]`).checked = true;
  $("place").hidden = value !== "place";
}

// ---- filter popups ----
function updateFilterBadges() {
  for (const kind of ["hike", "stay"]) {
    const n = state.filters ? countActive(state.filters[kind], state.catalogue[kind]) : 0;
    const badge = $(`${kind}-filter-count`);
    badge.textContent = `•${n}`;
    badge.hidden = n === 0;
  }
}
function applyShared(shared) {
  if ("start" in shared) { $("start").value = shared.start; $("end").value = shared.end; }
  if ("drive" in shared) { $("drive").value = String(shared.drive); $("drive-label").textContent = driveText(driveLimit()); }
  if ("dog" in shared) $("dog").checked = shared.dog;
  if ("guests" in shared) state.filters.guests = shared.guests;
}
function setupFilterDialogs() {
  const dateFields = [{ name: "start", label: "📅 From", type: "date" }, { name: "end", label: "to", type: "date" }];
  const make = (kind, title, shared, readShared) => createFilterDialog({
    id: `${kind}-dialog`, title, section: state.catalogue[kind], shared,
    getValues: () => state.filters[kind],
    getShared: readShared,
    onApply: (values, sharedValues) => {
      state.filters[kind] = values;
      applyShared(sharedValues);
      saveFilters(state.filters);
      updateFilterBadges();
    },
  });
  state.dialogs.hike = make("hike", "⚙️ Hike filters", [
    ...dateFields,
    { name: "drive", label: "🚗 Drive", type: "slider", min: 0, max: DRIVE_STEPS.length - 1, step: 1,
      format: (i) => driveText(DRIVE_STEPS[i]) },
  ], () => ({ ...dates(), drive: Number($("drive").value) }));
  state.dialogs.stay = make("stay", "⚙️ Stay filters", [
    ...dateFields,
    { name: "dog", label: "🐕 Dog", type: "checkbox" },
    { name: "guests", label: "👥 Guests", type: "number", min: 1, max: 16, step: 1 },
  ], () => ({ ...dates(), dog: $("dog").checked, guests: guests() }));
  for (const kind of ["hike", "stay"]) {
    const button = $(`${kind}-filters-btn`);
    button.disabled = false;
    button.addEventListener("click", () => state.dialogs[kind].open());
  }
  updateFilterBadges();
}

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
  $("stay-note").textContent = "";
  state.stayWarnings = {};
  state.bookingFailed = false;
  state.staySpec = null;
  $("retry-booking").hidden = true;
}
function clearAll() {
  state.comboView = false;
  state.hikeGen += 1;
  state.staysRequest += 1;  // a stay answer still on its way belongs to the old search
  state.lastHikeBody = null;
  state.nextPageOffset = null;
  state.hikeTotal = null;
  state.hikeWarnings = [];
  $("hike-total").textContent = "";
  $("load-more").hidden = true;
  hikeLayer.clearLayers();
  overlay.clearLayers();
  clearStays();
  state.hikes.clear();
  state.markers.clear();
  state.items.clear();
  $("combo-section").hidden = true;
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
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit(), filters: state.filters?.hike };
  if (body.mode === "place") body.place = $("place").value;
  if (body.mode === "drawn") {
    const area = drawnArea();
    const problem = areaProblem(area);
    if (problem) { notices([problem]); return; }
    body.area = area;
  }
  const spin = startLoading("hikes");
  let data;
  try { data = await api("/api/hikes", body); } finally { stopLoading("hikes", spin); }
  clearAll();
  state.lastHikeBody = body;
  state.hikeWarnings = data.warnings || [];
  notices(data.warnings);
  data.hikes.forEach(addHike);
  showHikeMeta(data);
  drawReach(body.mode === "near_home" ? data.drive_limit_minutes : null);
  if (!data.hikes.length && !data.warnings.length) notices(["No hikes found – try more days or a bigger drive limit."]);
  fitAll();
  // A drawn search also brings the stays inside the shape (not awaited: Search stays usable meanwhile).
  if (body.mode === "drawn") loadAreaStays(body.area);}
function showHikeMeta(data) {
  $("hike-count").textContent = `(${state.hikes.size})`;
  state.nextPageOffset = data.next_page_offset ?? null;
  state.hikeTotal = data.total_available ?? state.hikeTotal;  // a failed Load more carries no total
  $("hike-total").textContent = state.hikeTotal != null
    ? `Showing ${state.hikes.size.toLocaleString("en-US")} of ${Number(state.hikeTotal).toLocaleString("en-US")} routes in this area`
    : "";
  $("load-more").hidden = state.nextPageOffset == null;
}
async function loadMoreHikes() {
  if (!state.lastHikeBody || state.nextPageOffset == null) return;
  const gen = state.hikeGen;
  const spin = startLoading("hikes-more");
  let data;
  try {
    data = await api("/api/hikes", { ...state.lastHikeBody, page_offset: state.nextPageOffset });
  } finally { stopLoading("hikes-more", spin); }
  if (gen !== state.hikeGen) return;  // a new search replaced the list meanwhile
  state.hikeWarnings = [...new Set([...state.hikeWarnings, ...(data.warnings || [])])];
  notices(state.hikeWarnings);
  data.hikes.filter((h) => !state.hikes.has(h.id)).forEach(addHike);
  showHikeMeta(data);
}
// Stays load as two parallel requests (Airbnb is quick, Booking.com is retried and slow); each answer
// is merged into the same list as it arrives. Every request is guarded by state.staysRequest.
const STAY_PROVIDERS = ["airbnb", "booking"];
const BOOKING_DOWN = /^Booking\.com unavailable/;
function showStayNotices(spec) {
  notices([...spec.baseWarnings(), ...Object.values(state.stayWarnings).flat()]);
}
function mergeStays(list) {
  list.filter((s) => !state.stays.has(s.id)).forEach(addStay);
  $("stay-count").textContent = `(${state.stays.size})`;
}
async function requestStays(provider, request, spec, key) {
  const spin = startLoading(key);
  try {
    const data = await api("/api/stays", { ...spec.body, providers: [provider] });
    if (request !== state.staysRequest) return;
    mergeStays(data.stays);
    state.stayWarnings[provider] = data.warnings || [];
    if (provider === "booking" && state.stayWarnings.booking.some((w) => BOOKING_DOWN.test(w))) {
      state.bookingFailed = true;
    }
    spec.afterMerge();
  } catch (err) {
    if (request === state.staysRequest) {
      state.stayWarnings[provider] = [err.message];
      if (provider === "booking") state.bookingFailed = true;
    }
  } finally {
    stopLoading(key, spin);
    if (request === state.staysRequest) {
      showStayNotices(spec);
      if (provider === "booking") $("retry-booking").hidden = !state.bookingFailed;
    }
  }
}
function startStays(spec) {
  const request = ++state.staysRequest;
  clearStays();
  state.staySpec = spec;
  $("stay-note").textContent = spec.note;
  return Promise.all(STAY_PROVIDERS.map((p) => requestStays(p, request, spec, `stay:${p}`)));
}
async function retryBooking() {
  const spec = state.staySpec;
  if (!spec) return;
  state.bookingFailed = false;
  $("retry-booking").hidden = true;
  delete state.stayWarnings.booking;
  showStayNotices(spec);
  await requestStays("booking", state.staysRequest, spec, "stay:retry");
}
function loadAreaStays(area) {
  return startStays({
    body: { ...dates(), area, dog: $("dog").checked, guests: guests(), filters: state.filters?.stay },
    note: "in drawn area",
    baseWarnings: () => state.hikeWarnings,
    afterMerge: fitAll,
  });
}
function selectHike(id) {
  for (const li of document.querySelectorAll("#hike-list li.selected")) li.classList.remove("selected");
  state.items.get(id)?.classList.add("selected");
  const hike = state.hikes.get(id);
  return startStays({
    body: { ...dates(), hike_id: id, dog: $("dog").checked, guests: guests(), filters: state.filters?.stay },
    note: `near ${hike ? hike.name : "selected hike"}`,
    baseWarnings: () => [],
    afterMerge: () => {
      const layers = [state.markers.get(id), ...stayLayer.getLayers()].filter(Boolean);
      if (layers.length > 1) map.fitBounds(L.featureGroup(layers).getBounds().pad(0.2));
      else if (hike) map.setView([hike.lat, hike.lon], 12);
    },
  });
}

// ---- go to place (navigation only: no hike or stay search) ----
const placeLayer = L.layerGroup().addTo(map);  // not part of the hike/stay layers, survives clearAll
async function gotoPlace() {
  const text = $("goto-input").value.trim();
  if (!text) return;
  const spin = startLoading("goto");
  try {
    const data = await api(`/api/locate?q=${encodeURIComponent(text)}`);
    if (data.warnings?.length) notices(data.warnings);
    const place = data.place;
    if (!place) return;
    placeLayer.clearLayers();
    const popup = document.createElement("div");
    popup.textContent = place.name;
    L.marker([place.lat, place.lon], { icon: pin("📍", "goto") }).bindPopup(popup).addTo(placeLayer);
    const b = place.bbox;
    if (b && b.south < b.north && b.west < b.east) {
      map.flyToBounds([[b.south, b.west], [b.north, b.east]], { maxZoom: 15 });
    } else {
      map.setView([place.lat, place.lon], 12);
    }
  } finally {
    stopLoading("goto", spin);
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
    const km = c.stay.distance_km != null ? ` (${c.stay.distance_km} km)` : "";
    const meta = b ? `hike ${b.hike} · stay ${b.stay} · near ${b.proximity}${km} · price ${b.price}` : (c.note || "");
    const li = listItem($("combo-list"), `combo:${i}`, `${i + 1}. ${c.hike.name} + ${c.stay.name}`,
      `${c.score != null ? c.score + "/100 · " : ""}${meta}`, c.stay.badge,
      () => map.fitBounds(L.latLngBounds([[c.hike.lat, c.hike.lon], [c.stay.lat, c.stay.lon]]).pad(0.3)));
    li.title = meta;
  });
  $("combo-count").textContent = `(${combos.length})`;
  $("stay-count").textContent = `(${state.stays.size})`;
  $("hike-count").textContent = `(${state.hikes.size})`;
  $("combo-section").hidden = combos.length === 0;
  if (combos.length > 0) state.comboView = true;
}
async function planTrip() {
  // WHERE comes from the mode radios (near home / place / drawn area), exactly as for Search hikes.
  const body = { ...dates(), mode: mode(), drive_limit: driveLimit(), dog: $("dog").checked, guests: guests(),
    hike_filters: state.filters?.hike, stay_filters: state.filters?.stay };
  if (body.mode === "place") body.place = $("place").value;
  if (body.mode === "drawn") {
    const area = drawnArea();
    const problem = areaProblem(area);
    if (problem) { notices([problem]); return; }
    body.area = area;
  }
  const section = $("combo-section");
  section.hidden = false;  // the spinner lives in this section
  const spin = startLoading("combos");
  let data;
  try {
    data = await api("/api/trips", body);
  } catch (err) {
    section.hidden = $("combo-list").children.length === 0;
    throw err;
  } finally {
    stopLoading("combos", spin);
  }
  clearAll();
  notices(data.warnings);
  renderCombos(data.combos);
  if (!data.combos.length && !data.warnings.length) notices(["No trip picks found – try more days or turn off the dog filter."]);
  fitAll();
}
async function ask(event) {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (question.length < 3) return;
  const started = Date.now();
  const spin = startLoading("ask");
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
      stopLoading("ask", spin);
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
  $("draw-rect-btn").addEventListener("click", () => startDrawing("rectangle"));
  $("draw-circle-btn").addEventListener("click", () => startDrawing("circle"));
  $("search").addEventListener("click", () => withBusy($("search"), searchHikes));
  $("suggest-trips").addEventListener("click", () => withBusy($("suggest-trips"), planTrip));
  $("load-more").addEventListener("click", () => withBusy($("load-more"), loadMoreHikes));
  $("ask-form").addEventListener("submit", ask);
  $("retry-booking").addEventListener("click", retryBooking);
  $("goto-btn").addEventListener("click", () => withBusy($("goto-btn"), gotoPlace));
  $("goto-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !$("goto-btn").disabled) { event.preventDefault(); $("goto-btn").click(); }
  });
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
    state.catalogue = await api("/api/filters");
    state.filters = loadFilters(state.catalogue, state.config.default_guests);
    setupFilterDialogs();
  } catch (err) {
    notices([err.message]);
  }
}
init();
