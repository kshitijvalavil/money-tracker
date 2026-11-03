// Sprint 13 — "Ask your money".
// A plain-language question box answered entirely on the device from the user's
// own rows. No network call, no AI service, no paid API key: just pattern
// matching over transactions, bills, balances and goals. Pure and testable.
//
// answerQuestion(question, data) -> {
//   ok, question, title, value, sub, breakdown:[{label,val}], spark:[n], sparkDir, note
// } on success, or { ok:false, question, message } when nothing matched.

const MONTHS = ["january","february","march","april","may","june",
  "july","august","september","october","november","december"];

// Category synonyms so "dining"/"groceries" find the Food category, etc.
const SYNONYMS = {
  food: ["food","dining","eat","eating","restaurant","restaurants","groceries","grocery","swiggy","zomato"],
  transport: ["transport","travel","cab","cabs","auto","metro","uber","ola","fuel","petrol"],
  rent: ["rent","house"],
  bills: ["bills","bill","electricity","water","utilities","utility"],
  shopping: ["shopping","clothes","amazon","flipkart"],
  entertainment: ["entertainment","movies","movie","streaming","netflix","fun"],
  health: ["health","medical","doctor","medicine","pharmacy"],
};

const num = (x) => Number(x) || 0;
const ym = (d) => String(d).slice(0, 7);
function inr(n) {
  const v = Math.round(num(n));
  return (v < 0 ? "−" : "") + "₹" + Math.abs(v).toLocaleString("en-IN");
}
function monthName(key) {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}
function addMonths(key, delta) {
  let [y, m] = key.split("-").map(Number);
  const idx = (y * 12 + (m - 1)) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

// Parse "last month" / "this month" / a named month out of the text.
function resolveMonth(q, thisKey) {
  if (/\blast month\b/.test(q)) return { key: addMonths(thisKey, -1), word: "last month" };
  if (/\bthis month\b|\bso far\b/.test(q)) return { key: thisKey, word: "this month" };
  for (let i = 0; i < 12; i++) {
    if (new RegExp(`\\b${MONTHS[i]}\\b`).test(q)) {
      // Most recent occurrence of that month, not in the future.
      const [ty, tm] = thisKey.split("-").map(Number);
      let y = ty;
      if (i + 1 > tm) y = ty - 1;
      return { key: `${y}-${String(i + 1).padStart(2, "0")}`, word: MONTHS[i][0].toUpperCase() + MONTHS[i].slice(1) };
    }
  }
  return null;
}

// Parse an amount like "20000", "20k", "₹20,000", "1.5 lakh", "2L".
function parseAmount(q) {
  let m = q.match(/(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(k|thousand|l|lakh|lakhs|cr|crore|crores)?/i);
  if (!m) return null;
  let v = parseFloat(m[1].replace(/,/g, ""));
  if (isNaN(v)) return null;
  const unit = (m[2] || "").toLowerCase();
  if (unit === "k" || unit === "thousand") v *= 1e3;
  else if (unit.startsWith("l")) v *= 1e5;
  else if (unit.startsWith("cr")) v *= 1e7;
  return v;
}

// Find a category id whose name (or a synonym) appears in the question.
function matchCategory(q, categories) {
  for (const c of categories || []) {
    if (c.kind && c.kind !== "expense") continue;
    const nm = String(c.name || "").toLowerCase();
    if (nm && q.includes(nm)) return c;
  }
  for (const [canon, words] of Object.entries(SYNONYMS)) {
    if (words.some((w) => new RegExp(`\\b${w}\\b`).test(q))) {
      const c = (categories || []).find((c) => String(c.name).toLowerCase() === canon);
      if (c) return c;
    }
  }
  return null;
}

// Sum of expenses in a category for a given month.
function catMonth(txns, catId, key) {
  return (txns || []).reduce((n, t) =>
    (t.type === "expense" && t.category_id === catId && ym(t.occurred_on) === key) ? n + num(t.amount) : n, 0);
}
function monthExpense(txns, key) {
  return (txns || []).reduce((n, t) => (t.type === "expense" && ym(t.occurred_on) === key) ? n + num(t.amount) : n, 0);
}
function monthIncome(txns, key) {
  return (txns || []).reduce((n, t) => (t.type === "income" && ym(t.occurred_on) === key) ? n + num(t.amount) : n, 0);
}

export function suggestedQuestions() {
  return [
    "How much did I spend on food last month?",
    "What's my biggest subscription?",
    "Can I afford a ₹20,000 trip?",
    "Am I saving more than last month?",
    "Where did my money go this month?",
  ];
}

export function answerQuestion(question, data = {}) {
  const q = String(question || "").toLowerCase().trim();
  const txns = data.txns || [];
  const cats = data.categories || [];
  const thisKey = ym(data.today || new Date().toISOString());
  const miss = (message) => ({ ok: false, question, message });
  if (!q) return miss("Ask about your spending, saving, bills or balance.");

  // --- Biggest subscription / recurring bill ---
  if (/\b(biggest|largest|most expensive|priciest)\b/.test(q) &&
      /\b(subscription|subscriptions|recurring|bill|bills)\b/.test(q)) {
    const subs = (data.recurring || []).filter((r) => r.active !== false && (r.type || "expense") === "expense");
    if (!subs.length) return miss("You don't have any recurring bills set up yet.");
    subs.sort((a, b) => num(b.amount) - num(a.amount));
    const top = subs[0];
    return {
      ok: true, question, title: question,
      value: inr(top.amount),
      sub: `${top.name}${top.frequency ? " · " + top.frequency : ""} — your largest recurring bill`,
      breakdown: subs.slice(0, 3).map((r) => ({ label: r.name, val: inr(r.amount) })),
      note: "From your bills and subscriptions.",
    };
  }

  // --- Can I afford <amount>? ---
  if (/\b(afford|can i spend|do i have enough)\b/.test(q)) {
    const amt = parseAmount(q);
    const balance = (data.balances || []).filter((a) => a.type !== "card")
      .reduce((n, a) => n + num(a.balance), 0);
    const upcoming = (data.bills || []).filter((b) => (b.type || "expense") === "expense" && num(b.days_left) >= 0)
      .reduce((n, b) => n + num(b.amount), 0);
    const free = balance - upcoming;
    if (amt == null) {
      return { ok: true, question, title: question, value: inr(free),
        sub: "is free to spend after upcoming bills", note: "Balance minus bills still due this month." };
    }
    const can = free >= amt;
    return {
      ok: true, question, title: question,
      value: can ? "Yes" : "Not comfortably",
      sub: can ? `${inr(amt)} leaves you ${inr(free - amt)}`
               : `${inr(amt)} is more than the ${inr(free)} you have free`,
      breakdown: [
        { label: "Balance", val: inr(balance) },
        { label: "Upcoming bills", val: "−" + inr(upcoming) },
        { label: "Free to spend", val: inr(free) },
      ],
      note: "Balance across your non-card accounts, minus bills still due.",
    };
  }

  // --- Am I saving more than last / saving this month ---
  if (/\b(sav(e|ing|ings)|put away|left over)\b/.test(q)) {
    const inc = monthIncome(txns, thisKey), exp = monthExpense(txns, thisKey);
    const saved = inc - exp;
    const prevKey = addMonths(thisKey, -1);
    const prevSaved = monthIncome(txns, prevKey) - monthExpense(txns, prevKey);
    const more = saved - prevSaved;
    return {
      ok: true, question, title: question,
      value: inr(saved),
      sub: `saved this month — ${more >= 0 ? inr(more) + " more" : inr(-more) + " less"} than last month`,
      breakdown: [
        { label: "Income", val: inr(inc) },
        { label: "Spending", val: "−" + inr(exp) },
        { label: "Last month", val: inr(prevSaved) },
      ],
      spark: [prevSaved, saved], sparkDir: more >= 0 ? "up" : "down",
      note: "Income minus spending, this month versus last.",
    };
  }

  // --- Where did my money/salary go ---
  if (/\b(where|what)\b.*\b(go|gone|went|spend|spent|money|salary)\b/.test(q) &&
      !matchCategory(q, cats)) {
    const mr = resolveMonth(q, thisKey) || { key: thisKey, word: "this month" };
    const byCat = new Map();
    for (const t of txns) {
      if (t.type !== "expense" || ym(t.occurred_on) !== mr.key) continue;
      byCat.set(t.category_id, (byCat.get(t.category_id) || 0) + num(t.amount));
    }
    if (!byCat.size) return miss(`No spending recorded for ${mr.word}.`);
    const name = new Map(cats.map((c) => [c.id, c.name]));
    const rows = [...byCat.entries()].map(([id, v]) => ({ label: name.get(id) || "Other", val: v }))
      .sort((a, b) => b.val - a.val);
    const total = rows.reduce((n, r) => n + r.val, 0);
    return {
      ok: true, question, title: question,
      value: inr(total),
      sub: `spent ${mr.word} · top category ${rows[0].label}`,
      breakdown: rows.slice(0, 4).map((r) => ({ label: r.label, val: inr(r.val) })),
      note: "Your expenses grouped by category.",
    };
  }

  // --- Spend on <category> [in some month] ---
  const cat = matchCategory(q, cats);
  if (cat && /\b(spend|spent|spending|cost|paid|how much)\b/.test(q)) {
    const mr = resolveMonth(q, thisKey) || { key: thisKey, word: "this month" };
    const total = catMonth(txns, cat.id, mr.key);
    const count = txns.filter((t) => t.type === "expense" && t.category_id === cat.id && ym(t.occurred_on) === mr.key).length;
    const spark = [3, 2, 1, 0].map((d) => catMonth(txns, cat.id, addMonths(mr.key, -d)));
    const prev = catMonth(txns, cat.id, addMonths(mr.key, -1));
    const delta = prev > 0 ? Math.round(((total - prev) / prev) * 100) : null;
    return {
      ok: true, question, title: question,
      value: inr(total),
      sub: `on ${cat.name} ${mr.word} · ${count} transaction${count === 1 ? "" : "s"}`,
      breakdown: [
        { label: mr.word[0].toUpperCase() + mr.word.slice(1), val: inr(total) },
        { label: "Month before", val: inr(prev) },
        delta == null ? { label: "Change", val: "—" }
          : { label: "vs before", val: (delta >= 0 ? "+" : "") + delta + "%" },
      ],
      spark, sparkDir: total >= prev ? "up" : "down",
      note: `Your ${cat.name} spending, from your transactions.`,
    };
  }

  // --- Total spent / income / balance this month ---
  if (/\b(balance|how much.*(have|left)|net worth|in my account)\b/.test(q)) {
    const balance = (data.balances || []).reduce((n, a) => n + num(a.balance), 0);
    return { ok: true, question, title: question, value: inr(balance),
      sub: "across all your accounts",
      breakdown: (data.balances || []).map((a) => ({ label: a.name, val: inr(a.balance) })),
      note: "Live balance from your accounts." };
  }
  if (/\b(income|earn|earned|made|salary)\b/.test(q)) {
    const mr = resolveMonth(q, thisKey) || { key: thisKey, word: "this month" };
    const inc = monthIncome(txns, mr.key);
    return { ok: true, question, title: question, value: inr(inc),
      sub: `income ${mr.word}`, note: "Your income transactions." };
  }
  if (/\b(spend|spent|spending|expenses?)\b/.test(q)) {
    const mr = resolveMonth(q, thisKey) || { key: thisKey, word: "this month" };
    const exp = monthExpense(txns, mr.key);
    const spark = [3, 2, 1, 0].map((d) => monthExpense(txns, addMonths(mr.key, -d)));
    return { ok: true, question, title: question, value: inr(exp),
      sub: `spent ${mr.word}`, spark, sparkDir: "up", note: "Your total expenses." };
  }

  return miss("I couldn't work that out yet. Try asking about a category, a bill, your balance or your savings.");
}
