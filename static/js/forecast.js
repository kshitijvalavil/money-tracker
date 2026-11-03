// Cash-flow forecast maths (Sprint 12). Pure functions over "YYYY-MM-DD"
// strings and numbers, so they can be unit-tested and reused. The browser
// feeds in the current balance, the recurring bills and a daily-spend estimate;
// these turn that into a day-by-day projection, the tightest day, and a safe
// daily spend. Money that moves between your own accounts (transfers) is left
// out: it never changes your total balance.

import { occurrence, addDays, daysBetween, perMonth } from "./recurring.js";

// Expand active recurring bills into signed, dated amounts between two dates
// (inclusive). Income is positive, expense negative. Each bill is walked from
// its next occurrence forward, so a monthly bill lands once a month, a weekly
// one every week, for as far as the horizon reaches.
export function expandScheduled(recurring, fromDate, toDate) {
  const out = [];
  for (const r of recurring || []) {
    if (r.active === false) continue;
    const sign = r.type === "income" ? 1 : -1;
    let seq = Number(r.next_seq) || 0;
    for (let i = 0; i < 400; i++) {
      const date = occurrence(r.start_date, r.frequency, seq + i);
      if (date < fromDate) continue;
      if (date > toDate) break;
      out.push({ date, amount: sign * Number(r.amount), name: r.name, type: r.type });
    }
  }
  return out.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

// Average daily discretionary spend from recent history: everyday expenses
// (not bills posted by a recurring schedule) over the last `lookback` days.
export function estimateDailySpend(transactions, asOf, lookback = 60) {
  const from = addDays(asOf, -lookback);
  let total = 0;
  for (const t of transactions || []) {
    if (t.type !== "expense") continue;
    if (t.source === "recurring") continue;          // bills are projected separately
    if (t.occurred_on <= from || t.occurred_on > asOf) continue;
    total += Number(t.amount);
  }
  return total / lookback;
}

// Reconstruct the balance at the end of each day from the month's start up to
// and including `asOf`, by walking backwards from today's balance. Only income
// and expense move the total; transfers net to zero across your accounts.
export function actualPath(balanceNow, transactions, monthStart, asOf) {
  const net = {};                                    // date -> income - expense
  for (const t of transactions || []) {
    if (t.occurred_on < monthStart || t.occurred_on > asOf) continue;
    const d = t.occurred_on;
    if (t.type === "income") net[d] = (net[d] || 0) + Number(t.amount);
    else if (t.type === "expense") net[d] = (net[d] || 0) - Number(t.amount);
  }
  const days = [];
  for (let d = monthStart; d <= asOf; d = addDays(d, 1)) days.push(d);
  // Balance at end of asOf is balanceNow; step backwards subtracting each day's net.
  const endBal = {};
  let bal = balanceNow;
  for (let i = days.length - 1; i >= 0; i--) {
    endBal[days[i]] = bal;
    bal -= net[days[i]] || 0;                         // becomes the prior day's end balance
  }
  return days.map((d) => ({ date: d, balance: Math.round(endBal[d]) }));
}

// Project the balance forward from the day after `asOf` to `horizonEnd`,
// subtracting the daily spend estimate and applying scheduled items on their
// dates. The first point is `asOf` itself (today's balance), so the projected
// line joins the actual line.
export function projectForward(balanceNow, asOf, horizonEnd, dailySpend, scheduled) {
  const byDate = {};
  for (const s of scheduled) byDate[s.date] = (byDate[s.date] || 0) + s.amount;
  const out = [{ date: asOf, balance: Math.round(balanceNow) }];
  let bal = balanceNow;
  for (let d = addDays(asOf, 1); d <= horizonEnd; d = addDays(d, 1)) {
    bal -= dailySpend;
    bal += byDate[d] || 0;
    out.push({ date: d, balance: Math.round(bal) });
  }
  return out;
}

// The lowest projected day over the future window (after today).
export function tightestDay(projected) {
  let low = null;
  for (const p of projected) {
    if (p.date === projected[0].date) continue;      // skip today
    if (!low || p.balance < low.balance) low = p;
  }
  return low;
}

// The largest even daily discretionary spend that keeps every remaining day's
// balance at or above zero, given only the scheduled (bills/income) movements.
// For each future day d, balance without discretionary spend is schedBal[d];
// spending s each day costs s × (days elapsed), so s ≤ schedBal[d] / elapsed[d].
export function safeDailySpend(balanceNow, asOf, horizonEnd, scheduled) {
  const byDate = {};
  for (const s of scheduled) byDate[s.date] = (byDate[s.date] || 0) + s.amount;
  let bal = balanceNow, elapsed = 0, limit = Infinity;
  for (let d = addDays(asOf, 1); d <= horizonEnd; d = addDays(d, 1)) {
    bal += byDate[d] || 0;
    elapsed += 1;
    limit = Math.min(limit, bal / elapsed);
  }
  if (!isFinite(limit)) return 0;
  return Math.max(0, Math.floor(limit));
}

// A light three-month outlook: this month's projected end balance, then two
// more months added at the recurring net (per-month income − expenses) minus
// the estimated monthly discretionary spend. Clearly an estimate.
export function monthlyOutlook(monthEndBalance, asOf, recurring, dailySpend, months = 3) {
  let net = 0;
  for (const r of recurring || []) {
    if (r.active === false) continue;
    const m = perMonth(Number(r.amount), r.frequency);
    net += r.type === "income" ? m : -m;
  }
  net -= dailySpend * 30.4;                           // average month length
  const out = [];
  const [y, mo] = asOf.split("-").map(Number);
  let bal = monthEndBalance;
  for (let i = 0; i < months; i++) {
    const d = new Date(y, mo - 1 + i + 1, 0);         // last day of month asOf+i
    const label = d.toLocaleDateString("en-IN", { month: "short" });
    out.push({ label, balance: Math.round(bal) });
    bal += net;
  }
  return out;
}
