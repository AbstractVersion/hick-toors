"use strict";
// 🧭 The guided round (#guide): a share link (/?round=<id>), the Join screen, one trip per step and your summary.
// Loaded after vote.js and before banner.js / mobile.js. Uses $, api, money, safeUrl, notices (app.js), auth
// (auth.js), tripPath / plural / showTripOnMap (trips.js), SIGHT_EMOJI / SIGHT_LABELS (nature.js), markAutoOpened
// (banner.js, at run time) and from vote.js: the round state (vote.round, applyRound, refreshVote, write, nextSeq),
// castVote (the one-😍 rules and the "Moved your Love" toast), showVoteError, vEl / vButton, enc, closesIn / localWhen,
// openVoteOverview, RATINGS, myVote.
// The guide shows the current open round (there is only ever one): every answer vote.js applies fires
// "round-changed", which updates the open step in place (buttons, comment, dots) – the step itself is only rebuilt
// when you move, so a poll never moves anything under a finger or resets a comment being typed.
// Trips come in the round's fixed order (position), the same for everyone. Every name, note, comment and title is user
// input: textContent only.
const guide = {
  roundId: null,
  screen: null,          // "join" | "trip" | "summary" | "closed" | null (closed dialog)
  tripId: null,          // the trip step shown
  joinRound: null,       // the RoundView the Join screen shows
  trips: new Map(),      // trip id → saved trip (details for the step; reloaded each time the guide opens)
  loads: new Map(),      // trip id → its pending load
  showStays: new Set(),  // trips rated 🚫 whose stays were opened anyway
  view: null,            // the open trip step's live controls, updated in place
  pollTimer: null,
  pressing: false, pressTimer: null,  // a press on a rating button: the comment's blur must not save the old rating
  back: null,            // {roundId, tripId}: where ← Back to voting returns to
  link: null,            // a ?round= id still to handle (after log in)
  linkBusy: false,
};
const GUIDE_HOW = [
  "Rate every trip: 😍 love it (one per round), 🙂 OK, 🚫 no-go",
  "A 🚫 counts a lot – use it when you really can't go",
  "The top trip wins when the round closes",
];
const RATING_SHORT = { love: "😍 Love it", ok: "🙂 OK", nogo: "🚫 No-go" };

const guideOrder = (round) => [...round.trips].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
const guideRound = () => (vote.round && vote.round.id === guide.roundId ? vote.round : null);
const guideOpen = () => $("guide").open;
function firstUnrated(round) { return guideOrder(round).find((t) => !t.my_rating) || null; }

// ---- opening / closing ----
function startGuide() {
  const dlg = $("guide");
  if (!dlg.open) {
    guide.trips.clear();  // fresh trip details for this visit (a trip may have been edited meanwhile)
    hidePill();
    dlg.showModal();
  }
  clearInterval(guide.pollTimer);
  guide.pollTimer = setInterval(refreshVote, VOTE_POLL_MS);
  showVoteError("");
}
// The Join screen for an open round you aren't part of yet.
function openJoin(round) {
  if (!round || round.status !== "open") return openVoteOverview(round?.id);
  if (round.me?.joined) return openGuide(round);
  guide.roundId = round.id;
  guide.joinRound = round;
  startGuide();
  showScreen("join");
}
// The guide at tripId, the summary (summary: true), or where you left off: the first trip you haven't rated yet (the
// summary when every trip is rated).
function openGuide(round, { tripId = null, summary = false } = {}) {
  if (!round || round.status !== "open") return openVoteOverview(round?.id);
  if (round.me && !round.me.joined) return openJoin(round);
  guide.roundId = round.id;
  startGuide();
  const target = tripId && round.trips.some((t) => t.trip_id === tripId) ? tripId : firstUnrated(round)?.trip_id;
  if (summary || !target) showScreen("summary");
  else goToStep(target);
}
function closeGuide() { $("guide").close(); }
function onGuideClose() {
  clearInterval(guide.pollTimer);
  guide.pollTimer = null;
  guide.screen = null;
  guide.view = null;
  clearTimeout(guide.pressTimer);
  guide.pressing = false;
  window.mobile?.dialogClosed();
}

