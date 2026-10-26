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

// Small toast for errors / confirmations.
export function toast(message, kind = "error") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = "toast show " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3500);
}
