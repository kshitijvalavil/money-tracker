// Smart column mapper for bulk file import. Pure functions, no DOM, so the same
// file runs in the browser and under `node tests/importmap_test.mjs`.
//
// Input: a grid (array of rows, each an array of cells; cells may be string,
// number, Date or null) from any reader (xlsx, csv, pdf, docx).
// Output: { headerRow, columns, rows, warnings, confidence } where rows are
// normalized { date:'YYYY-MM-DD', amount:number>0, direction:'income'|'expense',
// note, account, category, subcategory, raw, rowIndex }.

const ROLE_WORDS = {
  date: ["date", "txn date", "transaction date", "value date", "posting date", "tran date", "period", "day", "when"],
  amount: ["amount", "amt", "inr", "rs", "value", "total", "price", "cost", "sum", "transaction amount"],
  debit: ["debit", "withdrawal", "withdrawal amt", "withdrawals", "dr", "paid out", "money out", "spent", "debit amount"],
  credit: ["credit", "deposit", "deposit amt", "deposits", "cr", "paid in", "money in", "received", "credit amount"],
  type: ["income/expense", "type", "dr/cr", "txn type", "transaction type", "cr/dr", "direction", "kind"],
  account: ["account", "payment method", "paid from", "card", "bank", "wallet", "mode", "paid by"],
  category: ["category", "cat", "head", "group"],
  subcategory: ["subcategory", "sub category", "sub-category", "subcat"],
  note: ["note", "notes", "narration", "description", "particulars", "details", "memo", "merchant", "payee", "remarks", "title", "name", "transaction remarks", "item"],
  currency: ["currency", "ccy"],
  balance: ["balance", "closing balance", "running balance", "bal"],
  ref: ["chq/ref no", "ref no", "reference", "utr", "cheque no", "chq no", "ref", "txn id"],
};

const norm = (s) => String(s ?? "").toLowerCase().replace(/[_\-\.]+/g, " ").replace(/\s+/g, " ").trim();
const isBlank = (v) => v === null || v === undefined || (typeof v === "string" && v.trim() === "");

// ---- amounts -----------------------------------------------------------------
// Handles 1,23,456.78 / 1,234.50 / Rs. / ₹ / INR / (123.00) / -123 / 123 Dr / 123 Cr / 1.234,50
export function parseAmount(v) {
  if (typeof v === "number") return Number.isFinite(v) ? { value: v, sign: v < 0 ? -1 : 1, marker: null } : null;
  if (isBlank(v)) return null;
  let s = String(v).trim();
  let sign = 1, marker = null;
  if (/^\(.*\)$/.test(s)) { sign = -1; s = s.slice(1, -1); }
  const m = s.match(/\b(dr|cr)\.?\s*$/i) || s.match(/^\s*(dr|cr)\.?\b/i);
  if (m) { marker = m[1].toLowerCase(); s = s.replace(m[0], ""); }
  s = s.replace(/(?:inr|rs\.?|₹|\$|€|£)/gi, "").replace(/\s+/g, "");
  if (/^-/.test(s)) { sign = -sign; s = s.slice(1); }
  else if (/^\+/.test(s)) s = s.slice(1);
  if (s.endsWith("-")) { sign = -sign; s = s.slice(0, -1); }
  if (!/^[\d.,]+$/.test(s) || !/\d/.test(s)) return null;
  const lastDot = s.lastIndexOf("."), lastComma = s.lastIndexOf(",");
  if (lastComma > lastDot && s.length - lastComma - 1 <= 2 && lastDot !== -1) s = s.replace(/\./g, "").replace(",", ".");   // 1.234,50
  else if (lastComma > lastDot && lastDot === -1 && /^\d{1,3}(,\d{3})+$/.test(s) === false && /,\d{1,2}$/.test(s)) s = s.replace(",", "."); // 12,50
  else s = s.replace(/,/g, "");
  const n = Number(s);
  return Number.isFinite(n) ? { value: n * sign, sign, marker } : null;
}

