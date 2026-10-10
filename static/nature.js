"use strict";
// 🌿 Nature layer: OpenStreetMap sights (GET /api/nature) for the visible map, plus the "trip sights" group
// that 🗺️ Show on map fills. Loaded after app.js (uses map, pin, esc, safeUrl, api, notices, state,
// startLoading/stopLoading/releaseLoading) and before trips.js (tripButtonHtml is called only at render time).
// Neither group is part of the hike/stay layers: clearAll()/clearHikes() never touch the nature layer;
// clearAll() empties only the trip sights (via clearTripSights).

// Mirrors app.models.SIGHT_LABELS / SIGHT_EMOJI.
const SIGHT_LABELS = {
  waterfall: "Waterfall", spring: "Spring", hot_spring: "Hot spring", lake: "Lake", river: "River",
  peak: "Peak", gorge: "Gorge", cave: "Cave", arch: "Arch", rock: "Rock", viewpoint: "Viewpoint",
  beach: "Beach", protected_area: "Protected area", forest: "Forest",
};
const SIGHT_EMOJI = {
  waterfall: "💧", spring: "🚰", hot_spring: "♨️", lake: "🏞️", river: "🌊", peak: "⛰️", gorge: "🏔️",
  cave: "🕳️", arch: "🪨", rock: "🪨", viewpoint: "👁️", beach: "🏖️", protected_area: "🌲", forest: "🌳",
};
const NATURE_GROUPS = ["water", "landforms", "views", "protected"];
const NATURE_KEY = "hick-toors:nature";
const NATURE_GROUPS_KEY = "hick-toors:nature-groups";
const NATURE_MIN_ZOOM = 9;  // sanity floor only: the tile count below decides
const NATURE_TILE_DEG = 0.25;  // mirrors app/sources/nature.py TILE_DEG / MAX_TILES
const NATURE_MAX_TILES = 16;
const NATURE_DEBOUNCE_MS = 600;
const NATURE_ZOOM_IN = "Zoom in to see nature sights";
const NATURE_ATTRIBUTION = 'Nature sights © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors (ODbL)';
const RIVER_STYLE = { color: "#3b7dd8", weight: 2 };
const AREA_STYLE = { color: "#2f6f4e", weight: 1.5, dashArray: "5 5", fill: false };
const OTHER_LINE_STYLE = { color: "#3b7dd8", weight: 1 };

state.sights = new Map();  // id -> sight (without lines), for the ＋ Trip lookup
// rendered: id -> { sight, layers } for the nature layer, updated incrementally so open popups survive refreshes.
const nature = { on: false, groups: new Set(NATURE_GROUPS), request: 0, timer: null, lastWarning: "", drawing: false,
  rendered: new Map(), tripShown: new Map(), layer: L.layerGroup().addTo(map), tripLayer: L.layerGroup().addTo(map),
  canvas: L.canvas({ padding: 0.5 }) };  // one shared canvas for all river/area lines

function readStored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function writeStored(key, value) { try { localStorage.setItem(key, value); } catch { /* storage blocked */ } }

function sightLabel(s) {
  const label = SIGHT_LABELS[s.kind] || "Sight";
  return s.elevation_m != null ? `${label} · ${s.elevation_m} m` : label;
}
function sightPopup(s) {
  const links = [];
  if (safeUrl(s.url) !== "#") links.push(`<a href="${esc(s.url)}" target="_blank" rel="noopener">↗ OpenStreetMap</a>`);
  if (safeUrl(s.wikipedia) !== "#") links.push(`<a href="${esc(s.wikipedia)}" target="_blank" rel="noopener">↗ Wikipedia</a>`);
  return `<div class="popup sight-popup"><strong>${esc(s.name)}</strong><br>
    <span class="sight-kind">${esc(SIGHT_EMOJI[s.kind] || "🌿")} ${esc(sightLabel(s))}</span><br>
    ${links.length ? `<span class="sight-links">${links.join("")}</span><br>` : ""}${tripButtonHtml("sight", s.id)}
    <span class="osm-credit">© OpenStreetMap contributors</span></div>`;
}
function lineStyle(kind) {
  if (kind === "river") return RIVER_STYLE;
  if (kind === "protected_area" || kind === "forest") return AREA_STYLE;
  return OTHER_LINE_STYLE;
}
function plainSight(s) {
  const { lines, ...rest } = s;  // lines are never stored in trips
  return { ...rest, lines: [] };
}
// state.sights holds whatever is on the map now: the trip sights plus the current nature render.
function syncSightLookup() {
  state.sights.clear();
  for (const [id, s] of nature.tripShown) state.sights.set(id, s);
  for (const [id, entry] of nature.rendered) state.sights.set(id, entry.sight);
}
// One lazily built popup per sight, shared by its pin and its lines.
function sightPopupFor(s) { return L.popup().setContent(() => sightPopup(s)); }
function sightMarker(s, extraClass, popup) {
  return L.marker([s.lat, s.lon], { icon: pin(SIGHT_EMOJI[s.kind] || "🌿", `sight${extraClass ? " " + extraClass : ""}`),
    title: s.name, keyboard: true }).bindPopup(popup || sightPopupFor(s));
}

