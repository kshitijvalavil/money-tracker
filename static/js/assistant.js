// The assistant's tools and conversation loop. The tools run here, in the
// browser, against the signed-in person's own data (row-level security applies),
// so the server never reads anyone's money. See assistant.py for the other half.

import { mapGrid } from "./importmap.js";
import { readFile, bestSheet } from "./fileimport.js";
import { stageRows } from "./stage.js";

const TX_SELECT = "occurred_on, amount, type, note, categories(name), accounts!transactions_account_id_fkey(name)";
const PAGE = 1000, MAX_ROWS = 20000;
const lower = (s) => String(s ?? "").toLowerCase();
const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;
const isDate = (s) => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s);
const must = (r) => { if (r.error) throw new Error(r.error.message || "database error"); return r.data || []; };

// Every transaction in a date range (paged: the database returns 1,000 rows at a time).
async function fetchTransactions(db, { from_date, to_date, type } = {}) {
  const out = [];
  for (let at = 0; at < MAX_ROWS; at += PAGE) {
    let q = db.from("transactions").select(TX_SELECT);
    if (isDate(from_date)) q = q.gte("occurred_on", from_date);
    if (isDate(to_date)) q = q.lte("occurred_on", to_date);
    if (type) q = q.eq("type", type);
    const rows = must(await q.order("occurred_on", { ascending: false }).range(at, at + PAGE - 1));
    out.push(...rows);
    if (rows.length < PAGE) break;
  }
  return out.map((t) => ({ date: t.occurred_on, amount: round2(t.amount), type: t.type, note: t.note || "",
    category: t.categories?.name || null, account: t.accounts?.name || null }));
}

// ---- tools ------------------------------------------------------------------
async function getSnapshot(db, today) {
  const month = today.slice(0, 7);
  const [bal, sum, bud, bills, goals, nw, inv, rev] = await Promise.all([
    db.rpc("account_balances"),
    db.rpc("dashboard_summary", { p_month: month }),
    db.rpc("budget_status", { p_month: month }),
    db.rpc("upcoming_recurring", { p_days: 400, p_as_of: today }),
    db.rpc("goal_progress"),
    db.rpc("net_worth_breakdown"),
    db.from("investments").select("name, kind, invested_amount, current_value"),
    db.from("pending_imports").select("id", { count: "exact", head: true }).eq("status", "pending"),
  ]);
  const accounts = must(bal).map((a) => ({ name: a.name, type: a.type, balance: round2(a.balance) }));
  const s = must(sum)[0] || {};
  const worth = nw.error ? [] : nw.data || [];
  const side = (k) => round2(worth.filter((r) => r.kind === k).reduce((n, r) => n + Number(r.amount), 0));
  return {
    today, month, currency: "INR",
    accounts,
    total_balance: round2(accounts.reduce((n, a) => n + a.balance, 0)),
    this_month: { income: round2(s.month_income), spent: round2(s.month_expense) },
    net_worth: worth.length ? { assets: side("asset"), liabilities: side("liability"), net: round2(side("asset") - side("liability")) } : null,
    budgets: (bud.error ? [] : bud.data || []).map((b) => ({ category: b.category, limit: round2(b.monthly_limit),
      spent: round2(b.spent), left: round2(b.monthly_limit - b.spent) })),
    bills: (bills.error ? [] : bills.data || []).map((b) => ({ name: b.name, type: b.type, amount: round2(b.amount),
      next_due: b.next_due, days_left: Number(b.days_left), overdue: !!b.overdue, auto_post: !!b.auto_post })),
    goals: (goals.error ? [] : goals.data || []).map((g) => ({ name: g.name, target: round2(g.target_amount),
      saved: round2(g.saved), target_date: g.target_date || null })),
    investments: (inv.error ? [] : inv.data || []).map((i) => ({ name: i.name, kind: i.kind,
      invested: round2(i.invested_amount), value: round2(i.current_value) })),
    waiting_for_review: rev.error ? 0 : rev.count || 0,
  };
}

