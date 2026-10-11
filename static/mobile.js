"use strict";
// Phone layout (PHONE_QUERY: ≤ 760 px wide, or a touch screen ≤ 500 px tall = a phone in landscape; the styles at the
// end of style.css use the same query): bottom tabs 🗺️ Map / 📋 List / 🧳 Trips / 🗳️ Vote, the ☰
// controls panel (#controls-more, the header's own controls – nothing is duplicated) and the tab badges.
// Loaded last; uses $ (app.js) and window.mapShown (app.js). The Trips / Vote tabs click the existing header buttons,
// so those dialogs keep all their own code; trips.js / vote.js call mobile.dialogClosed() when their dialog closes.
// Body classes: phone-tab-map | phone-tab-list (the view under any dialog) and menu-open. Desktop CSS ignores them.
// Back gesture / button: opening the ☰ panel or going Map → List pushes a history entry (phone only), so back closes
// the panel / returns to the Map tab instead of leaving the site.
window.mobile = (() => {
  // The single source of truth in JS; style.css's phone @media line must be this same query.
  const PHONE_QUERY = "(max-width: 760px), (pointer: coarse) and (max-height: 500px)";
  const phone = window.matchMedia(PHONE_QUERY);
  const body = document.body;
  const DIALOG_TABS = { trips: "trips-dialog", vote: "vote-dialog" };
  let view = "map";  // "map" | "list" – a dialog tab shows over it and returns to it when closed

  const isPhone = () => phone.matches;

  // The highlighted tab: an open Trips / Vote dialog wins (also when it was opened from elsewhere, e.g. ＋ Trip).
  function syncTabbar() {
    const dialogTab = Object.keys(DIALOG_TABS).find((name) => $(DIALOG_TABS[name]).open);
    const active = dialogTab || view;
    for (const button of document.querySelectorAll("#tabbar button[data-tab]")) {
      button.setAttribute("aria-pressed", String(button.dataset.tab === active));
    }
  }
  function setView(name) {
    view = name;
    body.classList.toggle("phone-tab-map", name === "map");
    body.classList.toggle("phone-tab-list", name === "list");
    syncTabbar();
    if (name === "map") window.mapShown?.();  // re-measure after the List tab hid it; runs a fit that waited
  }

  // While the ☰ panel is open it is a modal dialog: everything behind it is inert (no Tab, no taps); closing it
  // gives the focus back to ☰. ☰ only exists on phones, so on desktop the panel stays plain header content.
  const panel = $("controls-more");
  const behindPanel = () => [document.querySelector("#controls .goto"), $("menu-btn"), $("notices"),
    document.querySelector("main"), $("ask"), $("tabbar")];
  function openMenu() {
    body.classList.add("menu-open");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Search controls");
    $("menu-btn").setAttribute("aria-expanded", "true");
    for (const el of behindPanel()) el.inert = true;
    $("menu-close").focus();
    syncHistory();
  }
  function closeMenu() {
    if (!body.classList.contains("menu-open")) return;
    const hadFocus = panel.contains(document.activeElement);
    body.classList.remove("menu-open");
    for (const name of ["role", "aria-modal", "aria-label"]) panel.removeAttribute(name);
    $("menu-btn").setAttribute("aria-expanded", "false");
    for (const el of behindPanel()) el.inert = false;
    if (hadFocus) $("menu-btn").focus({ preventScroll: true });
  }
  const closeMenuFromUi = () => { closeMenu(); syncHistory(); };

  function showTab(name) {
    closeMenu();
    if (name === "trips" || name === "vote") {
      $(name === "trips" ? "trips-btn" : "vote-btn").click();
      syncTabbar();
    } else {
      setView(name === "list" ? "list" : "map");
    }
    syncHistory();
  }
  const showMap = () => showTab("map");
  function dialogClosed() {
    syncTabbar();
    if (view === "map") window.mapShown?.();
  }

  // ---- back gesture: our history entries carry {phone: [...]} – what is open, bottom up ("list", then "menu") ----
  const entries = () => (Array.isArray(history.state?.phone) ? history.state.phone : []);
  let ownStepBack = false;
  // Runs after every UI change: pushes an entry for what was opened (phone only) and steps back over the entries of
  // what was closed from the UI, so they never pile up. The popstate of our own step back just catches up.
  function syncHistory() {
    const want = [];
    if (view === "list") want.push("list");
    if (body.classList.contains("menu-open")) want.push("menu");
    const have = entries();
    if (have.length === want.length && have.every((kind, i) => kind === want[i])) return;
    if (have.length > want.length) {
      ownStepBack = true;
      history.go(want.length - have.length);
      return;
    }
    if (!isPhone()) return;  // never push on desktop
    if (have.length) history.replaceState({ ...history.state, phone: want.slice(0, have.length) }, "");
    for (let i = have.length; i < want.length; i += 1) history.pushState({ phone: want.slice(0, i + 1) }, "");
  }
  window.addEventListener("popstate", () => {
    if (ownStepBack) {
      ownStepBack = false;
      syncHistory();
      return;
    }
    const open = entries();  // back (or forward): close what the entry we landed on does not have open
    if (!open.includes("menu")) closeMenu();
    if (!open.includes("list") && view === "list") setView("map");
  });

  // Tab badges mirror the existing counters: (12) hikes, (3) trips, " · 2 left" votes; no badge for 0.
  const number = (el) => {
    const n = (el.textContent.match(/\d+/) || [""])[0];
    return n === "0" ? "" : n;
  };
  function mirror(sourceId, badgeId, read) {
    const source = $(sourceId), badge = $(badgeId);
    const update = () => {
      const text = read(source);
      badge.textContent = text;
      badge.hidden = !text;
    };
    new MutationObserver(update).observe(source, { childList: true, characterData: true, subtree: true, attributes: true });
    update();
  }
  mirror("hike-count", "tab-badge-list", number);
  mirror("trips-count", "tab-badge-trips", number);
  mirror("vote-left", "tab-badge-vote", (el) => (el.hidden ? "" : number(el)));

  // Searching on the Map tab: the sidebar's spinners are hidden there, so while any search loads a "Searching…"
  // chip on the map (#map-busy) and a spinner on the 📋 List tab mirror them.
  const searchSpinners = ["hikes", "stay:airbnb", "stay:booking", "stay:retry", "combos"]
    .flatMap((key) => [...$("sidebar").querySelectorAll(`[data-spin="${key}"]`)]);
  function syncBusy() {
    const busy = searchSpinners.some((el) => !el.hidden);
    $("map-busy").hidden = !busy;
    $("tab-spin-list").hidden = !busy;
  }
  const busyWatch = new MutationObserver(syncBusy);
  for (const el of searchSpinners) busyWatch.observe(el, { attributes: true, attributeFilter: ["hidden"] });
  syncBusy();

  $("tabbar").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (button) showTab(button.dataset.tab);
  });
  $("menu-btn").addEventListener("click", openMenu);
  $("menu-close").addEventListener("click", closeMenuFromUi);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && body.classList.contains("menu-open") && !document.querySelector("dialog[open]")) closeMenuFromUi();
  });
  // Capture listeners run before app.js's own click handlers, so searching / drawing starts on a visible map:
  // a search or draw button closes the panel and shows the Map tab, so does 🌿 Nature (its chips and pins are on
  // the map); 📍 Go shows the Map tab.
  for (const id of ["search", "search-all", "suggest-trips", "draw-rect-btn", "draw-circle-btn", "nature-btn", "goto-btn"]) {
    $(id).addEventListener("click", () => { if (isPhone()) showMap(); }, true);
  }
  // A Trips / Vote dialog opened from anywhere lights its tab.
  const dialogWatch = new MutationObserver(syncTabbar);
  for (const id of Object.values(DIALOG_TABS)) dialogWatch.observe($(id), { attributes: true, attributeFilter: ["open"] });
  // Rotating / resizing across the breakpoint resets the phone state: an unfinished draw cancelled (its hint and
  // Leaflet.draw's toolbar differ per layout), panel and ＋ Trip sheet closed, Map tab (which also re-measures a map
  // the List tab had hidden).
  phone.addEventListener("change", () => {
    window.cancelDrawing?.();
    closeMenu();
    window.closeTripMenu?.();
    setView("map");
    syncHistory();
  });

  setView("map");
  return { PHONE_QUERY, isPhone, showTab, showMap, openMenu, closeMenu, dialogClosed };
})();