// ---- screens ----
function setTitle(text) { $("guide-title").textContent = text; }
function showScreen(screen) {
  guide.screen = screen;
  guide.view = null;
  if (screen === "join") renderJoin();
  else if (screen === "summary") renderSummary();
  else if (screen === "closed") renderClosed();
  scrollGuideTop();
}
function scrollGuideTop() {
  $("guide").scrollTop = 0;
  $("guide-body").scrollTop = 0;
}
function goToStep(tripId) {
  guide.tripId = tripId;
  guide.screen = "trip";
  renderStep();
  scrollGuideTop();
  $("guide-body").querySelector(".g-trip-name")?.focus({ preventScroll: true });
}
function setNav({ back = null, next = null, nextLabel = "Next →", backLabel = "← Back" } = {}) {
  const nav = $("guide-nav");
  nav.hidden = !back && !next;
  $("guide-back").textContent = backLabel;
  $("guide-back").disabled = !back;
  $("guide-back").onclick = back;
  $("guide-next").textContent = nextLabel;
  $("guide-next").hidden = !next;
  $("guide-next").onclick = next;
}

// Join: title, who invites, deadline, who's in, how it works, Join and start / Not now.
function renderJoin() {
  const round = guide.joinRound;
  setTitle("🗳️ Join the vote");
  setNav();
  const body = $("guide-body");
  body.replaceChildren();
  const card = vEl("div", "g-join");
  card.append(vEl("div", "g-hero", "🗳️"), vEl("h3", "g-join-title", round.title));
  card.append(vEl("p", "g-join-by", `${auth.label(round.opener)} invites you to vote`));
  card.append(vEl("p", "g-join-when", `⏰ ${closesIn(round.closes_at)} · ${localWhen(round.closes_at)}`));
  const who = vEl("div", "g-who");
  const people = round.participants?.length ? round.participants : [round.opener];
  who.append(vEl("div", "g-label", `Who's in (${people.length})`));
  const chips = vEl("div", "g-chips");
  for (const name of people) chips.append(vEl("span", "g-chip", auth.label(name)));
  who.append(chips);
  card.append(who);
  card.append(vEl("div", "g-label", "How it works"));
  const how = vEl("ol", "g-how");
  for (const line of GUIDE_HOW) how.append(vEl("li", null, line));
  card.append(how);
  card.append(vEl("p", "g-trips-count", `${plural(round.trips.length, "trip")} to rate – it takes a few minutes.`));
  const actions = vEl("div", "g-join-actions");
  const join = vButton("Join and start", "primary g-big", () => joinRound(round.id));
  join.id = "guide-join";
  const later = vButton("Not now", "tool-btn g-big", closeGuide);
  later.id = "guide-later";
  actions.append(join, later);
  card.append(actions);
  body.append(card);
  join.focus({ preventScroll: true });
}
function joinRound(roundId) {
  showVoteError("");
  const button = $("guide-join");
  if (button) button.disabled = true;
  write(async () => {
    try {
      const view = await api(`/api/rounds/${enc(roundId)}/join`, {}, "POST");
      applyRound(view, nextSeq());
      if (guideOpen()) openGuide(view);
    } catch (err) {
      if (/closed/i.test(err.message)) {  // it closed meanwhile: the results instead
        closeGuide();
        openVoteOverview(roundId);
        notices([err.message]);
        return;
      }
      throw err;
    } finally {
      if (button) button.disabled = false;
    }
  });
}

function renderClosed() {
  setTitle("🗳️ Vote");
  setNav();
  const box = vEl("div", "g-join");
  box.append(vEl("div", "g-hero", "🏁"), vEl("h3", "g-join-title", "This round is closed"),
    vEl("p", "g-join-by", "Voting is over – see who voted what and which trip won."),
    vButton("📊 See the results", "primary g-big", () => { closeGuide(); openVoteOverview(guide.roundId); }));
  $("guide-body").replaceChildren(box);
}