async function searchTransactions(db, input) {
  const all = await fetchTransactions(db, input);
  const text = lower(input.text), cat = lower(input.category), acc = lower(input.account);
  const rows = all.filter((t) =>
    (!cat || lower(t.category) === cat) &&
    (!acc || lower(t.account).includes(acc)) &&
    (!text || lower(t.note).includes(text)) &&
    (input.min_amount == null || t.amount >= Number(input.min_amount)) &&
    (input.max_amount == null || t.amount <= Number(input.max_amount)));
  const limit = Math.min(200, Math.max(1, Number(input.limit) || 50));
  const total = (type) => round2(rows.filter((t) => t.type === type).reduce((n, t) => n + t.amount, 0));
  return { matches: rows.length, total_out: total("expense"), total_in: total("income"),
    shown: Math.min(limit, rows.length), rows: rows.slice(0, limit) };
}

async function summarizeSpending(db, input) {
  const type = input.type === "income" ? "income" : "expense";
  const rows = await fetchTransactions(db, { ...input, type });
  const keyOf = { category: (t) => t.category || "Uncategorised", month: (t) => t.date.slice(0, 7),
    merchant: (t) => t.note.trim() || "(no note)", account: (t) => t.account || "Unknown" }[input.group_by];
  if (!keyOf) throw new Error("group_by must be category, month, merchant or account");
  const groups = new Map();
  for (const t of rows) {
    const g = groups.get(keyOf(t)) || { total: 0, count: 0 };
    g.total += t.amount; g.count++; groups.set(keyOf(t), g);
  }
  let list = [...groups].map(([name, g]) => ({ name, total: round2(g.total), count: g.count }));
  list = input.group_by === "month" ? list.sort((a, b) => a.name.localeCompare(b.name)) : list.sort((a, b) => b.total - a.total);
  return { type, from_date: input.from_date, to_date: input.to_date, total: round2(rows.reduce((n, t) => n + t.amount, 0)),
    transactions: rows.length, groups: list.slice(0, 60), more_groups: Math.max(0, list.length - 60) };
}

// The attached file, read once and remembered for propose_transactions.
async function readAttached(ctx, input) {
  if (!ctx.file) throw new Error("No file is attached. Ask the person to attach one with the paperclip.");
  if (!ctx.read || ctx.readOf !== ctx.file) {
    const read = await readFile(ctx.file);
    if (read.needsServer) throw new Error("Old .xls files and scanned PDFs can't be read in chat. Use Import a file instead.");
    if (!read.sheets.length) throw new Error("No table was found in that file.");
    ctx.read = read; ctx.readOf = ctx.file;
  }
  const sheets = ctx.read.sheets;
  const idx = Number.isInteger(input.sheet) && sheets[input.sheet] ? input.sheet : bestSheet(sheets, mapGrid);
  const grid = sheets[idx].rows;
  const mapped = mapGrid(grid);
  ctx.mapped = mapped.ok ? mapped.rows : null;
  // A readable slice: at most 80 rows and 14,000 characters of cells.
  const first = []; let chars = 0;
  for (const r of grid.slice(0, 80)) {
    const row = r.map((c) => String(c ?? "").slice(0, 60)); chars += row.join("").length;
    if (chars > 14000) break;
    first.push(row);
  }
  return {
    file: ctx.file.name, kind: ctx.read.kind,
    sheets: sheets.map((s, i) => ({ index: i, name: s.name, rows: s.rows.length })), showing_sheet: idx,
    total_rows: grid.length, first_rows: first,
    auto_mapping: mapped.ok
      ? { ok: true, transactions: mapped.rows.length, warnings: mapped.warnings,
          columns: (mapped.columns || []).filter((c) => c.role).map((c) => ({ header: c.header, used_as: c.role })),
          sample: mapped.rows.slice(0, 5).map(({ date, amount, direction, note }) => ({ date, amount, direction, description: note })) }
      : { ok: false, problem: mapped.message },
  };
}

