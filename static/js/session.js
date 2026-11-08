// Session safety: idle auto-lock, two-step sign-in (authenticator app) helpers,
// the personal security-activity log, and the privacy-notice consent check.
// Everything here runs in the browser against the signed-in person's own rows.

// ---- Idle lock --------------------------------------------------------------
export const IDLE_MS = 30 * 60 * 1000;           // 30 minutes without touching the app
const KEY = "mt_last_active";

// Pure so it can be unit-tested: has the person been away for `limit` or longer?
export function isIdle(now, last, limit = IDLE_MS) {
  return Number.isFinite(last) && last > 0 && now - last >= limit;
}

let started = false;
// Starts watching for inactivity. `lock()` is called once when the limit passes.
// The last-activity time lives in localStorage so every open tab agrees, and is
// re-checked whenever the tab wakes up (a laptop lid or a phone coming back).
export function startIdleLock(lock, { limit = IDLE_MS, now = () => Date.now() } = {}) {
  if (started) return () => {};
  started = true;
  let mem = now(), lastWrite = 0, fired = false;
  const read = () => {
    try { const v = Number(localStorage.getItem(KEY)); return v > 0 ? v : mem; } catch (e) { return mem; }
  };
  const touch = () => {
    const t = now();
    mem = t;
    if (t - lastWrite < 5000) return;
    lastWrite = t;
    try { localStorage.setItem(KEY, String(t)); } catch (e) {}
  };
  const check = () => {
    if (fired) return;
    if (isIdle(now(), read(), limit)) { fired = true; lock(); }
  };
  touch();
  const evs = ["pointerdown", "keydown", "touchstart", "scroll", "wheel"];
  evs.forEach((e) => window.addEventListener(e, touch, { passive: true }));
  const timer = setInterval(check, 30000);
  const onVis = () => { if (document.visibilityState === "visible") check(); };
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("focus", check);
  check();
  return () => {
    clearInterval(timer);
    evs.forEach((e) => window.removeEventListener(e, touch));
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("focus", check);
    started = false;
  };
}

// ---- Two-step sign-in (TOTP authenticator app) -----------------------------
export async function totpFactors(db) {
  const { data } = await db.auth.mfa.listFactors();
  return (data?.totp || []).filter((f) => f.status === "verified");
}

// True when this person has two-step on but this session has only passed the password.
export async function needsMfa(db) {
  if (!db?.auth?.mfa) return false;
  const { data, error } = await db.auth.mfa.getAuthenticatorAssuranceLevel();
  if (error || !data) return false;
  return data.nextLevel === "aal2" && data.currentLevel !== "aal2";
}

export async function verifyCode(db, factorId, code) {
  const { data: ch, error: e1 } = await db.auth.mfa.challenge({ factorId });
  if (e1) return { error: e1 };
  return db.auth.mfa.verify({ factorId, challengeId: ch.id, code: String(code).trim() });
}

// ---- Security activity log -------------------------------------------------
export const EVENT_LABEL = {
  sign_in: "Signed in",
  sign_out_all: "Signed out of all devices",
  password_changed: "Password changed",
  mfa_enabled: "Two-step sign-in turned on",
  mfa_removed: "Two-step sign-in turned off",
  data_exported: "Downloaded a copy of your data",
  consent_accepted: "Accepted the privacy notice",
  idle_lock: "Signed out after 30 minutes of inactivity",
};

// Best effort and silent: a missing table or a flaky network must never block
// the action the person actually asked for.
export async function logEvent(db, event, detail = {}) {
  try { await db.from("audit_log").insert({ event, detail }); } catch (e) {}
}

// ---- Privacy-notice consent ------------------------------------------------
export const noticeVersion = () => (window.__CONFIG && window.__CONFIG.noticeVersion) || "";

// Has this person accepted the current privacy notice? Remembered for the tab so
// pages do not ask the database every time. If the check itself fails we let
// them in (the notice is a legal courtesy, it must not lock people out of their
// own money by a network blip).
export async function hasConsent(db, userId) {
  const ver = noticeVersion();
  if (!ver) return true;
  const ck = "mt_consent_" + userId + "_" + ver;
  try { if (sessionStorage.getItem(ck) === "1") return true; } catch (e) {}
  const { data, error } = await db.from("user_consents").select("notice_version");
  if (error) return true;
  const ok = (data || []).some((r) => r.notice_version === ver);
  if (ok) { try { sessionStorage.setItem(ck, "1"); } catch (e) {} }
  return ok;
}

export function rememberConsent(userId) {
  try { sessionStorage.setItem("mt_consent_" + userId + "_" + noticeVersion(), "1"); } catch (e) {}
}