// ---- nature layer ----
function clearNatureLayer() {
  nature.layer.clearLayers();
  nature.rendered.clear();
  syncSightLookup();
}
function addSightLayers(s) {
  const popup = sightPopupFor(s);
  const layers = [];
  for (const line of s.lines || []) {
    if (Array.isArray(line) && line.length >= 2) {
      // Lines ignore the mouse while a draw tool is active (canvas hit-testing reads options.interactive).
      layers.push(L.polyline(line, { ...lineStyle(s.kind), renderer: nature.canvas, interactive: !nature.drawing })
        .bindPopup(popup));
    }
  }
  // A sight already pinned as a trip sight keeps only that (distinct) pin; its lines are still drawn.
  if (!nature.tripShown.has(s.id)) layers.push(sightMarker(s, "", popup));
  for (const layer of layers) nature.layer.addLayer(layer);
  return layers;
}
function renderedEntry(s) {
  return { sight: plainSight(s), full: s, pinned: !nature.tripShown.has(s.id), layers: addSightLayers(s) };
}
// After the trip sights change: redraw nature sights whose "also a trip sight" state flipped.
function refreshTripOverlap() {
  for (const [id, entry] of nature.rendered) {
    if (entry.pinned === !nature.tripShown.has(id)) continue;
    for (const layer of entry.layers) nature.layer.removeLayer(layer);
    nature.rendered.set(id, renderedEntry(entry.full));
  }
}
// Incremental: sights already on the map keep their pin, lines and any open popup; gone ones are removed.
function renderSights(sights) {
  const wanted = new Map();
  for (const s of sights) {
    if (Number.isFinite(s.lat) && Number.isFinite(s.lon) && !wanted.has(s.id)) wanted.set(s.id, s);
  }
  for (const [id, entry] of nature.rendered) {
    if (wanted.has(id)) continue;
    for (const layer of entry.layers) nature.layer.removeLayer(layer);
    nature.rendered.delete(id);
  }
  for (const [id, s] of wanted) {
    if (!nature.rendered.has(id)) nature.rendered.set(id, renderedEntry(s));
  }
  syncSightLookup();
}
function setLinesInteractive(on) {
  nature.drawing = !on;
  for (const entry of nature.rendered.values()) {
    for (const layer of entry.layers) if (layer instanceof L.Polyline) layer.options.interactive = on;
  }
}
function showNatureWarnings(warnings) {
  const text = (warnings || []).join("\n");
  if (!text) { nature.lastWarning = ""; return; }
  if (text === nature.lastWarning) return;  // panning must not repeat the same notice
  nature.lastWarning = text;
  notices(warnings);
}
// Same grid math as app/sources/nature.py tiles_for (incl. its float tolerance), on the numbers we send.
function tileIndex(value, up) {
  const x = value / NATURE_TILE_DEG;
  return up ? Math.ceil(x - 1e-9) : Math.floor(x + 1e-9);
}
function tileCount(box) {
  const s0 = tileIndex(box.south, false), s1 = Math.max(tileIndex(box.north, true), s0 + 1);
  const w0 = tileIndex(box.west, false), w1 = Math.max(tileIndex(box.east, true), w0 + 1);
  return (s1 - s0) * (w1 - w0);
}
function boxOf(bounds) {
  const fix = (v, lo, hi) => Number(Math.min(hi, Math.max(lo, v)).toFixed(5));
  return { south: fix(bounds.getSouth(), -90, 90), west: fix(bounds.getWest(), -180, 180),
    north: fix(bounds.getNorth(), -90, 90), east: fix(bounds.getEast(), -180, 180) };
}
// The box to request: the view padded by 10 % if that fits the server's tile limit, else the bare view,
// else null (too wide: "Zoom in"). This one function decides both the hint and whether a request is made.
function natureBox() {
  if (map.getZoom() < NATURE_MIN_ZOOM) return null;
  const view = map.getBounds();
  const padded = boxOf(view.pad(0.1));
  if (tileCount(padded) <= NATURE_MAX_TILES) return padded;
  const bare = boxOf(view);
  return tileCount(bare) <= NATURE_MAX_TILES ? bare : null;
}
function paintNatureHint() {
  $("nature-hint").textContent = NATURE_ZOOM_IN;
  $("nature-hint").hidden = !nature.on || natureBox() !== null;
}
function cancelNatureRequest() {
  nature.request += 1;  // an answer still on its way is ignored
  releaseLoading("nature");
}
async function loadNature() {
  nature.timer = null;
  if (!nature.on) return;
  const box = natureBox();
  paintNatureHint();
  const groups = NATURE_GROUPS.filter((g) => nature.groups.has(g));
  if (!box || !groups.length) {
    cancelNatureRequest();
    clearNatureLayer();
    return;
  }
  const params = new URLSearchParams({ south: String(box.south), west: String(box.west),
    north: String(box.north), east: String(box.east), groups: groups.join(",") });
  const id = ++nature.request;
  const spin = startLoading("nature");
  let data;
  try {
    data = await api(`/api/nature?${params}`);
  } catch (err) {
    if (id !== nature.request) return;
    if (err.message === NATURE_ZOOM_IN) {  // fallback: the server counted more tiles than we did
      clearNatureLayer();
      $("nature-hint").hidden = false;
    } else {
      showNatureWarnings([err.message]);
    }
    return;
  } finally {
    stopLoading("nature", spin);
  }
  if (id !== nature.request || !nature.on) return;  // a newer request (or toggle off) replaced this one
  renderSights(data.sights || []);
  showNatureWarnings(data.warnings);
}
function scheduleNature() {
  clearTimeout(nature.timer);
  nature.timer = setTimeout(() => { loadNature().catch((err) => notices([err.message])); }, NATURE_DEBOUNCE_MS);
}
function paintNatureControls() {
  $("nature-btn").classList.toggle("active", nature.on);
  $("nature-btn").setAttribute("aria-pressed", String(nature.on));
  $("nature-chips").hidden = !nature.on;
  for (const chip of document.querySelectorAll(".nature-chip")) {
    chip.setAttribute("aria-pressed", String(nature.groups.has(chip.dataset.group)));
  }
}
function setNature(on) {
  if (on === nature.on) return;
  nature.on = on;
  writeStored(NATURE_KEY, on ? "1" : "0");
  paintNatureControls();
  paintNatureHint();
  if (on) {
    map.attributionControl?.addAttribution(NATURE_ATTRIBUTION);
    scheduleNature();
  } else {
    map.attributionControl?.removeAttribution(NATURE_ATTRIBUTION);
    clearTimeout(nature.timer);
    nature.timer = null;
    cancelNatureRequest();
    clearNatureLayer();
    nature.lastWarning = "";
  }
}
function toggleGroup(group) {
  if (nature.groups.has(group)) nature.groups.delete(group); else nature.groups.add(group);
  writeStored(NATURE_GROUPS_KEY, JSON.stringify(NATURE_GROUPS.filter((g) => nature.groups.has(g))));
  paintNatureControls();
  scheduleNature();
}

