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
const NATURE_MIN_ZOOM = 10;
const NATURE_DEBOUNCE_MS = 600;
const NATURE_ZOOM_IN = "Zoom in to see nature sights";
const NATURE_ATTRIBUTION = 'Nature sights © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors (ODbL)';
const RIVER_STYLE = { color: "#3b7dd8", weight: 2 };
const AREA_STYLE = { color: "#2f6f4e", weight: 1.5, dashArray: "5 5", fill: false };
const OTHER_LINE_STYLE = { color: "#3b7dd8", weight: 1 };

state.sights = new Map();  // id -> sight (without lines), for the ＋ Trip lookup
const nature = { on: false, groups: new Set(NATURE_GROUPS), request: 0, timer: null, lastWarning: "",
  shown: new Map(), tripShown: new Map(), layer: L.layerGroup().addTo(map), tripLayer: L.layerGroup().addTo(map) };

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
  for (const [id, s] of nature.shown) state.sights.set(id, s);
}
function sightMarker(s, extraClass) {
  return L.marker([s.lat, s.lon], { icon: pin(SIGHT_EMOJI[s.kind] || "🌿", `sight${extraClass ? " " + extraClass : ""}`),
    title: s.name, keyboard: true }).bindPopup(sightPopup(s));
}

// ---- nature layer ----
function clearNatureLayer() {
  nature.layer.clearLayers();
  nature.shown.clear();
  syncSightLookup();
}
function renderSights(sights) {
  clearNatureLayer();
  for (const s of sights) {
    if (!Number.isFinite(s.lat) || !Number.isFinite(s.lon)) continue;
    nature.shown.set(s.id, plainSight(s));
    for (const line of s.lines || []) {
      if (Array.isArray(line) && line.length >= 2) {
        L.polyline(line, lineStyle(s.kind)).bindPopup(sightPopup(s)).addTo(nature.layer);
      }
    }
    sightMarker(s).addTo(nature.layer);
  }
  syncSightLookup();
}
function showNatureWarnings(warnings) {
  const text = (warnings || []).join("\n");
  if (!text) { nature.lastWarning = ""; return; }
  if (text === nature.lastWarning) return;  // panning must not repeat the same notice
  nature.lastWarning = text;
  notices(warnings);
}
function natureBox() {
  const b = map.getBounds().pad(0.1);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  return { south: clamp(b.getSouth(), -90, 90), west: clamp(b.getWest(), -180, 180),
    north: clamp(b.getNorth(), -90, 90), east: clamp(b.getEast(), -180, 180) };
}
function paintNatureHint() {
  $("nature-hint").textContent = NATURE_ZOOM_IN;
  $("nature-hint").hidden = !nature.on || map.getZoom() >= NATURE_MIN_ZOOM;
}
function cancelNatureRequest() {
  nature.request += 1;  // an answer still on its way is ignored
  releaseLoading("nature");
}
async function loadNature() {
  nature.timer = null;
  if (!nature.on) return;
  const zoomedIn = map.getZoom() >= NATURE_MIN_ZOOM;
  paintNatureHint();
  const groups = NATURE_GROUPS.filter((g) => nature.groups.has(g));
  if (!zoomedIn || !groups.length) {
    cancelNatureRequest();
    clearNatureLayer();
    return;
  }
  const box = natureBox();
  const params = new URLSearchParams({ south: box.south.toFixed(5), west: box.west.toFixed(5),
    north: box.north.toFixed(5), east: box.east.toFixed(5), groups: groups.join(",") });
  const id = ++nature.request;
  const spin = startLoading("nature");
  let data;
  try {
    data = await api(`/api/nature?${params}`);
  } catch (err) {
    if (id !== nature.request) return;
    if (err.message === NATURE_ZOOM_IN) {  // the visible area spans too many tiles
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
  if (readStored(NATURE_KEY) === "1") setNature(true);
})();
