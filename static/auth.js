"use strict";
// Log in / sign up: the #welcome wall (login first: it covers the app until someone is logged in), the header's
// account area, the phone top bar's account button and requireLogin(then). Loaded after app.js (uses $, notices) and
// before trips.js / vote.js, which wrap every action that changes shared data in requireLogin.
// The wall holds the one Log in / Sign up form (#auth-form). It shows a spinner until the page's first /api/auth/me
// answer (no flash of the app), the form once nobody is logged in, and goes away after log in / sign up; log out
// (or a 401) brings it back over whatever was open. Everything else on the page is inert while it shows.
// #welcome-invite above the form is a slot for an invitation line (hidden by CSS while empty).
// Auth calls use their own fetch, not api(): a 401 "Wrong username or password" must not trigger onUnauthorized.
// Emojis: every account has a unique emoji from the server's set; names are shown as auth.label(name) = "🤡 nikos"
// ("👤 nikos" when it has none yet). The users map behind it is refreshed on load, after log in / sign up / emoji
// change and by vote.js's polls; a change fires "users-changed" so open lists redraw their names.
const auth = {
  user: null, emoji: null, mode: "login", pending: null, busy: false, ready: null,
  users: new Map(),     // lowercase username → emoji (null: none yet)
  signupEmoji: null,    // the emoji picked in the Sign up tab
  prompted: false,      // the "pick your emoji" prompt opens at most once per page load
  emojiBusy: false,
};
const AUTH_BODY_ERROR = "Check the username (3–20 letters, digits, _ . -) and password (1–200 characters)";
const NO_EMOJI = "👤";
const EMOJI_TAKEN = "That emoji is taken – pick another";
const EMOJIS_GONE = "All emojis are taken";
const EMOJIS_DOWN = "Couldn't load the emojis – try again";

auth.emojiOf = (name) => (name ? auth.users.get(String(name).toLowerCase()) || null : null);
auth.label = (name) => `${auth.emojiOf(name) || NO_EMOJI} ${name}`;

let usersSeq = 0;
function setUsers(list) {
  const next = new Map(list.map((u) => [String(u.username).toLowerCase(), u.emoji || null]));
  if (auth.user) next.set(auth.user.toLowerCase(), auth.emoji);  // our own emoji is known first-hand
  const same = next.size === auth.users.size && [...next].every(([name, emoji]) => auth.users.get(name) === emoji);
  auth.users = next;
  if (!same) document.dispatchEvent(new CustomEvent("users-changed"));
}
// GET /api/users → the map; an answer older than a newer request's is dropped.
auth.refreshUsers = async () => {
  const seq = ++usersSeq;
  try {
    const res = await fetch("/api/users");
    if (!res.ok) return;
    const list = await res.json();
    if (seq === usersSeq && Array.isArray(list)) setUsers(list);
  } catch { /* offline: keep the map we have */ }
};

function setAuthUser(user, emoji = null) {
  emoji = (user && emoji) || null;
  const changed = auth.user !== user || auth.emoji !== emoji;
  if (auth.user !== user) auth.prompted = false;  // a different account may need the prompt
  auth.user = user;
  auth.emoji = emoji;
  const mapChanged = Boolean(user) && auth.users.get(user.toLowerCase()) !== emoji;
  if (mapChanged) auth.users.set(user.toLowerCase(), emoji);
  $("auth-login-btn").hidden = Boolean(user);
  $("auth-user").hidden = !user;
  $("auth-name").textContent = user ? `${emoji || NO_EMOJI} ${user}` : "";
  $("account-btn").hidden = !user;
  $("account-btn").textContent = user ? emoji || NO_EMOJI : "";
  $("account-btn").setAttribute("aria-label", user ? `Account: ${user}` : "Account");
  $("account-menu-name").textContent = user ? `${emoji || NO_EMOJI} ${user}` : "";
  if (!user) closeAccountMenu();
  showWall(!user);
  if (changed) document.dispatchEvent(new CustomEvent("auth-changed"));
  if (mapChanged) document.dispatchEvent(new CustomEvent("users-changed"));  // our own name's label changed
}
// GET /api/auth/me. prompt: an account without an emoji gets the picker (once per page load).
auth.refresh = async (prompt = true) => {
  try {
    const res = await fetch("/api/auth/me");
    if (res.ok) {
      const me = await res.json();
      setAuthUser(me.username ?? null, me.emoji ?? null);
    }
  } catch { /* offline: keep what we have */ }
  wallLoaded();  // the first answer (or failure) swaps the wall's spinner for the form when nobody is logged in
  if (prompt) promptForEmoji();
  return auth.user;
};
function promptForEmoji() {
  if (!auth.user || auth.emoji || auth.prompted) return;
  auth.prompted = true;
  openEmojiDialog(true);
}

