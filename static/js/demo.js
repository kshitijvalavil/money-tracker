// Demo mode: an in-browser stand-in for Supabase so the UI can be explored
// without any setup. Data lives in memory and resets on reload. It mimics only
// the small slice of the supabase-js API that these pages use.

const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
const ym = (d) => d.toISOString().slice(0, 10);

function seed() {
  const now = new Date();
  const thisMonth = now.toISOString().slice(0, 7);
  const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 15).toISOString().slice(0, 7);
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
  const pending_imports = [
    { id: uid(), source: "sbi_alert", direction: "income", amount: 5000,
      occurred_on: day(thisMonth, 5), account_hint: "0001", note: "UPI transfer",
      source_message_id: "demo-1", status: "pending", created_at: new Date().toISOString() },
    { id: uid(), source: "statement", direction: "expense", amount: 450,
      occurred_on: day(thisMonth, 7), account_hint: "0001", note: "POS purchase",
      source_message_id: "demo-2", status: "pending", created_at: new Date().toISOString() },
    { id: uid(), source: "receipt", direction: "expense", amount: 299,
      occurred_on: day(thisMonth, 8), account_hint: null, note: "Zomato order",
      source_message_id: "demo-3", status: "pending", created_at: new Date().toISOString() },
  ];

  return { accounts, categories, transactions, budgets, pending_imports };
}

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

function rpc(s, name, params) {
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
      const key = d.toISOString().slice(0, 7);
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

// ---- a tiny chainable, awaitable query builder ----------------------------
function makeBuilder(store, table) {
  const b = {
    _op: "select", _payload: null, _conflict: null,
    _filters: [], _orders: [], _limit: null,
    select() { this._op = "select"; return this; },
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
          for (const p of list) rows.push({ id: uid(), created_at: new Date().toISOString(), ...p });
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
          for (const r of rows.filter((r) => this._match(r))) Object.assign(r, this._payload);
          return { data: null, error: null };
        }
        if (this._op === "delete") {
          store.data[table] = rows.filter((r) => !this._match(r));
          return { data: null, error: null };
        }
        // select
        let out = rows.filter((r) => this._match(r)).map((r) => enrich(s, table, r));
        for (const [c, asc] of [...this._orders].reverse()) {
          out.sort((x, y) => (x[c] > y[c] ? 1 : x[c] < y[c] ? -1 : 0) * (asc ? 1 : -1));
        }
        if (this._limit != null) out = out.slice(0, this._limit);
        return { data: out, error: null };
      } catch (e) {
        return { data: null, error: { message: String(e) } };
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
    async rpc(name, params) { return { data: rpc(store.data, name, params), error: null }; },
  };
}