// ---- progress: "Trip 2 of 5" + dots (rated = filled, current = ring; tap to jump) and 🏁 the summary ----
function progress(round, currentId) {
  const order = guideOrder(round);
  const index = order.findIndex((t) => t.trip_id === currentId);
  const wrap = vEl("div", "g-progress");
  const rated = order.filter((t) => t.my_rating).length;
  const label = vEl("div", "g-step-label");
  label.append(vEl("strong", null, currentId ? `Trip ${index + 1} of ${order.length}` : "Summary"),
    vEl("span", "g-step-rated", ` · ${rated} of ${order.length} rated`));
  const dots = vEl("div", "g-dots");
  dots.setAttribute("role", "group");
  dots.setAttribute("aria-label", "Trips");
  order.forEach((t, i) => {
    const dot = vButton("", "g-dot", () => goToStep(t.trip_id));
    dot.dataset.trip = t.trip_id;
    dot.append(vEl("span", "g-dot-mark"));
    dot.classList.toggle("rated", Boolean(t.my_rating));
    dot.classList.toggle("current", t.trip_id === currentId);
    if (t.trip_id === currentId) dot.setAttribute("aria-current", "step");
    dot.setAttribute("aria-label", `Trip ${i + 1}: ${t.name} – ${t.my_rating ? RATING_SHORT[t.my_rating] : "not rated"}`);
    dots.append(dot);
  });
  const end = vButton("🏁", "g-dot g-dot-end", () => showScreen("summary"));
  end.setAttribute("aria-label", "Your picks (summary)");
  if (!currentId) { end.classList.add("current"); end.setAttribute("aria-current", "step"); }
  dots.append(end);
  wrap.append(label, dots);
  return wrap;
}

// ---- a trip step ----
function renderStep() {
  const round = guideRound();
  if (!round || round.status !== "open") return showScreen("closed");
  const order = guideOrder(round);
  const index = order.findIndex((t) => t.trip_id === guide.tripId);
  if (index < 0) return openGuide(round);  // the trip left the round: where you left off
  const t = order[index];
  setTitle(`🗳️ ${round.title}`);
  const body = $("guide-body");
  body.replaceChildren();
  body.append(progress(round, t.trip_id));

  const card = vEl("section", "g-trip");
  const name = vEl("h3", "g-trip-name", t.name);
  name.tabIndex = -1;
  card.append(name);
  const details = vEl("div", "g-details");
  details.append(vEl("p", "list-note", "Loading the trip…"));
  card.append(details);
  const mapBtn = vButton("🗺️ See on map", "tool-btn g-map-btn", () => seeOnMap(t.trip_id));
  card.append(mapBtn);
  body.append(card);

  // Rate it: 😍 / 🙂 / 🚫 + a comment, saved at once.
  const rate = vEl("section", "g-rate");
  rate.append(vEl("h4", "g-section-title", "Your rating"));
  const buttons = vEl("div", "g-rate-buttons");
  const rateBtns = new Map();
  const comment = vEl("input", "g-comment");
  comment.type = "text";
  comment.maxLength = 140;
  comment.placeholder = "💬 Why? (optional)";
  comment.setAttribute("aria-label", `Comment: ${t.name}`);
  for (const [rating, emoji, label] of RATINGS) {
    const b = vButton("", "g-rate-btn", () => {
      guide.pressing = false;
      castVote({ tripId: t.trip_id }, rating, comment.disabled ? null : comment.value);
    });
    b.dataset.rating = rating;
    b.append(vEl("span", "g-rate-emoji", emoji), vEl("span", "g-rate-label", label));
    b.setAttribute("aria-label", `${label}: ${t.name}`);
    rateBtns.set(rating, b);
    buttons.append(b);
  }
  rate.append(buttons);
  const loveNote = vEl("p", "g-love-note");
  rate.append(loveNote, comment);
  body.append(rate);
  comment.addEventListener("blur", () => { if (!guide.pressing) saveComment(); });
  comment.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); comment.blur(); }
  });

  // Where would you sleep? The trip's stays (filled once the trip is loaded).
  const sleep = vEl("section", "g-sleep");
  sleep.hidden = true;
  body.append(sleep);

  guide.view = { tripId: t.trip_id, rateBtns, comment, loveNote, details, sleep, stayBtns: new Map(),
    staysList: null, staysCollapsed: null };
  updateStep();
  loadTrip(t.trip_id).then((trip) => {
    if (guide.view?.tripId !== t.trip_id || !guideOpen()) return;
    fillDetails(details, trip);
    buildStays(trip);
    updateStep();
  }).catch((err) => {
    if (guide.view?.tripId !== t.trip_id) return;
    details.replaceChildren(vEl("p", "list-note", `Couldn't load the trip – ${err.message}`));
  });
  const next = order[index + 1];
  if (next) loadTrip(next.trip_id).catch(() => {});  // the next step opens without a wait
}
function loadTrip(tripId) {
  if (guide.trips.has(tripId)) return Promise.resolve(guide.trips.get(tripId));
  if (!guide.loads.has(tripId)) {
    const load = api(tripPath(tripId)).then((trip) => {
      guide.trips.set(tripId, trip);
      return trip;
    }).finally(() => guide.loads.delete(tripId));
    guide.loads.set(tripId, load);
  }
  return guide.loads.get(tripId);
}