// ---- dates -------------------------------------------------------------------
const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12 };
const pad = (n) => String(n).padStart(2, "0");
const validYMD = (y, m, d) => {
  if (y < 1990 || y > 2100 || m < 1 || m > 12 || d < 1) return false;
  return d <= new Date(Date.UTC(y, m, 0)).getUTCDate();
};

function splitDate(s) {
  s = s.trim().replace(/\s+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*(am|pm)?$/i, "").replace(/T.*$/, (t) => (/^\d{4}-/.test(s) ? "" : t));
  let m = s.match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})$/);
  if (m) return { kind: "ymd", a: +m[1], b: +m[2], c: +m[3] };
  m = s.match(/^(\d{1,2})[-\/. ]([A-Za-z]{3,9})[-\/. ,]*(\d{2,4})$/);
  if (m && MONTHS[m[2].slice(0, 4).toLowerCase()] || m && MONTHS[m[2].slice(0, 3).toLowerCase()]) {
    const mon = MONTHS[m[2].slice(0, 3).toLowerCase()];
    let y = +m[3]; if (y < 100) y += 2000;
    return { kind: "dmy-named", d: +m[1], m: mon, y };
  }
  m = s.match(/^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})$/);
  if (m && MONTHS[m[1].slice(0, 3).toLowerCase()]) return { kind: "dmy-named", d: +m[2], m: MONTHS[m[1].slice(0, 3).toLowerCase()], y: +m[3] };
  m = s.match(/^(\d{1,2})[-\/.](\d{1,2})[-\/.](\d{2,4})$/);
  if (m) { let y = +m[3]; if (y < 100) y += 2000; return { kind: "ab", a: +m[1], b: +m[2], y }; }
  return null;
}

// Decide day-first vs month-first from ALL values: any first part >12 means
// day-first; any second part >12 means month-first; otherwise default day-first
// (India) and flag it as an assumption.
export function detectDateOrder(values) {
  let dayFirst = 0, monthFirst = 0, ambiguous = 0, seen = 0;
  for (const v of values) {
    if (typeof v !== "string") continue;
    const p = splitDate(v);
    if (!p || p.kind !== "ab") continue;
    seen++;
    if (p.a > 12 && p.b <= 12) dayFirst++;
    else if (p.b > 12 && p.a <= 12) monthFirst++;
    else if (p.a !== p.b) ambiguous++;
  }
  if (dayFirst && monthFirst) return { order: "mixed", ambiguous: true };
  if (monthFirst) return { order: "mdy", ambiguous: false };
  if (dayFirst) return { order: "dmy", ambiguous: false };
  return { order: "dmy", ambiguous: ambiguous > 0 && seen > 0 };
}