async function proposeTransactions(ctx, input) {
  let rows;
  if (input.use_auto_mapping) {
    if (!ctx.mapped) throw new Error("There is no usable auto_mapping. Call read_attached_file first, or pass rows.");
    rows = ctx.mapped;
  } else {
    const seen = new Map();
    rows = (Array.isArray(input.rows) ? input.rows : []).slice(0, 300).flatMap((r) => {
      const amount = round2(Math.abs(Number(r.amount)));
      if (!isDate(r.date) || !(amount > 0) || amount > 999999999999.99) return [];
      const direction = r.direction === "income" ? "income" : "expense";
      const note = String(r.description || "").slice(0, 200);
      const base = `${r.date}|${amount.toFixed(2)}|${lower(note)}|${lower(r.account)}`;
      const occ = (seen.get(base) || 0) + 1; seen.set(base, occ);
      return [{ date: r.date, amount, direction, note, account: String(r.account || ""), category: String(r.category || ""),
        subcategory: "", dedupe: `${base}#${occ}` }];
    });
  }
  if (!rows.length) throw new Error("No valid rows to propose (each needs a YYYY-MM-DD date and a positive amount).");
  const label = String(input.label || ctx.file?.name || "From the assistant").slice(0, 100);
  // The person confirms in the chat; only then do the rows reach the review list.
  ctx.onProposal({ rows, label });
  return { proposed: rows.length, status: "Shown to the person. Nothing is on the review list until they press the button." };
}

// ---- the loop ---------------------------------------------------------------
// ctx: { db, today, file, authHeaders(), onProposal({rows,label}), onStep(text) }
export async function runTool(ctx, name, input) {
  input = input && typeof input === "object" ? input : {};
  if (name === "get_snapshot") return getSnapshot(ctx.db, ctx.today);
  if (name === "search_transactions") return searchTransactions(ctx.db, input);
  if (name === "summarize_spending") return summarizeSpending(ctx.db, input);
  if (name === "read_attached_file") return readAttached(ctx, input);
  if (name === "propose_transactions") return proposeTransactions(ctx, input);
  throw new Error("Unknown tool: " + name);
}

const STEP = { get_snapshot: "Looking at your accounts, bills and budgets…", search_transactions: "Searching your transactions…",
  summarize_spending: "Adding up your spending…", read_attached_file: "Reading your file…", propose_transactions: "Preparing the rows…" };

export class AssistantUnavailable extends Error {}

// Sends `history` (which this function extends in place) and returns Claude's
// final text. Runs any tools Claude asks for along the way.
export async function converse(ctx, history) {
  for (let round = 0; round < 10; round++) {
    const res = await fetch("/api/assistant", { method: "POST",
      headers: { "Content-Type": "application/json", ...(await ctx.authHeaders()) },
      body: JSON.stringify({ messages: history, today: ctx.today }) });
    const out = await res.json().catch(() => ({}));
    if (res.status === 503 && out.unavailable) throw new AssistantUnavailable(out.error);
    if (!res.ok) throw new Error(out.error || "The assistant couldn't answer just now. Please try again.");
    history.push({ role: "assistant", content: out.content });
    const text = out.content.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
    if (out.stop_reason !== "tool_use") {
      if (out.stop_reason === "refusal") return text || "I can't help with that one.";
      if (out.stop_reason === "max_tokens") return (text ? text + "\n\n" : "") + "(That answer was cut short. Ask me to continue.)";
      return text || "I don't have anything to add.";
    }
    const results = [];
    for (const call of out.content.filter((b) => b.type === "tool_use")) {
      ctx.onStep(STEP[call.name] || "Working…");
      try {
        results.push({ type: "tool_result", tool_use_id: call.id, content: JSON.stringify(await runTool(ctx, call.name, call.input)) });
      } catch (e) {
        results.push({ type: "tool_result", tool_use_id: call.id, is_error: true, content: JSON.stringify({ error: e?.message || "failed" }) });
      }
    }
    history.push({ role: "user", content: results });
  }
  return "That took more steps than I allow in one go. Try a narrower question.";
}

// Confirmed by the person: move a proposal onto the review list.
export const acceptProposal = (db, { rows, label }) => stageRows(db, rows, { fileName: label, kind: "other", usedClaude: true });
