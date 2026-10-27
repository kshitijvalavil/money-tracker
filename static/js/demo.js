// Demo mode: an in-browser stand-in for Supabase so the UI can be explored
// without any setup. Data lives in memory and resets on reload. It mimics only
// the small slice of the supabase-js API that these pages use.

import { occurrence, addDays, daysBetween, normaliseName, FREQUENCIES } from "./recurring.js";

const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
// Local calendar dates, like the app's today() (SB-11). toISOString() is UTC.
const localDate = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function seed() {
  const now = new Date();
  const thisMonth = localDate(now).slice(0, 7);
  const lastMonth = localDate(new Date(now.getFullYear(), now.getMonth() - 1, 15)).slice(0, 7);
  const day = (m, d) => `${m}-${String(d).padStart(2, "0")}`;

  const accounts = [
    { id: uid(), name: "HDFC Savings", type: "bank", opening_balance: 25000 },
    { id: uid(), name: "Cash", type: "cash", opening_balance: 2000 },
    { id: uid(), name: "Credit Card", type: "card", opening_balance: 0 },
  ];
  const A = Object.fromEntries(accounts.map((a) => [a.name, a.id]));

  const cats = [
    ["Salary", "income"], ["Freelance", "income"], ["Gift", "income"], ["Investment", "income"],
    ["Food", "expense"], ["Rent", "expense"], ["Transport", "expense"], ["Bills", "expense"],
    ["Shopping", "expense"], ["Health", "expense"], ["Entertainment", "expense"], ["Other", "expense"],
  ];
  const categories = cats.map(([name, kind]) => ({ id: uid(), name, kind }));
  const C = Object.fromEntries(categories.map((c) => [c.name, c.id]));

  const tx = (occurred_on, amount, type, account, extra = {}) => ({
    id: uid(), occurred_on, amount, type,
    account_id: A[account], to_account_id: null, category_id: null, note: "",
    created_at: new Date().toISOString(), ...extra,
  });

  const transactions = [
    tx(day(thisMonth, 1), 85000, "income", "HDFC Savings", { category_id: C["Salary"], note: "Monthly salary" }),
    tx(day(thisMonth, 3), 18000, "expense", "HDFC Savings", { category_id: C["Rent"], note: "Flat rent" }),
    tx(day(thisMonth, 5), 2450, "expense", "Credit Card", { category_id: C["Food"], note: "Groceries" }),
    tx(day(thisMonth, 6), 600, "expense", "Cash", { category_id: C["Transport"], note: "Auto + metro" }),
    tx(day(thisMonth, 8), 1299, "expense", "Credit Card", { category_id: C["Entertainment"], note: "Streaming + movie" }),
    tx(day(thisMonth, 9), 3200, "expense", "HDFC Savings", { category_id: C["Shopping"], note: "Clothes" }),
    tx(day(thisMonth, 10), 5000, "transfer", "HDFC Savings", { to_account_id: A["Cash"], note: "ATM withdrawal" }),
    tx(day(thisMonth, 12), 1850, "expense", "Credit Card", { category_id: C["Bills"], note: "Electricity" }),
    tx(day(lastMonth, 1), 85000, "income", "HDFC Savings", { category_id: C["Salary"] }),
    tx(day(lastMonth, 4), 18000, "expense", "HDFC Savings", { category_id: C["Rent"] }),
    tx(day(lastMonth, 15), 9000, "expense", "Credit Card", { category_id: C["Food"] }),
  ];

  const budgets = [
    { id: uid(), category_id: C["Food"], monthly_limit: 10000 },
    { id: uid(), category_id: C["Shopping"], monthly_limit: 3000 },
    { id: uid(), category_id: C["Entertainment"], monthly_limit: 2000 },
  ];

  // Sample transactions "detected from email", awaiting review (demo only).
  // The hints/sources are chosen so the Imports page can pre-select a matching
  // account for some rows (HDFC by bank name, Credit Card by hint).
  const pi = (source, direction, amount, d, account_hint, note, n) => ({
    id: uid(), source, direction, amount, occurred_on: day(thisMonth, d), account_hint, note,
    source_message_id: `demo-${n}`, status: "pending", created_at: new Date().toISOString(),
  });
  const pending_imports = [
    pi("hdfc_alert", "income", 5000, 5, "0001", "UPI transfer from Ravi", 1),
    pi("statement", "expense", 450, 7, "card", "POS purchase", 2),
    pi("receipt", "expense", 299, 8, null, "Zomato order", 3),
    pi("hdfc_alert", "expense", 1200, 9, "0001", "Electricity bill autopay", 4),
    pi("receipt", "expense", 186, 11, null, "Uber trip", 5),
  ];

  // Recurring bills, placed relative to today so the demo always shows one
  // overdue bill, one due soon, one auto-posting today and one far off.
  // Each is anchored to a start date a few periods back (next_seq > 0), just
  // as a real bill would be after a few months of use.
  const today = localDate(now);
  const bill = (name, amount, account, category, frequency, dueInDays, back, extra = {}) => {
    const start_date = occurrence(addDays(today, dueInDays), frequency, -back);
    return {
      id: uid(), name, type: "expense", amount, account_id: A[account], category_id: C[category],
      frequency, start_date, next_seq: back, next_due: occurrence(start_date, frequency, back),
      auto_post: false, active: true, source: "manual", created_at: new Date().toISOString(), ...extra,
    };
  };
  const recurring = [
    bill("Flat rent", 18000, "HDFC Savings", "Rent", "monthly", 4, 6),
    bill("Netflix", 649, "Credit Card", "Entertainment", "monthly", -2, 5),
    bill("Gym membership", 500, "Cash", "Health", "weekly", 0, 8, { auto_post: true }),
    bill("Home insurance", 14500, "HDFC Savings", "Bills", "yearly", 40, 2),
  ];

  // History that suggest_recurring() should spot: a monthly Spotify charge on
  // the card and a 28-day phone recharge, neither of which is a bill yet.
  for (let k = 0; k < 4; k++) {
    transactions.push(tx(occurrence(addDays(today, -10), "monthly", -k), 119, "expense", "Credit Card",
      { category_id: C["Entertainment"], note: "Spotify Premium" }));
    transactions.push(tx(addDays(today, -15 - 28 * k), k === 2 ? 309 : 299, "expense", "HDFC Savings",
      { category_id: C["Bills"], note: `Airtel recharge #${4810 + k}` }));
  }

  // Irregular spend that only weakly looks monthly (confidence < 0.5): the
  // Bills page must not suggest it.
  [3, 33, 50, 110].forEach((d, k) => transactions.push(tx(addDays(today, -d), k % 2 ? 180 : 175,
    "expense", "Cash", { category_id: C["Food"], note: "Chai Point" })));

  return { accounts, categories, transactions, budgets, pending_imports, recurring };
}