const NIGHT_MS = 86400000;
function nightsOf(trip) {
  if (!trip.start || !trip.end) return null;
  const n = Math.round((new Date(trip.end) - new Date(trip.start)) / NIGHT_MS);
  return n > 0 ? n : null;
}
function shortDate(iso) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}
// ≈ walking time (Naismith: 5 km/h plus an hour per 600 m of ascent); Wikiloc gives no duration.
function hikeTime(h) {
  if (h.distance_km == null) return null;
  const minutes = Math.round(((h.distance_km / 5) + (h.elevation_gain_m || 0) / 600) * 60 / 15) * 15;
  if (minutes < 15) return null;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return `≈ ${hours ? `${hours} h` : ""}${hours && rest ? " " : ""}${rest ? `${rest} min` : ""}`;
}
function outLink(url, text, label) {
  const a = vEl("a", "g-out", text);
  a.href = safeUrl(url);
  a.target = "_blank";
  a.rel = "noopener";
  if (label) a.setAttribute("aria-label", label);
  return a;
}
// Dates / nights / guests / 🐕, the notes, the hikes and the nature sights counted by kind.
function fillDetails(box, trip) {
  box.replaceChildren();
  const facts = [];
  const nights = nightsOf(trip);
  if (trip.start && trip.end) facts.push(`📅 ${shortDate(trip.start)} → ${shortDate(trip.end)}`);
  else if (trip.start) facts.push(`📅 from ${shortDate(trip.start)}`);
  if (nights) facts.push(plural(nights, "night"));
  facts.push(`👥 ${trip.guests}`);
  if (trip.dog) facts.push("🐕 dog");
  const line = vEl("div", "g-facts");
  for (const f of facts) line.append(vEl("span", "g-fact", f));
  box.append(line);
  if (trip.notes?.trim()) {
    const notes = vEl("div", "g-notes");
    notes.append(vEl("div", "g-label", `📝 Notes${trip.created_by ? ` by ${auth.label(trip.created_by)}` : ""}`),
      vEl("div", "g-notes-text", trip.notes.trim()));
    box.append(notes);
  }
  if (trip.hikes.length) {
    box.append(vEl("h4", "g-section-title", `🥾 ${plural(trip.hikes.length, "hike")}`));
    const list = vEl("ul", "g-hikes");
    for (const h of trip.hikes) {
      const li = vEl("li", "g-hike");
      const head = vEl("div", "g-item-head");
      head.append(vEl("span", "g-item-name", h.name), outLink(h.url, "↗", `Open ${h.name} on Wikiloc`));
      const meta = [h.distance_km != null ? `${h.distance_km} km` : null, hikeTime(h),
        h.elevation_gain_m != null ? `+${h.elevation_gain_m} m` : null, h.difficulty].filter(Boolean);
      li.append(head, vEl("div", "g-item-meta", meta.join(" · ")));
      list.append(li);
    }
    box.append(list);
  } else {
    box.append(vEl("p", "list-note", "No hikes saved in this trip."));
  }
  const sights = trip.sights || [];
  if (sights.length) {
    const byKind = new Map();
    for (const s of sights) byKind.set(s.kind, (byKind.get(s.kind) || 0) + 1);
    const nature = vEl("div", "g-nature");
    nature.append(vEl("span", "g-label", `🌿 Nature nearby `));
    for (const [kind, n] of byKind) {
      const chip = vEl("span", "g-chip", `${SIGHT_EMOJI[kind] || "🌿"} ${n} ${(SIGHT_LABELS[kind] || "sight").toLowerCase()}${n === 1 ? "" : "s"}`);
      nature.append(chip);
    }
    box.append(nature);
  }
}

