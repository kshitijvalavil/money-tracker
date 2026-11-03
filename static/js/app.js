// Shared helpers used by every page.
// window.__CONFIG is set in base.html from the server's Supabase settings.

import { createDemoClient } from "./demo.js";
import { computeAlerts, withDefaults } from "./alerts.js";

const CONFIG = window.__CONFIG || {};
export const DEMO = !!CONFIG.demo;

// One data client for the whole app. In demo mode it's an in-memory stand-in;
// otherwise it's the real Supabase client (supabase-js is loaded as a global,
// window.supabase, by the UMD script in base.html).
export const db = DEMO
  ? createDemoClient()
  : (CONFIG.url && CONFIG.key)
    ? window.supabase.createClient(CONFIG.url, CONFIG.key)
    : null;

// Demo only: expose the in-memory client so it can be poked at from devtools
// and browser tests. Never set for a real Supabase client.
if (DEMO) window.__demoDb = db;

// Headers that prove who's signed in, for our own /api calls that need it.
// The demo client has no session, so demo calls go without one.
export async function authHeaders() {
  if (DEMO || !db?.auth?.getSession) return {};
  try {
    const { data } = await db.auth.getSession();
    const token = data?.session?.access_token;
    return token ? { Authorization: "Bearer " + token } : {};
  } catch (e) {
    return {};
  }
}

// Format money the Indian way: ₹1,23,456.00
const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
export const money = (n) => inr.format(Number(n) || 0);

// Sprint 13 number polish ---------------------------------------------------
// Whole rupees, Indian grouping, no paise: ₹1,23,456 (for summaries/tiles).
const inrWhole = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR",
  minimumFractionDigits: 0, maximumFractionDigits: 0,
});
export const moneyWhole = (n) => inrWhole.format(Math.round(Number(n) || 0));

// Compact Indian units for big numbers on tiles: ₹12,480 · ₹2.48L · ₹1.25Cr.
// Keeps the ₹ symbol and a true minus for negatives. Below ₹1,00,000 it falls
// back to whole rupees so small amounts stay exact and legible.
export function moneyCompact(n) {
  const v = Number(n) || 0;
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  const trim = (x) => String(Number(x.toFixed(2))); // 2.50 -> "2.5", 2.00 -> "2"
  if (a >= 1e7) return `${sign}₹${trim(a / 1e7)}Cr`;
  if (a >= 1e5) return `${sign}₹${trim(a / 1e5)}L`;
  return sign + moneyWhole(a);
}

// Format an amount with a true minus sign for negatives (− not -), using the
// given formatter (whole rupees by default).
export const signedMoney = (n, fmt = moneyWhole) =>
  (Number(n) < 0 ? "−" + fmt(Math.abs(n)) : fmt(n));

// Current month as YYYY-MM
// Dates use the browser's local calendar day (SB-11): toISOString() is UTC, so
// in India it gave yesterday's date between 00:00 and 05:30.
export const localDate = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const thisMonth = () => localDate().slice(0, 7);
// Today as YYYY-MM-DD
export const today = () => localDate();

// Escape text before putting it in HTML.
export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Redirect to the login page if there is no signed-in user. Returns the user,
// or null (after redirecting) when signed out. Pages call this on load.
export async function requireAuth() {
  if (!db) return null; // not configured; base.html already shows a notice
  const { data } = await db.auth.getUser();
  if (!data.user) {
    window.location.href = "/login";
    return null;
  }
  return data.user;
}

export async function signOut() {
  if (DEMO) { toast("Sign-out is disabled in demo mode.", "ok"); return; }
  if (db) await db.auth.signOut();
  window.location.href = "/login";
}

// --- Day / night theme -----------------------------------------------------
// The current theme is applied to <html data-theme> in base.html before paint.
export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

export function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("theme", theme); } catch (e) {}
}

// Clean line icons (no emoji) for the theme toggle.
const SUN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="18" height="18" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

// Wire up a sidebar toggle button; keeps its icon/label in sync.
export function initThemeToggle(btn, iconEl, labelEl) {
  const paint = () => {
    const dark = currentTheme() === "dark";
    if (iconEl) iconEl.innerHTML = dark ? SUN_SVG : MOON_SVG;
    if (labelEl) labelEl.textContent = dark ? "Day mode" : "Night mode";
  };
  paint();
  if (btn) btn.onclick = () => { setTheme(currentTheme() === "dark" ? "light" : "dark"); paint(); };
}

// Small toast for errors / confirmations.
export function toast(message, kind = "error") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-atomic", "true");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = "toast show " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3500);
}

