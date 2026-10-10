"use strict";
// Filter values: sanitising, counting, validation and browser storage. No DOM here, so
// tests/ui/filters_logic.test.js can run it under node. `section` is one half of
// GET /api/filters: {fields: [...], defaults: {...}}.

const FILTERS_STORAGE_KEY = "hicktoors.filters.v1";

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}
function optionValues(field) {
  return (field.options || []).map((o) => o.value);
}
function acceptsNumber(value, field, allowNull) {
  if (value === null) return allowNull;
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  if (field.options) return optionValues(field).includes(value);
  if (field.min != null && value < field.min) return false;
  if (field.max != null && value > field.max) return false;
  return true;
}

// Any stored/raw object -> a complete, valid values object; anything unusable falls back to defaults.
function sanitizeSection(raw, section) {
  const out = cloneJson(section.defaults);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const field of section.fields) {
    if (field.type === "multi") {
      const value = raw[field.key];
      if (!Array.isArray(value)) continue;
      const allowed = optionValues(field);
      const kept = [...new Set(value.filter((v) => allowed.includes(v)))];
      if (kept.length || out[field.key].length === 0) out[field.key] = kept;
    } else if (field.type === "range") {
      const lo = field.min_key in raw && acceptsNumber(raw[field.min_key], field, out[field.min_key] === null)
        ? raw[field.min_key] : out[field.min_key];
      const hi = field.max_key in raw && acceptsNumber(raw[field.max_key], field, out[field.max_key] === null)
        ? raw[field.max_key] : out[field.max_key];
      if (lo === null || hi === null || lo <= hi) {
        out[field.min_key] = lo;
        out[field.max_key] = hi;
      }
    } else if (field.type === "number") {
      if (field.key in raw && acceptsNumber(raw[field.key], field, true)) out[field.key] = raw[field.key];
    } else if (field.type === "select") {
      if (field.key in raw && optionValues(field).includes(raw[field.key])) out[field.key] = raw[field.key];
    } else if (field.type === "bool") {
      if (typeof raw[field.key] === "boolean") out[field.key] = raw[field.key];
    }
  }
  return out;
}

function sanitizeFilters(raw, catalogue, defaultGuests) {
  const obj = raw && typeof raw === "object" ? raw : {};
  const guests = Number.isInteger(obj.guests) && obj.guests >= 1 && obj.guests <= 16 ? obj.guests : defaultGuests;
  return {
    hike: sanitizeSection(obj.hike, catalogue.hike),
    stay: sanitizeSection(obj.stay, catalogue.stay),
    guests,
  };
}

function sameValue(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) {
    return JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  }
  return a === b;
}

// One per popup field that differs from its default (a range counts once).
function countActive(values, section) {
  let n = 0;
  for (const field of section.fields) {
    const keys = field.type === "range" ? [field.min_key, field.max_key] : [field.key];
    if (keys.some((k) => !sameValue(values[k], section.defaults[k]))) n += 1;
  }
  return n;
}

// Keys of range fields whose minimum is above their maximum.
function invalidRanges(values, section) {
  return section.fields
    .filter((f) => f.type === "range" && values[f.min_key] !== null && values[f.max_key] !== null
      && values[f.min_key] > values[f.max_key])
    .map((f) => f.key);
}

// Storage lives behind these two functions so a backend store can replace them later.
function loadFilters(catalogue, defaultGuests) {
  let raw = null;
  try {
    raw = JSON.parse(localStorage.getItem(FILTERS_STORAGE_KEY));
  } catch (err) {
    raw = null;
  }
  return sanitizeFilters(raw, catalogue, defaultGuests);
}
function saveFilters(filters) {
  try {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(filters));
  } catch (err) {
    // storage full or disabled: filters still apply for this page session
  }
}

if (typeof module !== "undefined") {
  module.exports = { sanitizeFilters, sanitizeSection, countActive, invalidRanges, loadFilters, saveFilters };
}