// One card per stay: photo, name, price (total + per night), ★, 🐕, ↗ and 😍 / 🙂 / 🚫 (one 😍 per trip).
function buildStays(trip) {
  const v = guide.view;
  const round = guideRound();
  const standing = round && guideOrder(round).find((t) => t.trip_id === v.tripId);
  const inRound = new Set((standing?.stays || []).map((s) => s.stay_id));
  const stays = trip.stays.filter((s) => inRound.has(s.id));
  const box = v.sleep;
  box.replaceChildren();
  box.hidden = !stays.length;
  if (!stays.length) return;
  box.append(vEl("h4", "g-section-title", "🏡 Where would you sleep?"),
    vEl("p", "g-hint", "Rate the places – 😍 your favourite (one per trip)."));
  const collapsed = vEl("div", "g-collapsed");
  collapsed.append(vEl("span", null, "You said 🚫 no-go to this trip. "),
    vButton("You said no-go – show anyway", "link-btn g-show-anyway", () => {
      guide.showStays.add(v.tripId);
      updateStep();
    }));
  const list = vEl("ul", "g-stays");
  const nights = nightsOf(trip);
  for (const s of stays) {
    const li = vEl("li", "g-stay");
    li.dataset.stay = s.id;
    const top = vEl("div", "g-stay-top");
    if (safeUrl(s.photo) !== "#") {
      const img = vEl("img", "g-stay-photo");
      img.src = s.photo;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => img.replaceWith(vEl("div", "g-stay-photo g-stay-nophoto", "🏡")));
      top.append(img);
    } else {
      top.append(vEl("div", "g-stay-photo g-stay-nophoto", "🏡"));
    }
    const info = vEl("div", "g-stay-info");
    const head = vEl("div", "g-item-head");
    head.append(vEl("span", "g-item-name", s.name), outLink(s.url, "↗", `Open ${s.name} (${s.source === "airbnb" ? "Airbnb" : "Booking.com"})`));
    info.append(head);
    const price = [];
    if (s.total_price != null) {
      price.push(`${money(s.total_price, s.currency)} total`);
      if (nights && nights > 1) price.push(`${money(s.total_price / nights, s.currency)}/night`);
    }
    const meta = vEl("div", "g-item-meta");
    meta.textContent = [...price, s.rating != null ? `★ ${s.rating}` : null].filter(Boolean).join(" · ")
      || (s.source === "airbnb" ? "Airbnb" : "Booking.com");
    info.append(meta);
    const tags = vEl("div", "g-tags");
    tags.append(vEl("span", "g-tag", s.source === "airbnb" ? "Airbnb" : "Booking.com"));
    if (s.pets_allowed || s.badge === "pets" || s.badge === "pets_garden") tags.append(vEl("span", "g-tag g-tag-dog", "🐕 pets OK"));
    if (s.has_garden || s.badge === "garden" || s.badge === "pets_garden") tags.append(vEl("span", "g-tag", "🌳 garden"));
    info.append(tags);
    top.append(info);
    li.append(top);
    const row = vEl("div", "g-stay-buttons");
    const btns = new Map();
    for (const [rating, emoji, label] of RATINGS) {
      const b = vButton(`${emoji} ${label}`, "g-stay-btn", () => {
        const standingNow = guideRound() && guideOrder(guideRound()).find((t) => t.trip_id === v.tripId);
        const mine = myVote(standingNow?.stays.find((x) => x.stay_id === s.id)?.votes || []);
        castVote({ tripId: v.tripId, stayId: s.id }, rating, mine?.comment || null);  // keeps a comment from the Vote tab
      });
      b.dataset.rating = rating;
      b.setAttribute("aria-label", `${label}: ${s.name}`);
      btns.set(rating, b);
      row.append(b);
    }
    v.stayBtns.set(s.id, btns);
    li.append(row);
    list.append(li);
  }
  box.append(collapsed, list);
  v.staysList = list;
  v.staysCollapsed = collapsed;
}

