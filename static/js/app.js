// Shared helpers used by every page.
// window.__CONFIG is set in base.html from the server's Supabase settings.

import { createDemoClient } from "./demo.js";

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

// Format money the Indian way: ₹1,23,456.00
const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
export const money = (n) => inr.format(Number(n) || 0);

// Current month as YYYY-MM
export const thisMonth = () => new Date().toISOString().slice(0, 7);
// Today as YYYY-MM-DD
export const today = () => new Date().toISOString().slice(0, 10);

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

// Wire up a sidebar toggle button; keeps its icon/label in sync.
export function initThemeToggle(btn, iconEl, labelEl) {
  const paint = () => {
    const dark = currentTheme() === "dark";
    if (iconEl) iconEl.textContent = dark ? "☀️" : "🌙";
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
// The shell renders <a data-nav="imports"> with an empty #importsBadge. These
// keep the count in sync; the Imports page calls refreshImportsBadge() after
// every approve/reject.
export function setImportsBadge(n) {
  const badge = document.getElementById("importsBadge");
  const link = document.querySelector('[data-nav="imports"]');
  if (!badge || !link) return;
  const count = Math.max(0, Number(n) || 0);
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.hidden = count === 0;
  if (count) link.setAttribute("aria-label", `Email imports, ${count} waiting for review`);
  else link.removeAttribute("aria-label");
}

export async function refreshImportsBadge() {
  if (!db) return;
  const { count, error } = await db.from("pending_imports")
    .select("id", { count: "exact", head: true })
    .eq("status", "pending");
  if (error) return; // The badge is only a hint; never shout about it.
  setImportsBadge(count);
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
        const { data, error } = await db.rpc("post_due_recurring");
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