// ---- trip sights (🗺️ Show on map) ----
function clearTripSights() {
  nature.tripLayer.clearLayers();
  nature.tripShown.clear();
  refreshTripOverlap();
  syncSightLookup();
}
// Pins a saved trip's sights; returns the markers so the caller can fit the map to them.
function showTripSights(sights) {
  clearTripSights();
  const markers = [];
  for (const s of sights || []) {
    if (!Number.isFinite(s.lat) || !Number.isFinite(s.lon)) continue;
    nature.tripShown.set(s.id, plainSight(s));
    const marker = sightMarker(s, "trip-sight").addTo(nature.tripLayer);
    markers.push(marker);
  }
  refreshTripOverlap();
  syncSightLookup();
  return markers;
}

// ---- init ----
(function initNature() {
  try {
    const stored = JSON.parse(readStored(NATURE_GROUPS_KEY));
    if (Array.isArray(stored)) nature.groups = new Set(NATURE_GROUPS.filter((g) => stored.includes(g)));
  } catch { /* corrupt or missing: all groups on */ }
  paintNatureControls();
  $("nature-btn").addEventListener("click", () => setNature(!nature.on));
  for (const chip of document.querySelectorAll(".nature-chip")) {
    chip.addEventListener("click", () => toggleGroup(chip.dataset.group));
  }
  map.on("moveend zoomend", () => {
    if (!nature.on) return;
    paintNatureHint();
    scheduleNature();
  });
  map.on(L.Draw.Event.DRAWSTART, () => setLinesInteractive(false));
  map.on(L.Draw.Event.DRAWSTOP, () => setLinesInteractive(true));
  if (readStored(NATURE_KEY) === "1") setNature(true);
})();