// The open step from the newest round answer: pressed buttons, the comment (unless it is being typed in), the 😍 note,
// the No-go collapse, the dots and the Next label.
function updateStep() {
  const v = guide.view;
  const round = guideRound();
  if (!v || !round) return;
  const order = guideOrder(round);
  const index = order.findIndex((t) => t.trip_id === v.tripId);
  const t = order[index];
  if (!t) return;
  for (const [rating, b] of v.rateBtns) b.setAttribute("aria-pressed", String(t.my_rating === rating));
  const mine = myVote(t.votes);
  v.comment.disabled = !t.my_rating;
  v.comment.title = t.my_rating ? "Saved with your rating" : "Rate first, then add a comment";
  if (document.activeElement !== v.comment) v.comment.value = mine?.comment || "";
  const love = round.me?.love_trip_id;
  const loveName = love && love !== t.trip_id ? order.find((x) => x.trip_id === love)?.name : null;
  v.loveNote.textContent = love === t.trip_id ? "😍 This trip has your Love – your one favourite of the round."
    : loveName ? `Your 😍 is on “${loveName}” – tapping 😍 here moves it.` : "You have one 😍 for the whole round.";
  for (const s of t.stays) {
    const btns = v.stayBtns.get(s.stay_id);
    if (btns) for (const [rating, b] of btns) b.setAttribute("aria-pressed", String(s.my_rating === rating));
  }
  if (v.staysList) {
    const hide = t.my_rating === "nogo" && !guide.showStays.has(t.trip_id);
    v.staysList.hidden = hide;
    v.staysCollapsed.hidden = !hide;
  }
  // dots + progress label
  const body = $("guide-body");
  for (const dot of body.querySelectorAll(".g-dot[data-trip]")) {
    const d = order.find((x) => x.trip_id === dot.dataset.trip);
    if (!d) continue;
    dot.classList.toggle("rated", Boolean(d.my_rating));
    const i = order.indexOf(d);
    dot.setAttribute("aria-label", `Trip ${i + 1}: ${d.name} – ${d.my_rating ? RATING_SHORT[d.my_rating] : "not rated"}`);
  }
  const rated = body.querySelector(".g-step-rated");
  if (rated) rated.textContent = ` · ${order.filter((x) => x.my_rating).length} of ${order.length} rated`;
  const last = index === order.length - 1;
  setNav({
    back: index > 0 ? () => goToStep(order[index - 1].trip_id) : null,
    next: () => (last ? showScreen("summary") : goToStep(order[index + 1].trip_id)),
    nextLabel: !t.my_rating ? "Decide later →" : last ? "Review my picks →" : "Next →",
  });
}
function saveComment() {
  const v = guide.view;
  const round = guideRound();
  const t = v && round && round.trips.find((x) => x.trip_id === v.tripId);
  if (!t || !t.my_rating) return;
  const text = v.comment.value.trim();
  if (text === (myVote(t.votes)?.comment || "")) return;
  castVote({ tripId: t.trip_id }, t.my_rating, text);
}

// ---- summary: every trip with your rating, comment and stay pick; tap one to change it; Done → Overview ----
function stayPick(t) {
  const loved = t.stays.find((s) => s.my_rating === "love");
  if (loved) return `🏡 😍 ${loved.name}`;
  const ok = t.stays.filter((s) => s.my_rating === "ok").map((s) => s.name);
  return ok.length ? `🏡 🙂 ${ok.join(", ")}` : null;
}
// What the summary shows: a poll that changes none of it leaves the screen (and the focus) alone.
function summaryKey(round) {
  return JSON.stringify([round.title, round.me?.love_trip_id, guideOrder(round).map((t) => [t.trip_id, t.name,
    t.my_rating, myVote(t.votes)?.comment, t.stays.map((s) => [s.name, s.my_rating])])]);
}
function renderSummary(force = true) {
  const round = guideRound();
  if (!round || round.status !== "open") return showScreen("closed");
  const key = summaryKey(round);
  if (!force && guide.screen === "summary" && guide.summaryKey === key) return;
  guide.summaryKey = key;
  guide.screen = "summary";
  setTitle(`🗳️ ${round.title}`);
  const order = guideOrder(round);
  const body = $("guide-body");
  const scroll = $("guide").scrollTop;
  body.replaceChildren(progress(round, null));
  const box = vEl("section", "g-summary");
  box.append(vEl("h3", "g-trip-name", "Your picks"));
  const unrated = order.filter((t) => !t.my_rating);
  if (unrated.length) {
    box.append(vEl("div", "g-warn", `⚠ ${plural(unrated.length, "trip")} not rated yet – tap ${unrated.length === 1 ? "it" : "one"} to decide`));
  } else {
    box.append(vEl("div", "g-done", "✓ You rated every trip – thank you!"));
  }
  const love = order.find((t) => t.trip_id === round.me?.love_trip_id);
  box.append(vEl("div", "g-love-sum", love ? `😍 Your Love: ${love.name}` : "😍 You haven't given your Love yet – one trip can get it"));
  const list = vEl("ol", "g-sum-list");
  order.forEach((t, i) => {
    const li = vEl("li");
    const b = vButton("", `g-sum-row${t.my_rating ? "" : " unrated"}`, () => goToStep(t.trip_id));
    b.dataset.trip = t.trip_id;
    const head = vEl("span", "g-sum-head");
    head.append(vEl("span", "g-sum-name", `${i + 1}. ${t.name}`),
      vEl("span", `g-sum-rating${t.my_rating ? ` r-${t.my_rating}` : ""}`, t.my_rating ? RATING_SHORT[t.my_rating] : "⚠ Not rated"));
    b.append(head);
    const comment = myVote(t.votes)?.comment;
    if (comment) b.append(vEl("span", "g-sum-line", `💬 “${comment}”`));
    const pick = stayPick(t);
    if (pick) b.append(vEl("span", "g-sum-line", pick));
    else if (t.stays.length && t.my_rating !== "nogo") b.append(vEl("span", "g-sum-line muted", "🏡 No stay picked yet"));
    b.append(vEl("span", "g-sum-edit", "Change ›"));
    li.append(b);
    list.append(li);
  });
  box.append(list);
  body.append(box);
  $("guide").scrollTop = scroll;
  setNav({
    back: order.length ? () => goToStep(order[order.length - 1].trip_id) : null,
    next: () => { closeGuide(); openVoteOverview(round.id); },
    nextLabel: "Done ✓",
  });
}