// Same SQLSTATE-style errors as the real functions.
function fail(code, message) { const e = new Error(message); e.code = code; throw e; }

// ---- recurring bills (mirror migration 0007 / 0008) ------------------------
const todayLocal = () => localDate();
const MAX_AMOUNT = 999999999999.99;       // numeric(14,2)
const median = (xs) => {
  const a = [...xs].sort((x, y) => x - y), m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
};

// Table rules the database enforces (checks + the recurring_before_write
// trigger in 0007). Like the trigger, it recomputes next_due from
// start_date/frequency/next_seq, so clients never send next_due.
function checkRecurring(s, r) {
  const name = String(r.name ?? "").trim();
  if (name.length < 1 || name.length > 80) fail("23514", "name must be 1 to 80 characters");
  if (!["income", "expense"].includes(r.type)) fail("23514", "type must be income or expense");
  if (!(Number(r.amount) > 0)) fail("23514", "amount must be greater than zero");
  if (Number(r.amount) > MAX_AMOUNT) fail("22003", "numeric field overflow");
  r.amount = Math.round(Number(r.amount) * 100) / 100;      // numeric(14,2) rounds to cents
  if (!FREQUENCIES.includes(r.frequency)) fail("23514", "bad frequency");
  if (!r.start_date) fail("23502", "start_date is required");
  if (!(Number.isInteger(r.next_seq) && r.next_seq >= 0)) fail("23514", "next_seq must be >= 0");
  if (!s.accounts.some((a) => a.id === r.account_id)) fail("P0002", `account ${r.account_id} not found`);
  const cat = s.categories.find((c) => c.id === r.category_id);
  if (!cat) fail("P0002", `category ${r.category_id} not found`);
  if (cat.kind !== r.type) fail("22023", `category kind ${cat.kind} does not match bill type ${r.type}`);
  r.next_due = occurrence(r.start_date, r.frequency, r.next_seq);
}