// --- Pending email imports badge (sidebar) ---------------------------------
// The shell's Transactions link carries data-nav="imports" and an empty
// #importsBadge (email review lives under Transactions since Sprint 6); the
// "From email" tab has its own count. These keep both in sync; the imports
// view calls refreshImportsBadge() after every approve/reject.
export function setImportsBadge(n) {
  const badge = document.getElementById("importsBadge");
  const link = document.querySelector('[data-nav="imports"]');
  if (!badge || !link) return;
  const count = Math.max(0, Number(n) || 0);
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.hidden = count === 0;
  if (count) link.setAttribute("aria-label", `Transactions, ${count} from email waiting for review`);
  else link.removeAttribute("aria-label");
  document.querySelectorAll("[data-imports-count]").forEach((el) => {
    el.textContent = badge.textContent;
    el.hidden = count === 0;
  });
}

export async function refreshImportsBadge() {
  if (!db) return;
  const { count, error } = await db.from("pending_imports")
    .select("id", { count: "exact", head: true })
    .eq("status", "pending");
  if (error) return; // The badge is only a hint; never shout about it.
  setImportsBadge(count);
}

// --- Alerts badge (sidebar + topbar bell) ----------------------------------
// Sprint 13: the bell shows how many alerts you haven't looked at yet. Alerts
// are computed on the device, so this just runs the same cheap queries and
// counts the ones whose id isn't in the "seen" set the Alerts page writes.
const ALERTS_SEEN_KEY = "alertsSeen";
function seenAlerts() {
  try { return new Set(JSON.parse(localStorage.getItem(ALERTS_SEEN_KEY) || "[]")); }
  catch (e) { return new Set(); }
}
export function setAlertsBadge(n) {
  const count = Math.max(0, Number(n) || 0);
  const side = document.getElementById("alertsBadge");
  if (side) {
    side.textContent = count > 99 ? "99+" : String(count);
    side.hidden = count === 0;
  }
  const top = document.getElementById("alertsBadgeTop");
  if (top) top.hidden = count === 0;
  const bell = document.querySelector(".topbar-bell");
  if (bell) {
    if (count) bell.setAttribute("aria-label", `Alerts, ${count} new`);
    else bell.setAttribute("aria-label", "Alerts");
  }
}
export async function refreshAlertsBadge() {
  if (!db) return;
  try {
    const t = new Date();
    const month = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}`;
    const since = (() => { const d = new Date(t.getFullYear(), t.getMonth() - 3, 1);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`; })();
    const [bal, bud, bills, goals, tx, cats, pr] = await Promise.all([
      db.rpc("account_balances"),
      db.rpc("budget_status"),
      db.rpc("upcoming_recurring", { p_days: 30, p_as_of: today() }),
      db.rpc("goal_progress"),
      db.from("transactions").select("id, occurred_on, amount, type, category_id").gte("occurred_on", since),
      db.from("categories").select("id, name, kind"),
      db.from("user_prefs").select("alert_prefs").limit(1),
    ]);
    if (bal.error) return;
    const prefs = withDefaults(pr.data && pr.data[0] ? pr.data[0].alert_prefs : null);
    const alerts = computeAlerts({
      balances: bal.data || [], budgets: bud.data || [], bills: bills.data || [],
      goals: goals.data || [], txns: tx.data || [], categories: cats.data || [], month,
    }, prefs);
    const seen = seenAlerts();
    setAlertsBadge(alerts.filter((a) => !seen.has(a.id)).length);
  } catch (e) { /* the badge is only a hint */ }
}

// --- Recurring bills: post due auto-post bills once per page load ----------
// No server cron: the shell calls this on every page load (post_due_recurring
// is idempotent). Pages that show bills or balances can `await postDueBills()`
// first so they render after posting; every caller shares one request.
let postedThisLoad = null;
export function postDueBills() {
  if (!db) return Promise.resolve(0);
  if (!postedThisLoad) {
    postedThisLoad = (async () => {
      try {
        const { data, error } = await db.rpc("post_due_recurring", { p_as_of: today() });
        if (error) return 0;              // e.g. migration not applied yet: stay quiet
        const n = Number(data) || 0;
        if (n > 0) {
          toast(`Posted ${n} ${n === 1 ? "bill" : "bills"}.`, "ok");
          window.dispatchEvent(new CustomEvent("bills:posted", { detail: { count: n } }));
        }
        return n;
      } catch (e) {
        return 0;
      }
    })();
  }
  return postedThisLoad;
}