// ---- 🗺️ See on map → the map + "← Back to voting (Trip 2 of 5)" ----
async function seeOnMap(tripId) {
  const round = guideRound();
  try {
    const trip = await loadTrip(tripId);
    const order = round ? guideOrder(round) : [];
    const index = order.findIndex((t) => t.trip_id === tripId);
    guide.back = { roundId: guide.roundId, tripId };
    closeGuide();
    $("guide-pill-back").textContent = `← Back to voting (Trip ${index + 1} of ${order.length})`;
    // Shown before the map: mobile.js's history (synced by showTripOnMap and below) gets a "guide" entry, so the
    // back gesture / button reopens the guide at this step (guideHistoryBack) instead of leaving the site.
    $("guide-pill").hidden = false;
    showTripOnMap(trip, trip.name);
    window.mobile?.syncHistory();
  } catch (err) {
    showVoteError(err.message);
  }
}
// Hiding the pill (← Back to voting, ✕, the guide opened another way) steps back over its history entry.
function hidePill() {
  if ($("guide-pill").hidden) return;
  $("guide-pill").hidden = true;
  window.mobile?.syncHistory();
}
function backToVoting() {
  const back = guide.back;
  hidePill();
  const round = vote.round;
  if (!back || !round || round.id !== back.roundId || round.status !== "open") return openVoteOverview(back?.roundId);
  openGuide(round, { tripId: back.tripId });
}

// The back gesture / button left the pill's history entry (mobile.js): back to the guide at the same step.
window.guideHistoryBack = () => { if (!$("guide-pill").hidden) backToVoting(); };

// ---- live updates ----
document.addEventListener("round-changed", () => {
  if (!guideOpen()) return;
  const round = guideRound();
  if (guide.screen === "join") {
    if (round && round.me?.joined) openGuide(round);  // joined elsewhere (another tab, or by voting)
    else if (round) guide.joinRound = round;
    return;
  }
  if (!round || round.status !== "open") {
    if (guide.screen !== "closed") showScreen("closed");
    return;
  }
  if (guide.screen === "trip") {
    if (!round.trips.some((t) => t.trip_id === guide.tripId)) openGuide(round);
    else if (guide.view && $("guide-body").querySelectorAll(".g-dot[data-trip]").length !== round.trips.length) renderStep();
    else updateStep();
  } else if (guide.screen === "summary") {
    renderSummary(false);
  }
});
document.addEventListener("users-changed", () => {
  if (!guideOpen()) return;
  if (guide.screen === "join") renderJoin();
  else if (guide.screen === "summary") renderSummary();
});
document.addEventListener("auth-changed", () => {
  if (!auth.user) hidePill();  // the wall closed the guide; a new account starts fresh
});
// A press on a rating button (see guide.pressing): released without a click, the typed comment is saved after all.
$("guide-body").addEventListener("pointerdown", (event) => {
  if (event.button === 0 && event.target.closest?.(".g-rate-btn")) guide.pressing = true;
});
document.addEventListener("pointerup", () => {
  if (!guide.pressing) return;
  clearTimeout(guide.pressTimer);
  guide.pressTimer = setTimeout(() => {
    if (!guide.pressing) return;
    guide.pressing = false;
    if (guide.view && document.activeElement !== guide.view.comment) saveComment();
  }, PRESS_GRACE_MS);
}, true);