// Mirrors 0008 rules 1-6 (and bot/recurring.py).
function suggestRecurring(s, minOcc = 3) {
  const BANDS = { weekly: [6, 8], monthly: [26, 35], quarterly: [85, 95], yearly: [355, 375] };
  const covered = new Set(s.recurring.map((r) => normaliseName(r.name)));
  const groups = new Map();
  for (const t of s.transactions) {
    if (t.type !== "income" && t.type !== "expense") continue;
    const key = normaliseName(t.note);
    if (!key || covered.has(key)) continue;
    const gk = `${key}\u0000${t.type}`;
    if (!groups.has(gk)) groups.set(gk, { key, type: t.type, rows: [] });
    groups.get(gk).rows.push(t);
  }
  const out = [];
  for (const { key, type, rows } of groups.values()) {
    if (rows.length < minOcc) continue;                                   // 2
    const med = median(rows.map((t) => Number(t.amount)));
    const kept = rows.filter((t) => Math.abs(Number(t.amount) - med) <= 0.1 * med);   // 3
    const dates = [...new Set(kept.map((t) => t.occurred_on))].sort();
    if (dates.length < minOcc) continue;                                  // 4
    const gaps = dates.slice(1).map((d, i) => daysBetween(dates[i], d));
    const g = median(gaps);                                               // 5
    const frequency = Object.keys(BANDS).find((f) => g >= BANDS[f][0] && g <= BANDS[f][1]);
    if (!frequency) continue;
    const [lo, hi] = BANDS[frequency];
    const inBand = gaps.filter((x) => x >= lo && x <= hi).length;
    // Decision 16: skip groups that are mostly off-amount or off-rhythm.
    if (2 * kept.length < rows.length) continue;
    if (3 * inBand < 2 * gaps.length) continue;
    const latest = [...kept].sort((a, b) => (a.occurred_on + a.id < b.occurred_on + b.id ? -1 : 1)).pop();   // 6
    const last_seen = dates[dates.length - 1];
    let k = 1;
    while (occurrence(dates[0], frequency, k) <= last_seen) k++;
    out.push({
      key, name: latest.note, type, amount: Math.round(median(kept.map((t) => Number(t.amount))) * 100) / 100,
      frequency, account_id: latest.account_id, category_id: latest.category_id, last_seen,
      next_guess: occurrence(dates[0], frequency, k), occurrences: dates.length,
      confidence: Math.round((inBand / gaps.length) * (kept.length / rows.length) * 100) / 100,
    });
  }
  out.sort((a, b) => (b.confidence - a.confidence) || (b.occurrences - a.occurrences)
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0) || (a.type < b.type ? -1 : 1));
  return out.map(({ key, ...row }) => row);
}