export function excelSerialToISO(n) {
  if (!(n > 20000 && n < 80000)) return null;
  const ms = Math.round((n - 25569) * 86400 * 1000);
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

export function parseDate(v, order = "dmy") {
  if (v instanceof Date) {
    if (isNaN(v)) return null;
    // Spreadsheet readers hand back local-midnight dates; use local parts.
    return `${v.getFullYear()}-${pad(v.getMonth() + 1)}-${pad(v.getDate())}`;
  }
  if (typeof v === "number") return excelSerialToISO(v);
  if (isBlank(v)) return null;
  const p = splitDate(String(v));
  if (!p) return null;
  let y, m, d;
  if (p.kind === "ymd") { y = p.a; m = p.b; d = p.c; }
  else if (p.kind === "dmy-named") { y = p.y; m = p.m; d = p.d; }
  else if (order === "mdy") { m = p.a; d = p.b; y = p.y; }
  else { d = p.a; m = p.b; y = p.y; }
  if (order === "dmy" && p.kind === "ab" && !validYMD(y, m, d) && validYMD(y, p.b, p.a)) { /* keep strict */ }
  return validYMD(y, m, d) ? `${y}-${pad(m)}-${pad(d)}` : null;
}

const looksDate = (v) => v instanceof Date || (typeof v === "number" && !!excelSerialToISO(v)) ||
  (typeof v === "string" && !!splitDate(v));
const looksNumber = (v) => typeof v === "number" || (typeof v === "string" && !!parseAmount(v));

// ---- header + columns ----------------------------------------------------------
function headerScore(row) {
  let text = 0, known = 0;
  for (const c of row) {
    if (isBlank(c) || typeof c !== "string" || looksNumber(c) || looksDate(c)) continue;
    text++;
    const n = norm(c);
    for (const words of Object.values(ROLE_WORDS)) if (words.includes(n)) { known++; break; }
  }
  return { text, known };
}

export function findHeaderRow(grid) {
  let best = -1, bestScore = 0;
  const limit = Math.min(grid.length, 60);
  for (let i = 0; i < limit; i++) {
    const { text, known } = headerScore(grid[i] || []);
    if (text < 2) continue;
    // Must be followed by data: next non-empty row has a date or number.
    let data = false;
    for (let j = i + 1; j < Math.min(grid.length, i + 6); j++) {
      const r = grid[j] || [];
      if (r.some(looksDate) && r.some(looksNumber)) { data = true; break; }
    }
    if (!data) continue;
    const score = known * 3 + text;
    if (score > bestScore) { best = i; bestScore = score; }
  }
  return best;
}

function colValues(rows, ci) { return rows.map((r) => r[ci]).filter((v) => !isBlank(v)); }
const share = (vals, fn) => (vals.length ? vals.filter(fn).length / vals.length : 0);

function headerRole(h) {
  const n = norm(h);
  if (!n) return null;
  for (const [role, words] of Object.entries(ROLE_WORDS)) if (words.includes(n)) return role;
  // keyword containment, most specific first
  const order = ["subcategory", "debit", "credit", "balance", "ref", "type", "date", "category", "account", "currency", "note", "amount"];
  for (const role of order) if (ROLE_WORDS[role].some((w) => w.length > 3 && n.includes(w))) return role;
  return null;
}

export function mapColumns(header, dataRows) {
  const warnings = [];
  const cols = header.map((h, i) => ({ index: i, header: String(h ?? "").trim(), role: null, confidence: 0 }));
  const taken = new Set();
  const assign = (c, role, conf) => { c.role = role; c.confidence = conf; taken.add(role); };

  // 1. By header name, then verified against the values.
  for (const c of cols) {
    const role = headerRole(c.header);
    if (!role) continue;
    const vals = colValues(dataRows, c.index);
    if (!vals.length) continue;
    const dateShare = share(vals, looksDate), numShare = share(vals, looksNumber);
    let ok = true;
    if (role === "date") ok = dateShare >= 0.7;
    else if (["amount", "debit", "credit", "balance"].includes(role)) ok = numShare >= 0.7;
    else if (["account", "category", "subcategory", "note", "type", "currency"].includes(role)) ok = numShare < 0.5;
    if (!ok) continue;
    c.role = role; c.confidence = c.header ? 0.95 : 0.6;
  }

  // 2. Duplicate roles: keep the best, demote others (sample has two "Account"
  // headers where the second is really a number column).
  const byRole = {};
  for (const c of cols) if (c.role) (byRole[c.role] ||= []).push(c);
  for (const [role, list] of Object.entries(byRole)) {
    if (list.length < 2) continue;
    if (role === "amount") {
      // Prefer the numeric column whose header is a currency code or says INR, else the first;
      // others become 'alt amount' and are reported when they disagree.
      list.sort((a, b) => (/^inr$/i.test(b.header) ? 1 : 0) - (/^inr$/i.test(a.header) ? 1 : 0) || a.index - b.index);
    }
    list.slice(1).forEach((c) => { c.role = role === "amount" ? "altamount" : null; c.confidence = 0; });
  }
  // A demoted or unnamed column that is all numbers (like a second "Account"
  // column holding converted amounts) is a second amount: compare, don't use.
  for (const c of cols) {
    if (c.role) continue;
    const vals = colValues(dataRows, c.index);
    if (vals.length && share(vals, (v) => typeof v === "number") >= 0.9 && headerRole(c.header) === "account") c.role = "altamount";
  }
  for (const c of cols) if (c.role && c.role !== "altamount") taken.add(c.role);

  // 3. Value-shape fallbacks for anything left unmapped.
  const free = cols.filter((c) => !c.role);
  if (!taken.has("date")) {
    const c = free.map((x) => [x, share(colValues(dataRows, x.index), looksDate)]).sort((a, b) => b[1] - a[1])[0];
    if (c && c[1] >= 0.8) { assign(c[0], "date", 0.7); free.splice(free.indexOf(c[0]), 1); }
  }
  if (!taken.has("amount") && !(taken.has("debit") || taken.has("credit"))) {
    const c = free.filter((x) => share(colValues(dataRows, x.index), looksNumber) >= 0.8)
      .sort((a, b) => a.index - b.index)[0];
    if (c) { assign(c, "amount", 0.6); free.splice(free.indexOf(c), 1); }
  }
  if (!taken.has("note")) {
    const c = free.map((x) => [x, colValues(dataRows, x.index)])
      .filter(([, v]) => v.length && share(v, (z) => typeof z === "string" && !looksNumber(z) && !looksDate(z)) >= 0.8)
      .sort((a, b) => avgLen(b[1]) - avgLen(a[1]))[0];
    if (c) assign(c[0], "note", 0.55);
  }
  return { cols, warnings };
}
const avgLen = (v) => v.reduce((s, x) => s + String(x).length, 0) / (v.length || 1);

const INCOME_WORDS = /^(income|credit|cr|deposit|received|in|salary|refund)$/i;
const EXPENSE_WORDS = /^(expense|debit|dr|withdrawal|paid|out|spent|exp)$/i;
const TRANSFER_WORDS = /^(transfer|transfer[- ]?(in|out)|xfer)$/i;

// ---- main entry -------------------------------------------------------------------
export function mapGrid(grid, opts = {}) {
  const warnings = [];
  grid = (grid || []).map((r) => (Array.isArray(r) ? r : []));
  if (!grid.some((r) => r.some((c) => !isBlank(c)))) return { ok: false, error: "empty", message: "That file looks empty.", rows: [], warnings };
  let h = opts.headerRow ?? findHeaderRow(grid);
  let header, body;
  if (h < 0) {
    // Header-less: first row is data if it holds a date + number.
    const first = grid.find((r) => r.some(looksDate) && r.some(looksNumber));
    if (!first) return { ok: false, error: "no_table", message: "Couldn't find a table of transactions in that file.", rows: [], warnings };
    const width = Math.max(...grid.map((r) => r.length));
    header = Array.from({ length: width }, (_, i) => `Column ${i + 1}`);
    body = grid.slice(grid.indexOf(first));
    warnings.push("No header row found; columns were guessed from the values.");
    h = -1;
  } else { header = grid[h].map((x) => (isBlank(x) ? "" : String(x))); body = grid.slice(h + 1); }

  body = body.filter((r) => r.some((c) => !isBlank(c)));
  let { cols } = mapColumns(header, body);
  if (opts.mapping) cols.forEach((c) => { c.role = opts.mapping[c.index] ?? null; c.confidence = c.role ? 1 : 0; });
  const col = (role) => cols.find((c) => c.role === role);
  const dateC = col("date"), amtC = col("amount"), debC = col("debit"), creC = col("credit"), typeC = col("type");
  if (!dateC) return { ok: false, error: "no_date", message: "Couldn't find a date column. Pick one below.", headerRow: h, columns: cols, header, rows: [], warnings };
  if (!amtC && !debC && !creC) return { ok: false, error: "no_amount", message: "Couldn't find an amount column. Pick one below.", headerRow: h, columns: cols, header, rows: [], warnings };

  const dateVals = body.map((r) => r[dateC.index]);
  const dord = opts.dateOrder ? { order: opts.dateOrder, ambiguous: false } : detectDateOrder(dateVals);
  if (dord.order === "mixed") warnings.push("Dates mix day-first and month-first; check the dates in the preview.");
  else if (dord.ambiguous) warnings.push("Dates like 03/04/2026 could be 3 April or 4 March. We read them as day/month (India). Change this if wrong.");

  const out = []; let skipped = 0, badDate = 0, disagree = 0;
  const alts = cols.filter((c) => c.role === "altamount");
  const seen = new Map();
  body.forEach((r, i) => {
    const rowIndex = h + 1 + i;
    const date = parseDate(r[dateC.index], dord.order === "mdy" ? "mdy" : "dmy");
    let amount = null, direction = null;
    if (amtC) {
      const a = parseAmount(r[amtC.index]);
      if (a) { amount = Math.abs(a.value); direction = a.value < 0 ? "expense" : null; if (a.marker) direction = a.marker === "cr" ? "income" : "expense"; }
    } else {
      const d = parseAmount(r[debC?.index]), c = parseAmount(r[creC?.index]);
      if (d && Math.abs(d.value) > 0) { amount = Math.abs(d.value); direction = "expense"; }
      else if (c && Math.abs(c.value) > 0) { amount = Math.abs(c.value); direction = "income"; }
    }
    if (typeC && !isBlank(r[typeC.index])) {
      const t = String(r[typeC.index]).trim();
      if (TRANSFER_WORDS.test(t)) { skipped++; return; }
      if (INCOME_WORDS.test(t)) direction = "income";
      else if (EXPENSE_WORDS.test(t)) direction = "expense";
    }
    if (amount === null || amount === 0) { skipped++; return; }          // totals, blanks, zero rows
    if (!date) { if (r.some((c) => !isBlank(c))) badDate++; skipped++; return; }
    if (!direction) direction = opts.defaultDirection || (typeC || debC ? "expense" : (amtC && parseAmount(r[amtC.index]).value < 0 ? "expense" : (opts.positiveIsIncome ? "income" : "expense")));
    if (alts.some((alt) => { const b = parseAmount(r[alt.index]); return b && Math.abs(Math.abs(b.value) - amount) > 0.01; })) disagree++;
    const cell = (role) => { const c = col(role); const v = c ? r[c.index] : null; return isBlank(v) ? "" : String(v).trim(); };
    const note = cell("note");
    const base = `${date}|${amount.toFixed(2)}|${note.toLowerCase()}|${cell("account").toLowerCase()}`;
    const occ = (seen.get(base) || 0) + 1; seen.set(base, occ);
    out.push({
      date, amount: Math.round(amount * 100) / 100, direction, note: note.slice(0, 200),
      account: cell("account"), category: cell("category"), subcategory: cell("subcategory"),
      currency: cell("currency"), occurrence: occ, rowIndex, dedupe: `${base}#${occ}`,
    });
  });
  if (skipped) warnings.push(`${skipped} row${skipped === 1 ? "" : "s"} skipped (blank, zero, transfers or totals).`);
  if (badDate) warnings.push(`${badDate} row${badDate === 1 ? "" : "s"} skipped because the date could not be read.`);
  if (disagree) warnings.push(`${disagree} row${disagree === 1 ? "" : "s"} have a second amount column that disagrees (for example another currency). We used "${amtC?.header}".`);
  const conf = Math.min(...[dateC, amtC || debC || creC].map((c) => c.confidence || 0));
  return {
    ok: out.length > 0, error: out.length ? null : "no_rows", message: out.length ? "" : "No usable rows were found.",
    headerRow: h, header, columns: cols, rows: out, warnings, dateOrder: dord.order,
    confidence: conf, needsReview: conf < 0.8 || !!dord.ambiguous,
  };
}

// Stable fingerprint of a header row so a saved mapping can be reused.
export function headerFingerprint(header) {
  const s = header.map(norm).join("|");
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}