// ---- the share link: /?round=<id> ----
function linkParam() {
  try { return new URLSearchParams(location.search).get("round")?.trim() || null; } catch { return null; }
}
function clearLinkParam() {
  const url = new URL(location.href);
  url.searchParams.delete("round");
  history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
}
// True until the link has been handled (also while its round loads): the banner's auto-open waits for it.
function guideLinkPending() { return Boolean(guide.link) || guide.linkBusy; }
// Logged out: the wall shows "🗳️ 🍕 anna invites you to vote: <title> · closes <date>" above Sign up / Log in.
function inviteLine(round) {
  const box = vEl("div", "invite-line");
  const open = round.status === "open";
  if (round.status === "cancelled") {
    box.append(vEl("span", "invite-who", "🗳️ This round was cancelled: "), vEl("strong", "invite-what", round.title));
    return box;
  }
  box.append(vEl("span", "invite-who", `🗳️ ${auth.label(round.opener)} invites you to ${open ? "vote" : "see the results"}: `),
    vEl("strong", "invite-what", round.title),
    vEl("span", "invite-when", open ? ` · closes ${localWhen(round.closes_at)}` : " · voting is over"));
  return box;
}
async function showLinkInvite() {
  const id = guide.link;
  if (!id || auth.user) return;
  try {
    const res = await fetch(`/api/rounds/${enc(id)}`);
    if (!res.ok || auth.user || guide.link !== id) return;  // unknown: the toast comes after log in
    const round = await res.json();
    guide.inviteRound = round;
    auth.setInvite(inviteLine(round));
  } catch { /* offline: no invitation line */ }
}
// Logged in with a link: not joined → the Join screen; joined → the guide where you left off; over → the Overview;
// unknown → a toast and the normal app. guide.link (and so guideLinkPending) stays set until the round is parsed and
// marked auto-opened, so the banner can't open it a second time meanwhile. The ?round= parameter goes once the link
// is handled; a failure (server error, offline) keeps it, so a reload tries again.
async function handleLink() {
  const id = guide.link;
  if (!id || !auth.user || guide.linkBusy) return;
  guide.linkBusy = true;
  const done = () => {
    guide.link = null;
    auth.setInvite(null);
    guide.inviteRound = null;
  };
  try {
    const res = await fetch(`/api/rounds/${enc(id)}`);
    if (!auth.user) return;  // logged out meanwhile: the wall keeps the link
    if (res.status === 404) {
      done();
      clearLinkParam();
      notices(["That voting round doesn't exist"]);
      return;
    }
    if (!res.ok) {
      done();
      notices([`Couldn't open the voting round (${res.status}) – try the link again`]);
      return;
    }
    const round = await res.json();
    if (!auth.user) return;
    if (round.status === "open") markAutoOpened(round.id);  // the link opens it: nothing left for the banner
    done();
    clearLinkParam();
    for (const dlg of document.querySelectorAll("dialog[open]")) if (dlg.id !== "guide" && dlg.id !== "emoji-dialog") dlg.close();
    if (round.status !== "open") {
      openVoteOverview(round.id);
      return;
    }
    applyRound(round, nextSeq());
    if (round.me?.joined) openGuide(round);
    else openJoin(round);
  } catch {
    if (guide.link === id) done();
    notices(["Couldn't reach the server – open the link again"]);
  } finally {
    guide.linkBusy = false;
  }
}
document.addEventListener("auth-changed", () => {
  if (auth.user) handleLink();
});
document.addEventListener("users-changed", () => {  // the opener's emoji arrived after the line was drawn
  if (!auth.user && guide.inviteRound && auth.invited) auth.setInvite(inviteLine(guide.inviteRound));
});

$("guide-close").addEventListener("click", closeGuide);
$("guide").addEventListener("close", onGuideClose);
$("guide-pill-back").addEventListener("click", backToVoting);
$("guide-pill-close").addEventListener("click", hidePill);

guide.link = linkParam();
if (guide.link) {
  showLinkInvite();
  Promise.resolve(auth.ready).then(() => (auth.user ? handleLink() : null));
}