// ---- the welcome wall ----
const wall = $("welcome");
// Everything but the wall (and the scripts) is inert while it shows; open dialogs, the ＋ Trip menu and the ☰ panel
// are closed first, so nothing opened before a log out (or a 401) sits above it. Only a change of state touches
// the inert flags (an emoji change while the ☰ panel is open must not undo the panel's own inert handling).
let wallOn = null;
function showWall(on) {
  if (on === wallOn) return;
  wallOn = on;
  if (on) {
    for (const dlg of document.querySelectorAll("dialog[open]")) dlg.close();
    window.closeTripMenu?.();
    window.mobile?.closeMenu();
  }
  wall.hidden = !on;
  for (const el of document.body.children) if (el !== wall && el.tagName !== "SCRIPT") el.inert = on;
  if (on && !wall.classList.contains("loading")) openLoginForm(auth.mode);
}
function wallLoaded() {
  if (!wall.classList.contains("loading")) return;
  wall.classList.remove("loading");
  $("welcome-loading").hidden = true;
  $("auth-form").hidden = false;
  if (!auth.user) openLoginForm("login");
}

// ---- phone top bar: the account button (your emoji) → a small menu: Change emoji / Log out ----
function openAccountMenu() {
  $("account-menu").hidden = false;
  $("account-btn").setAttribute("aria-expanded", "true");
  $("account-emoji").focus();
}
function closeAccountMenu() {
  if ($("account-menu").hidden) return;
  const hadFocus = $("account-menu").contains(document.activeElement);
  $("account-menu").hidden = true;
  $("account-btn").setAttribute("aria-expanded", "false");
  if (hadFocus && !$("account-btn").hidden) $("account-btn").focus({ preventScroll: true });
}
window.closeAccountMenu = closeAccountMenu;

// ---- the emoji picker: a grid of buttons, taken ones disabled ("taken by <name>"), the picked one pressed ----
const picker = { emojis: [], owners: new Map() };  // owners: taken emoji → username (null if unknown)
async function loadEmojis() {
  const [res, usersRes] = await Promise.all([fetch("/api/auth/emojis"), fetch("/api/users").catch(() => null)]);
  if (!res.ok) throw new Error(EMOJIS_DOWN);
  const data = await res.json();
  const users = usersRes && usersRes.ok ? await usersRes.json().catch(() => []) : [];
  if (Array.isArray(users) && users.length) {
    usersSeq += 1;  // fresher than any /api/users request still on its way
    setUsers(users);
  }
  const byEmoji = new Map((Array.isArray(users) ? users : []).filter((u) => u.emoji).map((u) => [u.emoji, u.username]));
  picker.emojis = data.emojis;
  picker.owners = new Map(data.taken.map((emoji) => [emoji, byEmoji.get(emoji) || null]));
}
// own: the user's current emoji (theirs, so not "taken"); selected: the pressed one. onTaken(message) for a tap on
// a taken emoji – phones have no tooltip, so the dialog says who has it.
function renderPicker(grid, { own = null, selected = null, onPick, onTaken }) {
  grid.replaceChildren();
  for (const emoji of picker.emojis) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "emoji-btn";
    b.textContent = emoji;
    b.dataset.emoji = emoji;
    const taken = picker.owners.has(emoji) && emoji !== own;
    const owner = picker.owners.get(emoji);
    b.setAttribute("aria-pressed", String(emoji === selected));
    if (taken) {
      b.setAttribute("aria-disabled", "true");
      b.title = owner ? `taken by ${owner}` : "taken";
      b.setAttribute("aria-label", `${emoji} ${b.title}`);
    }
    b.addEventListener("click", () => (taken ? onTaken(`${emoji} is ${b.title}`) : onPick(emoji)));
    grid.append(b);
  }
}
function pickerMessage(grid, message) {
  const p = document.createElement("p");
  p.className = "list-note";
  p.textContent = message;
  grid.replaceChildren(p);
}