function recurringRpc(s, name, p = {}) {
  const today = todayLocal();
  const find = (id) => s.recurring.find((r) => r.id === id) || fail("P0002", `recurring ${id} not found`);
  const advance = (r) => { r.next_seq += 1; r.next_due = occurrence(r.start_date, r.frequency, r.next_seq); };
  // Post occurrence next_seq once (unique on recurring_id + recurring_seq).
  const post = (r, on) => {
    const existing = s.transactions.find((t) => t.recurring_id === r.id && t.recurring_seq === r.next_seq);
    if (existing) { advance(r); return { id: existing.id, created: false }; }
    const t = {
      id: uid(), occurred_on: on, amount: r.amount, type: r.type, account_id: r.account_id,
      to_account_id: null, category_id: r.category_id, note: r.name, source: "recurring",
      recurring_id: r.id, recurring_seq: r.next_seq, created_at: new Date().toISOString(),
    };
    s.transactions.push(t);
    advance(r);
    return { id: t.id, created: true };
  };

  if (name === "recurring_occurrence") {
    if (!p.p_start || p.p_freq == null || p.p_n == null) fail("22023", "start date, frequency and occurrence number are required");
    if (p.p_n < 0) fail("22023", `occurrence number must be >= 0, got ${p.p_n}`);
    if (!FREQUENCIES.includes(p.p_freq)) fail("22023", `unknown frequency ${p.p_freq}`);
    return occurrence(p.p_start, p.p_freq, p.p_n);
  }
  if (name === "mark_recurring_paid") return post(find(p.p_id), p.p_paid_on || today).id;
  if (name === "skip_recurring") { advance(find(p.p_id)); return null; }
  if (name === "reschedule_recurring") {
    // Decisions 12 and 15: shift this bill's posted transactions below zero by
    // greatest(next_seq, max posted seq + 1) so history stays linked and unique,
    // then restart the schedule at the picked date (trigger recomputes next_due).
    if (!p.p_start) fail("22023", "start date is required");
    if (!FREQUENCIES.includes(p.p_frequency)) fail("22023", `unknown frequency ${p.p_frequency}`);
    const r = find(p.p_id);
    const mine = s.transactions.filter((t) => t.recurring_id === r.id);
    const shift = Math.max(r.next_seq, mine.length ? Math.max(...mine.map((t) => t.recurring_seq)) + 1 : 0);
    for (const t of mine) t.recurring_seq -= shift;
    const next = { ...r, start_date: p.p_start, frequency: p.p_frequency, next_seq: 0 };
    checkRecurring(s, next);
    Object.assign(r, next);
    return null;
  }
  if (name === "post_due_recurring") {
    const asOf = p.p_as_of || today;
    let posted = 0;
    for (const r of s.recurring.filter((x) => x.active && x.auto_post)) {
      for (let i = 0; i < 60 && r.next_due <= asOf; i++) if (post(r, r.next_due).created) posted++;
    }
    return posted;
  }
  if (name === "upcoming_recurring") {
    const asOf = p.p_as_of || today;
    const until = addDays(asOf, p.p_days ?? 30);
    return s.recurring.filter((r) => r.active && r.next_due <= until)
      .sort((a, b) => (a.next_due < b.next_due ? -1 : a.next_due > b.next_due ? 1
        : a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
      .map((r) => {
        const days_left = daysBetween(asOf, r.next_due);
        return { id: r.id, name: r.name, type: r.type, amount: r.amount, next_due: r.next_due,
          days_left, overdue: days_left < 0, auto_post: r.auto_post };
      });
  }
  if (name === "suggest_recurring") {
    const min = p.p_min_occurrences ?? 3;
    if (min < 2) fail("22023", "p_min_occurrences must be at least 2");
    return suggestRecurring(s, min);
  }
  return undefined;
}
const RECURRING_RPCS = ["recurring_occurrence", "mark_recurring_paid", "skip_recurring", "reschedule_recurring",
  "post_due_recurring", "upcoming_recurring", "suggest_recurring"];

// ---- helpers that mirror the SQL functions --------------------------------
function accountBalances(s) {
  return s.accounts.map((a) => {
    let bal = Number(a.opening_balance);
    for (const t of s.transactions) {
      if (t.account_id === a.id && t.type === "income") bal += Number(t.amount);
      if (t.account_id === a.id && t.type === "expense") bal -= Number(t.amount);
      if (t.account_id === a.id && t.type === "transfer") bal -= Number(t.amount);
      if (t.to_account_id === a.id && t.type === "transfer") bal += Number(t.amount);
    }
    return { id: a.id, name: a.name, type: a.type, balance: bal };
  }).sort((x, y) => x.name.localeCompare(y.name));
}
const inMonth = (t, m) => t.occurred_on.slice(0, 7) === m;
const sum = (arr) => arr.reduce((n, t) => n + Number(t.amount), 0);

// Mirrors the approve_import / reject_import SQL functions: approving creates
// the transaction and marks the import approved in one step, returning the new
// transaction id. Throwing becomes a supabase-style { error } in the client.
function importRpc(s, name, params) {
  const imp = s.pending_imports.find((p) => p.id === params?.p_import_id);
  if (!imp) fail("P0002", `import ${params?.p_import_id} not found`);
  if (imp.status !== "pending") fail("55000", `import ${imp.id} is already ${imp.status}`);
  if (name === "reject_import") { imp.status = "rejected"; return null; }
  if (!s.accounts.some((a) => a.id === params.p_account_id)) {
    fail("P0002", `account ${params.p_account_id} not found`);
  }
  const cat = s.categories.find((c) => c.id === params.p_category_id);
  if (!cat) fail("P0002", `category ${params.p_category_id} not found`);
  if (cat.kind !== imp.direction) {
    fail("22023", `category kind ${cat.kind} does not match import direction ${imp.direction}`);
  }
  // Same source email already imported: reuse that transaction (dedup).
  let t = imp.source_message_id && s.transactions.find((x) => x.source_message_id === imp.source_message_id);
  if (!t) {
    t = {
      id: uid(), occurred_on: imp.occurred_on, amount: imp.amount, type: imp.direction,
      account_id: params.p_account_id, to_account_id: null, category_id: params.p_category_id || null,
      note: imp.note || "", source: imp.source, source_message_id: imp.source_message_id,
      created_at: new Date().toISOString(),
    };
    s.transactions.push(t);
  }
  imp.status = "approved";
  return t.id;
}

function rpc(s, name, params) {
  if (name === "approve_import" || name === "reject_import") return importRpc(s, name, params);
  if (RECURRING_RPCS.includes(name)) return recurringRpc(s, name, params);
  const m = params?.p_month;
  if (name === "account_balances") return accountBalances(s);
  if (name === "dashboard_summary") {
    const total = accountBalances(s).reduce((n, a) => n + a.balance, 0);
    return [{
      total_balance: total,
      month_income: sum(s.transactions.filter((t) => t.type === "income" && inMonth(t, m))),
      month_expense: sum(s.transactions.filter((t) => t.type === "expense" && inMonth(t, m))),
    }];
  }
  if (name === "spending_by_category") {
    const byCat = {};
    for (const t of s.transactions.filter((t) => t.type === "expense" && inMonth(t, m))) {
      const c = s.categories.find((c) => c.id === t.category_id);
      if (c) byCat[c.name] = (byCat[c.name] || 0) + Number(t.amount);
    }
    return Object.entries(byCat).map(([category, total]) => ({ category, total }))
      .sort((a, b) => b.total - a.total);
  }
  if (name === "budget_status") {
    return s.budgets.map((b) => {
      const c = s.categories.find((c) => c.id === b.category_id);
      const spent = sum(s.transactions.filter(
        (t) => t.type === "expense" && t.category_id === b.category_id && inMonth(t, m)));
      return { category: c?.name || "—", monthly_limit: Number(b.monthly_limit), spent };
    });
  }
  if (name === "monthly_totals") {
    const out = [];
    const base = new Date();
    for (let i = 11; i >= 0; i--) {
      const d = new Date(base.getFullYear(), base.getMonth() - i, 1);
      const key = localDate(d).slice(0, 7);
      out.push({
        month: key,
        income: sum(s.transactions.filter((t) => t.type === "income" && inMonth(t, key))),
        expense: sum(s.transactions.filter((t) => t.type === "expense" && inMonth(t, key))),
      });
    }
    return out;
  }
  return [];
}

// ---- row enrichment (embedded relations the pages ask for) ----------------
function enrich(s, table, row) {
  if (table === "transactions") {
    const cat = s.categories.find((c) => c.id === row.category_id);
    const from = s.accounts.find((a) => a.id === row.account_id);
    const to = s.accounts.find((a) => a.id === row.to_account_id);
    return { ...row, categories: cat ? { name: cat.name } : null,
      accounts: from ? { name: from.name } : null,
      from_acc: from ? { name: from.name } : null,
      to_acc: to ? { name: to.name } : null };
  }
  if (table === "budgets") {
    const cat = s.categories.find((c) => c.id === row.category_id);
    return { ...row, categories: cat ? { name: cat.name } : null };
  }
  return { ...row };
}

// Column defaults the database would fill in.
const DEFAULTS = {
  recurring: { next_seq: 0, auto_post: false, active: true, source: "manual" },
};

// ---- a tiny chainable, awaitable query builder ----------------------------
function makeBuilder(store, table) {
  const b = {
    _op: "select", _payload: null, _conflict: null,
    _filters: [], _orders: [], _limit: null,
    _count: null, _head: false,
    select(_cols, o) { this._op = "select"; this._count = o?.count || null; this._head = !!o?.head; return this; },
    insert(p) { this._op = "insert"; this._payload = p; return this; },
    update(p) { this._op = "update"; this._payload = p; return this; },
    upsert(p, o) { this._op = "upsert"; this._payload = p; this._conflict = o?.onConflict; return this; },
    delete() { this._op = "delete"; return this; },
    eq(c, v) { this._filters.push((r) => String(r[c]) === String(v)); return this; },
    gte(c, v) { this._filters.push((r) => r[c] >= v); return this; },
    lte(c, v) { this._filters.push((r) => r[c] <= v); return this; },
    or(expr) {
      const tests = expr.split(",").map((p) => {
        const [col, , val] = p.split(".");
        return (r) => String(r[col]) === String(val);
      });
      this._filters.push((r) => tests.some((t) => t(r)));
      return this;
    },
    order(c, o) { this._orders.push([c, o?.ascending !== false]); return this; },
    limit(n) { this._limit = n; return this; },
    _match(r) { return this._filters.every((f) => f(r)); },
    _run() {
      const s = store.data;
      const rows = s[table];
      try {
        if (this._op === "insert") {
          const list = Array.isArray(this._payload) ? this._payload : [this._payload];
          for (const p of list) {
            const row = { id: uid(), created_at: new Date().toISOString(), ...(DEFAULTS[table] || {}), ...p };
            if (table === "recurring") checkRecurring(s, row);
            rows.push(row);
          }
          return { data: null, error: null };
        }
        if (this._op === "upsert") {
          const list = Array.isArray(this._payload) ? this._payload : [this._payload];
          for (const p of list) {
            const key = (this._conflict || "").split(",").map((k) => k.trim()).filter((k) => k !== "user_id");
            const existing = rows.find((r) => key.every((k) => String(r[k]) === String(p[k])));
            if (existing) Object.assign(existing, p);
            else rows.push({ id: uid(), created_at: new Date().toISOString(), ...p });
          }
          return { data: null, error: null };
        }
        if (this._op === "update") {
          const hits = rows.filter((r) => this._match(r));
          const next = hits.map((r) => ({ ...r, ...this._payload }));
          if (table === "recurring") next.forEach((r) => checkRecurring(s, r));   // all or nothing
          hits.forEach((r, i) => Object.assign(r, next[i]));
          return { data: null, error: null };
        }
        if (this._op === "delete") {
          if (table === "recurring") {        // transactions.recurring_id: on delete set null
            const gone = new Set(rows.filter((r) => this._match(r)).map((r) => r.id));
            s.transactions.forEach((t) => { if (gone.has(t.recurring_id)) t.recurring_id = null; });
          }
          store.data[table] = rows.filter((r) => !this._match(r));
          return { data: null, error: null };
        }
        // select
        let out = rows.filter((r) => this._match(r)).map((r) => enrich(s, table, r));
        for (const [c, asc] of [...this._orders].reverse()) {
          out.sort((x, y) => (x[c] > y[c] ? 1 : x[c] < y[c] ? -1 : 0) * (asc ? 1 : -1));
        }
        const count = this._count ? out.length : null;
        if (this._limit != null) out = out.slice(0, this._limit);
        return { data: this._head ? null : out, error: null, count };
      } catch (e) {
        return { data: null, error: { message: e.message || String(e), code: e.code || null } };
      }
    },
    then(resolve) { resolve(this._run()); },
  };
  return b;
}

export function createDemoClient() {
  const store = { data: seed() };
  const user = { id: "demo-user", email: "demo@example.com" };
  return {
    auth: {
      async getUser() { return { data: { user }, error: null }; },
      async signInWithPassword() { return { data: { user }, error: null }; },
      async signOut() { return { error: null }; },
    },
    from(table) { return makeBuilder(store, table); },
    async rpc(name, params) {
      // Write RPCs get a short fake network delay so loading states are visible.
      if (["approve_import", "reject_import", "mark_recurring_paid", "skip_recurring"].includes(name)) {
        await new Promise((r) => setTimeout(r, 350));
      }
      try {
        return { data: rpc(store.data, name, params), error: null };
      } catch (e) {
        return { data: null, error: { message: e.message || String(e), code: e.code || null } };
      }
    },
  };
}
