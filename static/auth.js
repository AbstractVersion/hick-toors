"use strict";
// Log in / sign up: the header's 👤 area, the #auth-dialog and requireLogin(then). Loaded after app.js (uses $,
// notices) and before trips.js / vote.js, which wrap every action that changes shared data in requireLogin.
// Auth calls use their own fetch, not api(): a 401 "Wrong username or password" must not trigger onUnauthorized.
const auth = { user: null, mode: "login", pending: null, busy: false, ready: null };
const AUTH_BODY_ERROR = "Check the username (3–20 letters, digits, _ . -) and password (1–200 characters)";

function setAuthUser(user) {
  const changed = auth.user !== user;
  auth.user = user;
  $("auth-login-btn").hidden = Boolean(user);
  $("auth-user").hidden = !user;
  $("auth-name").textContent = user || "";
  if (changed) document.dispatchEvent(new CustomEvent("auth-changed"));
}
auth.refresh = async () => {
  try {
    const res = await fetch("/api/auth/me");
    if (res.ok) setAuthUser((await res.json()).username ?? null);
  } catch { /* offline: keep what we have */ }
  return auth.user;
};

function setAuthMode(mode) {
  auth.mode = mode;
  const signup = mode === "signup";
  $("auth-title").textContent = signup ? "👤 Sign up" : "👤 Log in";
  $("auth-submit").textContent = signup ? "Sign up" : "Log in";
  $("auth-tab-login").setAttribute("aria-selected", String(!signup));
  $("auth-tab-signup").setAttribute("aria-selected", String(signup));
  $("auth-repeat-row").hidden = !signup;
  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  showAuthError("");
}
function showAuthError(message) { $("auth-error").textContent = message || ""; }
function openAuthDialog(mode = "login") {
  setAuthMode(mode);
  $("auth-password").value = "";
  $("auth-repeat").value = "";
  const dlg = $("auth-dialog");
  if (!dlg.open) dlg.showModal();
  $("auth-username").focus();
}

// Runs then() now when logged in; otherwise opens the dialog and runs it once after a successful log in / sign up
// (dropped if the dialog is closed without logging in). Before the page's first /api/auth/me answer it waits for
// auth.ready, so a logged-in user clicking right after load isn't shown the dialog (no gated action needs the click's
// user activation; the PDF's synchronous window.open path doesn't go through here).
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
    openAuthDialog("login");
  });
}

// A protected route answered 401 (logged out, or the cookie expired): the caller still shows its error.
window.onUnauthorized = () => {
  setAuthUser(null);
  auth.pending = null;  // no automatic replay: the user repeats the click after logging in
  openAuthDialog("login");
};

async function submitAuth(event) {
  event.preventDefault();
  if (auth.busy) return;
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  if (!username || !password) return showAuthError("Enter a username and a password");
  if (auth.mode === "signup" && password !== $("auth-repeat").value) return showAuthError("The passwords don't match");
  showAuthError("");
  auth.busy = true;
  $("auth-submit").disabled = true;
  try {
    const res = await fetch(`/api/auth/${auth.mode === "signup" ? "signup" : "login"}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showAuthError(typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? AUTH_BODY_ERROR : `Request failed (${res.status})`);
      return;
    }
    const then = auth.pending;
    auth.pending = null;
    setAuthUser(data.username);
    $("auth-password").value = "";
    $("auth-repeat").value = "";
    $("auth-dialog").close();
    if (then) then();
  } catch {
    showAuthError("Couldn't reach the server – try again");
  } finally {
    auth.busy = false;
    $("auth-submit").disabled = false;
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

$("auth-form").addEventListener("submit", submitAuth);
$("auth-tab-login").addEventListener("click", () => { setAuthMode("login"); $("auth-username").focus(); });
$("auth-tab-signup").addEventListener("click", () => { setAuthMode("signup"); $("auth-username").focus(); });
$("auth-close").addEventListener("click", () => $("auth-dialog").close());
$("auth-dialog").addEventListener("close", () => { auth.pending = null; });
$("auth-login-btn").addEventListener("click", () => { auth.pending = null; openAuthDialog("login"); });
$("auth-logout").addEventListener("click", logout);
auth.ready = auth.refresh();