// ---- the Log in / Sign up form (on the wall) ----
function setAuthMode(mode) {
  auth.mode = mode;
  const signup = mode === "signup";
  $("auth-submit").textContent = signup ? "Sign up" : "Log in";
  $("auth-tab-login").setAttribute("aria-selected", String(!signup));
  $("auth-tab-signup").setAttribute("aria-selected", String(signup));
  $("auth-repeat-row").hidden = !signup;
  $("auth-emoji-row").hidden = !signup;
  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  showAuthError("");
  updateAuthSubmit();
  if (signup) loadSignupPicker();
}
function showAuthError(message) { $("auth-error").textContent = message || ""; }
// Sign up stays disabled until an emoji is picked.
function updateAuthSubmit() {
  $("auth-submit").disabled = auth.busy || (auth.mode === "signup" && !auth.signupEmoji);
  $("auth-emoji-picked").textContent = auth.signupEmoji ? `: ${auth.signupEmoji}` : "";
}
function renderSignupPicker() {
  renderPicker($("auth-emoji-grid"), {
    selected: auth.signupEmoji,
    onPick: (emoji) => {
      auth.signupEmoji = emoji;
      showAuthError("");
      for (const b of $("auth-emoji-grid").children) b.setAttribute("aria-pressed", String(b.dataset.emoji === emoji));
      updateAuthSubmit();
    },
    onTaken: showAuthError,
  });
}
async function loadSignupPicker() {
  if (!picker.emojis.length) pickerMessage($("auth-emoji-grid"), "Loading…");
  try {
    await loadEmojis();
  } catch {
    if (!picker.emojis.length) pickerMessage($("auth-emoji-grid"), EMOJIS_DOWN);
    return;
  }
  if (picker.owners.has(auth.signupEmoji)) auth.signupEmoji = null;  // someone took it meanwhile
  renderSignupPicker();
  updateAuthSubmit();
  if (picker.emojis.length && picker.emojis.every((e) => picker.owners.has(e))) showAuthError("All emojis are taken");
}
// The form in a mode, passwords cleared, the focus in the username (whenever the wall comes up).
function openLoginForm(mode = "login") {
  setAuthMode(mode);
  $("auth-password").value = "";
  $("auth-repeat").value = "";
  $("auth-username").focus();
}

// Runs then() now when logged in; otherwise (the wall is up, so only code gets here) runs it once after a successful
// log in / sign up through the wall. Before the page's first /api/auth/me answer it waits for auth.ready, so a
// logged-in user's action right after load isn't lost (no gated action needs the click's user activation; the PDF's
// synchronous window.open path doesn't go through here).
function requireLogin(then) {
  if (auth.user) {
    then();
    return;
  }
  Promise.resolve(auth.ready).then(() => {
    if (auth.user) {
      then();
      return;
    }
    auth.pending = then;
    showWall(true);
  });
}

// A protected route answered 401 (logged out, or the cookie expired): the wall comes back over the page as it is.
window.onUnauthorized = () => {
  auth.pending = null;  // no automatic replay: the user repeats the click after logging in
  setAuthUser(null);
};

