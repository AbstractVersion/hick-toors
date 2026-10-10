// Run: node tests/ui/filters_logic.test.js   (plain node, no packages; not collected by pytest)
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const { sanitizeFilters, countActive, invalidRanges, loadFilters } = require(
  path.join(__dirname, "..", "..", "static", "filters.js"));

// Same shapes as GET /api/filters (trimmed to one field of each type).
const catalogue = {
  hike: {
    defaults: { activities: [1], difficulty_min: 1, difficulty_max: 5, min_trailrank: null, sort: "popular" },
    fields: [
      { key: "activities", type: "multi", options: [{ value: 1, label: "Hiking" }, { value: 43, label: "Walking" }] },
      { key: "difficulty", type: "range", min_key: "difficulty_min", max_key: "difficulty_max",
        options: [1, 2, 3, 4, 5].map((v) => ({ value: v, label: String(v) })) },
      { key: "min_trailrank", type: "number", min: 0, max: 100, step: 1 },
      { key: "sort", type: "select", options: [{ value: "popular", label: "p" }, { value: "top_rated", label: "t" }] },
    ],
  },
  stay: {
    defaults: { price_min: null, price_max: null, min_rating: null, entire_place: false, amenities: [] },
    fields: [
      { key: "price", type: "range", min_key: "price_min", max_key: "price_max", min: 0, max: 2000, step: 1 },
      { key: "min_rating", type: "select", options: [{ value: null, label: "Any" }, { value: 4.5, label: "4.5+" }] },
      { key: "entire_place", type: "bool" },
      { key: "amenities", type: "multi", options: [{ value: "wifi", label: "Wifi" }, { value: "fireplace", label: "F" }] },
    ],
  },
};
const defaults = sanitizeFilters(null, catalogue, 2);

// missing / corrupt storage -> defaults
for (const raw of [null, undefined, "garbage", 42, [], { hike: "x", stay: [1, 2], guests: "many" }]) {
  assert.deepEqual(sanitizeFilters(raw, catalogue, 2), defaults, `raw=${JSON.stringify(raw)}`);
}
assert.deepEqual(defaults, { hike: catalogue.hike.defaults, stay: catalogue.stay.defaults, guests: 2 });

// old/partial shapes: unknown keys dropped, bad values replaced field by field, good ones kept
const old = sanitizeFilters({
  hike: { activities: [43, 999, 43], difficulty_min: null, difficulty_max: 3, min_rank: 50, min_trailrank: 101,
    sort: "newest" },
  stay: { price_min: 300, price_max: 100, min_rating: 4.5, entire_place: "yes", amenities: ["sauna", "wifi"] },
  guests: 4,
}, catalogue, 2);
assert.deepEqual(old.hike, { activities: [43], difficulty_min: 1, difficulty_max: 3, min_trailrank: null, sort: "popular" });
assert.deepEqual(old.stay, { price_min: null, price_max: null, min_rating: 4.5, entire_place: false, amenities: ["wifi"] });
assert.equal(old.guests, 4);
assert.deepEqual(sanitizeFilters({ hike: { activities: [] } }, catalogue, 2).hike.activities, [1]);

// fractional values for integer fields fall back to the default; decimal-step fields keep fractions
const cat2 = JSON.parse(JSON.stringify(catalogue));
cat2.hike.defaults.elevation_min_m = null;
cat2.hike.fields.push({ key: "elevation_min_m", type: "number", min: 0, max: 5000 });
cat2.hike.defaults.distance = null;
cat2.hike.fields.push({ key: "distance", type: "number", min: 0, max: 100, step: 0.1 });
const frac = sanitizeFilters({ hike: { elevation_min_m: 10.5, distance: 2.5, min_trailrank: 50.5 },
  stay: { price_min: 10.5, price_max: 100 } }, cat2, 2);
assert.equal(frac.hike.elevation_min_m, null);
assert.equal(frac.hike.distance, 2.5);
assert.equal(frac.hike.min_trailrank, null);
assert.equal(frac.stay.price_min, null);
assert.equal(frac.stay.price_max, 100);

// a disabled bool (loop_only without login) is forced to its default
const cat3 = JSON.parse(JSON.stringify(catalogue));
cat3.hike.defaults.loop_only = false;
cat3.hike.fields.push({ key: "loop_only", type: "bool", disabled: true });
assert.equal(sanitizeFilters({ hike: { loop_only: true } }, cat3, 2).hike.loop_only, false);
cat3.hike.fields[cat3.hike.fields.length - 1].disabled = false;
assert.equal(sanitizeFilters({ hike: { loop_only: true } }, cat3, 2).hike.loop_only, true);

// badge counts: one per field, a range counts once, list order ignored
assert.equal(countActive(defaults.hike, catalogue.hike), 0);
assert.equal(countActive({ ...defaults.stay, price_min: 50, price_max: 120, amenities: ["wifi"] }, catalogue.stay), 2);
assert.equal(countActive({ ...defaults.hike, activities: [1] }, catalogue.hike), 0);
assert.equal(countActive({ ...defaults.hike, activities: [43, 1] }, catalogue.hike), 1);

// min > max detection
assert.deepEqual(invalidRanges({ ...defaults.stay, price_min: 200, price_max: 100 }, catalogue.stay), ["price"]);
assert.deepEqual(invalidRanges({ ...defaults.stay, price_min: 200 }, catalogue.stay), []);

// localStorage that throws (private mode) -> defaults, no exception
global.localStorage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
assert.deepEqual(loadFilters(catalogue, 2), defaults);
global.localStorage = { getItem: () => "{not json" };
assert.deepEqual(loadFilters(catalogue, 2), defaults);

console.log("filters_logic: all assertions passed");
