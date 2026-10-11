"use strict";
// Phone layout (≤ 760 px, styles at the end of style.css): bottom tabs 🗺️ Map / 📋 List / 🧳 Trips / 🗳️ Vote, the ☰
// controls panel (#controls-more, the header's own controls – nothing is duplicated) and the tab badges.
// Loaded last; uses $ (app.js) and window.mapShown (app.js). The Trips / Vote tabs click the existing header buttons,
// so those dialogs keep all their own code; trips.js / vote.js call mobile.dialogClosed() when their dialog closes.
// Body classes: phone-tab-map | phone-tab-list (the view under any dialog) and menu-open. Desktop CSS ignores them.
window.mobile = (() => {
  const phone = window.matchMedia("(max-width: 760px)");
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

  function openMenu() {
    body.classList.add("menu-open");
    $("menu-btn").setAttribute("aria-expanded", "true");
    $("menu-close").focus();
  }
  function closeMenu() {
    if (!body.classList.contains("menu-open")) return;
    body.classList.remove("menu-open");
    $("menu-btn").setAttribute("aria-expanded", "false");
  }

  function showTab(name) {
    closeMenu();
    if (name === "trips" || name === "vote") {
      $(name === "trips" ? "trips-btn" : "vote-btn").click();
      syncTabbar();
      return;
    }
    setView(name === "list" ? "list" : "map");
  }
  const showMap = () => showTab("map");
  function dialogClosed() {
    syncTabbar();
    if (view === "map") window.mapShown?.();
  }

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

  $("tabbar").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (button) showTab(button.dataset.tab);
  });
  $("menu-btn").addEventListener("click", openMenu);
  $("menu-close").addEventListener("click", closeMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && body.classList.contains("menu-open") && !document.querySelector("dialog[open]")) closeMenu();
  });
  // Capture listeners run before app.js's own click handlers, so searching / drawing starts on a visible map:
  // a search or draw button closes the panel and shows the Map tab; 📍 Go shows the Map tab.
  for (const id of ["search", "search-all", "suggest-trips", "draw-rect-btn", "draw-circle-btn", "goto-btn"]) {
    $(id).addEventListener("click", () => { if (isPhone()) showMap(); }, true);
  }
  // A Trips / Vote dialog opened from anywhere lights its tab.
  const dialogWatch = new MutationObserver(syncTabbar);
  for (const id of Object.values(DIALOG_TABS)) dialogWatch.observe($(id), { attributes: true, attributeFilter: ["open"] });
  // Rotating across the breakpoint can reveal a map hidden by the List tab.
  phone.addEventListener("change", () => window.mapShown?.());

  setView("map");
  return { isPhone, showTab, showMap, openMenu, closeMenu, dialogClosed };
})();