async function submitAuth(event) {
  event.preventDefault();
  if (auth.busy) return;
  const signup = auth.mode === "signup";
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  if (!username || !password) return showAuthError("Enter a username and a password");
  if (signup && password !== $("auth-repeat").value) return showAuthError("The passwords don't match");
  if (signup && !auth.signupEmoji) return showAuthError("Pick an emoji from the list");
  showAuthError("");
  auth.busy = true;
  updateAuthSubmit();
  try {
    const body = signup ? { username, password, emoji: auth.signupEmoji } : { username, password };
    const res = await fetch(`/api/auth/${signup ? "signup" : "login"}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (signup && res.status === 409 && (data.detail === EMOJI_TAKEN || data.detail === EMOJIS_GONE)) {
        auth.signupEmoji = null;  // someone was quicker: fresh taken list, pick again
        await loadSignupPicker();
      }
      showAuthError(typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? AUTH_BODY_ERROR : `Request failed (${res.status})`);
      return;
    }
    const then = auth.pending;
    auth.pending = null;
    if (signup) {
      setAuthUser(data.username, auth.signupEmoji);
      auth.signupEmoji = null;
    } else {
      await auth.refresh(false);  // the account's emoji (none: the prompt below)
      if (auth.user !== data.username) setAuthUser(data.username);  // /me failed: name only
    }
    auth.refreshUsers();
    $("auth-password").value = "";
    $("auth-repeat").value = "";
    if (then) then();
    promptForEmoji();
  } catch {
    showAuthError("Couldn't reach the server – try again");
  } finally {
    auth.busy = false;
    updateAuthSubmit();
  }
}

async function logout() {
  try {
    const res = await fetch("/api/auth/logout", { method: "POST" });
    if (!res.ok && res.status !== 401) throw new Error(`Log out failed (${res.status})`);
    setAuthUser(null);
  } catch (err) {
    notices([err.message.startsWith("Log out") ? err.message : "Couldn't reach the server – try again"]);
  }
}

// ---- Change emoji (click your name) / the first-login prompt (prompt: "Skip for now" shown) ----
function showEmojiError(message) { $("emoji-error").textContent = message || ""; }
function renderEmojiDialogPicker() {
  renderPicker($("emoji-grid"), { own: auth.emoji, selected: auth.emoji, onPick: saveEmoji, onTaken: showEmojiError });
}
async function openEmojiDialog(prompt = false) {
  if (!auth.user) return;
  $("emoji-title").textContent = prompt ? "Pick your emoji" : "Change emoji";
  $("emoji-note").textContent = prompt
    ? "Friends recognise each other by emoji + name – pick one that's yours alone."
    : "Friends see it next to your name. Taken ones are greyed out.";
  $("emoji-skip").parentElement.hidden = !prompt;  // the whole (phone: sticky) actions bar
  showEmojiError("");
  const dlg = $("emoji-dialog");
  if (!dlg.open) dlg.showModal();
  if (picker.emojis.length) renderEmojiDialogPicker(); else pickerMessage($("emoji-grid"), "Loading…");
  try {
    await loadEmojis();
    renderEmojiDialogPicker();
  } catch {
    if (!picker.emojis.length) pickerMessage($("emoji-grid"), EMOJIS_DOWN);
  }
}
async function saveEmoji(emoji) {
  if (auth.emojiBusy) return;
  if (emoji === auth.emoji) {
    $("emoji-dialog").close();
    return;
  }
  auth.emojiBusy = true;
  showEmojiError("");
  $("emoji-grid").setAttribute("aria-busy", "true");
  try {
    const res = await fetch("/api/auth/emoji", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ emoji }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      window.onUnauthorized();
      return;
    }
    if (!res.ok) {
      if (res.status === 409) {
        await loadEmojis().catch(() => {});
        renderEmojiDialogPicker();
      }
      showEmojiError(typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`);
      return;
    }
    setAuthUser(data.username || auth.user, data.emoji || emoji);
    auth.refreshUsers();
    $("emoji-dialog").close();
  } catch {
    showEmojiError("Couldn't reach the server – try again");
  } finally {
    auth.emojiBusy = false;
    $("emoji-grid").removeAttribute("aria-busy");
  }
}

$("auth-form").addEventListener("submit", submitAuth);
$("auth-tab-login").addEventListener("click", () => { setAuthMode("login"); $("auth-username").focus(); });
$("auth-tab-signup").addEventListener("click", () => { setAuthMode("signup"); $("auth-username").focus(); });
$("auth-login-btn").addEventListener("click", () => { auth.pending = null; showWall(true); });
$("account-btn").addEventListener("click", () => ($("account-menu").hidden ? openAccountMenu() : closeAccountMenu()));
$("account-emoji").addEventListener("click", () => { closeAccountMenu(); openEmojiDialog(false); });
$("account-logout").addEventListener("click", () => { closeAccountMenu(); logout(); });
document.addEventListener("click", (event) => {
  if (!event.target.closest?.("#account-menu, #account-btn")) closeAccountMenu();
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAccountMenu(); });
$("auth-logout").addEventListener("click", logout);
$("auth-name").addEventListener("click", () => openEmojiDialog(false));
$("emoji-close").addEventListener("click", () => $("emoji-dialog").close());
$("emoji-skip").addEventListener("click", () => $("emoji-dialog").close());
showWall(true);  // in the page from the first paint; a spinner until /me answers
auth.ready = auth.refresh();
auth.refreshUsers();
