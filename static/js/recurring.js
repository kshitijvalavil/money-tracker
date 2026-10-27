// Recurring bills: pure date + money helpers shared by the pages and the demo
// client. Mirrors recurring_occurrence() in SQL (migration 0007) and
// bot/recurring.py: the schedule is anchored to start_date, so occurrence n is
// start + n × period with the day clamped to the month's length
// (31 Jan → 29 Feb → 31 Mar, never drifting to the 28th/29th).
// Dates are "YYYY-MM-DD" strings handled in UTC so no timezone can shift them.

export const FREQUENCIES = ["weekly", "monthly", "quarterly", "yearly"];
const MONTHS = { monthly: 1, quarterly: 3, yearly: 12 };

const pad = (n) => String(n).padStart(2, "0");
const parts = (s) => String(s).slice(0, 10).split("-").map(Number);
const toUTC = (s) => { const [y, m, d] = parts(s); return Date.UTC(y, m - 1, d); };

export function addDays(date, n) {
  return new Date(toUTC(date) + n * 864e5).toISOString().slice(0, 10);
}

export function daysBetween(from, to) {
  return Math.round((toUTC(to) - toUTC(from)) / 864e5);
}

export function occurrence(start, freq, n) {
  if (freq === "weekly") return addDays(start, 7 * n);
  const k = MONTHS[freq];
  if (!k) throw new Error(`unknown frequency ${freq}`);
  const [y, m, d] = parts(start);
  const total = (m - 1) + k * n;
  const yy = y + Math.floor(total / 12);
  const mm = ((total % 12) + 12) % 12 + 1;
  const last = new Date(Date.UTC(yy, mm, 0)).getUTCDate();
  return `${yy}-${pad(mm)}-${pad(Math.min(d, last))}`;
}

// Smallest n >= minN whose occurrence falls on or after `date`.
export function firstSeqOnOrAfter(start, freq, date, minN = 0) {
  let n = minN;
  if (occurrence(start, freq, n) >= date) return n;
  const days = daysBetween(start, date);
  const guess = freq === "weekly" ? Math.floor(days / 7) : Math.floor(days / 31 / (MONTHS[freq] || 1));
  n = Math.max(minN, guess);
  while (n > minN && occurrence(start, freq, n - 1) >= date) n--;
  while (occurrence(start, freq, n) < date) n++;
  return n;
}

// Rough monthly cost, as the contract defines it.
export function perMonth(amount, freq) {
  const a = Number(amount) || 0;
  return freq === "weekly" ? (a * 52) / 12
    : freq === "monthly" ? a
    : freq === "quarterly" ? a / 3
    : freq === "yearly" ? a / 12 : 0;
}

// Same normalisation as suggest_recurring: lowercase, digits/punctuation out.
export function normaliseName(s) {
  return String(s || "").toLowerCase().replace(/[^\p{L}\s]/gu, " ").replace(/\s+/g, " ").trim();
}

export const FREQ_LABEL = { weekly: "Weekly", monthly: "Monthly", quarterly: "Every 3 months", yearly: "Yearly" };

// "Due today" / "Tomorrow" / "In 3 days" / "Overdue 2 days"
export function dueLabel(daysLeft) {
  const n = Math.abs(daysLeft);
  const days = `${n} ${n === 1 ? "day" : "days"}`;
  if (daysLeft === 0) return "Due today";
  if (daysLeft === 1) return "Tomorrow";
  return daysLeft < 0 ? `Overdue ${days}` : `In ${days}`;
}

const fmtShort = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const fmtLong = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
// "5 Nov", or "5 Nov 2027" when it isn't this year.
export function shortDate(date, todayStr) {
  const t = toUTC(date);
  const sameYear = String(date).slice(0, 4) === String(todayStr || "").slice(0, 4);
  return (sameYear ? fmtShort : fmtLong).format(new Date(t));
}

// Tidy a suggested name for display / pre-filling the form (Manager decision 3):
// drop trailing reference codes and digits, then title-case each word.
// "NETFLIX.COM 9921" -> "Netflix.com", "Airtel recharge #4813" -> "Airtel Recharge".
const REF_TOKEN = /(\d|^#|^[-–:#*/]+$|^(ref|txn|utr|upi|id|no|order)[:.#]?$)/i;
export function tidyName(raw) {
  const toks = String(raw || "").replace(/\s+/g, " ").trim().split(" ");
  while (toks.length > 1 && REF_TOKEN.test(toks[toks.length - 1])) toks.pop();
  const s = toks.join(" ").replace(/\d+/g, "").replace(/\s+/g, " ")
    .replace(/[\s\-–:#*/.,]+$/, "").replace(/^[\s\-–:#*/.,]+/, "").trim();
  return s.split(" ").map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w)).join(" ");
}

// The Bills page hides weaker suggestions (Manager decision 2).
export const MIN_SUGGEST_CONFIDENCE = 0.5;
