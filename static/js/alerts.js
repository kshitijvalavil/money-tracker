// Sprint 13 — Alerts.
// Pure, dependency-free functions that turn the data a signed-in user already
// has (balances, budgets, scheduled bills, goals, recent transactions) into a
// short list of things worth knowing. Everything is computed on the device from
// the user's own rows — no new server input, no network, no paid API — so the
// same code runs in demo mode and against Supabase, and is unit-testable.

export const ALERT_TYPES = [
  "budget_overspend",
  "unusual_txn",
  "upcoming_bill",
  "low_balance",
  "goal_milestone",
  "weekly_digest",
];

// What each toggle means, in the order the settings screen shows them.
export const ALERT_META = {
  budget_overspend: { label: "Budget overspend", hint: "Warn at 90% of any budget" },
  unusual_txn:      { label: "Unusual transactions", hint: "Flag charges well above your norm" },
  upcoming_bill:    { label: "Upcoming bills", hint: "Remind before a scheduled bill is due" },
  low_balance:      { label: "Low balance", hint: "When an account drops below your floor" },
  goal_milestone:   { label: "Goal milestones", hint: "At 50%, 90% and 100%" },
  weekly_digest:    { label: "Weekly digest email", hint: "A Sunday recap of the week" },
};

export function defaultAlertPrefs() {
  return {
    budget_overspend: true,
    unusual_txn: true,
    upcoming_bill: true,
    low_balance: true,
    goal_milestone: false,
    weekly_digest: false,
    budget_threshold: 0.9,   // fraction of a budget that trips the warning
    low_floor: 5000,         // rupees; below this an account is "low"
    bill_lead_days: 3,       // remind this many days before a bill is due
    unusual_mult: 2.5,       // a charge this many× the category average is "unusual"
    unusual_floor: 2000,     // ...and at least this many rupees, so small spikes stay quiet
  };
}

// Merge stored prefs over the defaults so a partial/old record still works.
export function withDefaults(prefs) {
  return { ...defaultAlertPrefs(), ...(prefs && typeof prefs === "object" ? prefs : {}) };
}

const num = (x) => Number(x) || 0;

// data: { balances:[{name,type,balance}], budgets:[{category,monthly_limit,spent}],
//         bills:[{name,type,amount,next_due,days_left,overdue}],
//         goals:[{name,pct,saved,target_amount}],
//         txns:[{occurred_on,amount,type,category_id}], categories:[{id,name}],
//         month:"YYYY-MM" }
// Returns alerts most-urgent first. Each: { id, type, severity, title, detail, pill }.
export function computeAlerts(data = {}, prefsIn) {
  const p = withDefaults(prefsIn);
  const out = [];
  const rank = { bad: 0, warn: 1, info: 2, ok: 3 };

  // 1) Budgets at or over their limit.
  if (p.budget_overspend) {
    for (const b of data.budgets || []) {
      const limit = num(b.monthly_limit);
      if (limit <= 0) continue;
      const spent = num(b.spent);
      const frac = spent / limit;
      if (frac >= 1) {
        out.push(mk("budget", b.category, "bad", `${b.category} is over budget`,
          `You've spent ${inr(spent)} of ${inr(limit)} this month`, "Budget", "bad"));
      } else if (frac >= p.budget_threshold) {
        out.push(mk("budget", b.category, "warn", `${b.category} budget almost gone`,
          `${pct(frac)} used — ${inr(spent)} of ${inr(limit)}`, "Budget", "warn"));
      }
    }
  }

  // 2) An unusually large charge this month, versus the category's own average.
  if (p.unusual_txn) {
    const catName = new Map((data.categories || []).map((c) => [c.id, c.name]));
    const month = data.month || (data.txns && data.txns[0] && String(data.txns[0].occurred_on).slice(0, 7));
    const byCat = new Map(); // category_id -> array of prior expense amounts
    for (const t of data.txns || []) {
      if (t.type !== "expense") continue;
      if (!byCat.has(t.category_id)) byCat.set(t.category_id, []);
      byCat.get(t.category_id).push(num(t.amount));
    }
    const flagged = [];
    for (const t of data.txns || []) {
      if (t.type !== "expense") continue;
      if (month && String(t.occurred_on).slice(0, 7) !== month) continue;
      const amt = num(t.amount);
      if (amt < p.unusual_floor) continue;
      const others = (byCat.get(t.category_id) || []).filter((x) => x !== amt);
      const avg = others.length ? others.reduce((n, x) => n + x, 0) / others.length : 0;
      if (avg > 0 && amt >= avg * p.unusual_mult) {
        flagged.push({ t, amt, avg, name: catName.get(t.category_id) || "Uncategorised" });
      }
    }
    // Show the single largest spike so the feed stays short.
    flagged.sort((a, b) => b.amt - a.amt);
    if (flagged[0]) {
      const f = flagged[0];
      out.push(mk("unusual", f.t.id || f.name, "warn", "Unusual charge",
        `${inr(f.amt)} on ${f.name} is larger than your usual ${inr(Math.round(f.avg))}`,
        "Spending", "warn"));
    }
  }

  // 3) Bills coming due soon (and anything overdue).
  if (p.upcoming_bill) {
    for (const bill of data.bills || []) {
      if (bill.type && bill.type !== "expense") continue;
      const left = num(bill.days_left);
      if (bill.overdue || left < 0) {
        out.push(mk("bill", bill.id || bill.name, "bad", `${bill.name} is overdue`,
          `${inr(bill.amount)} was due ${dueWord(left)}`, "Bill", "bad"));
      } else if (left <= p.bill_lead_days) {
        out.push(mk("bill", bill.id || bill.name, "info", `${bill.name} due ${dueWord(left)}`,
          `${inr(bill.amount)} scheduled`, "Bill", "brand"));
      }
    }
  }

  // 4) An account that has dropped below the floor.
  if (p.low_balance) {
    for (const a of data.balances || []) {
      if (a.type === "card") continue; // cards carry a balance owed, not a float
      const bal = num(a.balance);
      if (bal < p.low_floor) {
        out.push(mk("low", a.name, "warn", `${a.name} is low`,
          `${inr(bal)} — below your ${inr(p.low_floor)} floor`, "Balance", "warn"));
      }
    }
  }

  // 5) Goal milestones reached (highest band only).
  if (p.goal_milestone) {
    for (const g of data.goals || []) {
      const pc = num(g.pct);
      if (pc >= 100) {
        out.push(mk("goal", g.name, "ok", `${g.name} reached`,
          `Hit ${inr(g.target_amount)} — 100% of goal`, "Goal", "ok"));
      } else if (pc >= 90) {
        out.push(mk("goal", g.name, "info", `${g.name} almost there`,
          `${pct(pc / 100)} saved — nearly done`, "Goal", "brand"));
      } else if (pc >= 50) {
        out.push(mk("goal", g.name, "info", `${g.name} halfway`,
          `${pct(pc / 100)} of ${inr(g.target_amount)} saved`, "Goal", "brand"));
      }
    }
  }

  out.sort((a, b) => rank[a.severity] - rank[b.severity]);
  return out;
}

// Mark (avatar glyph) by palette role.
const MARK = { bad: "!", warn: "!", info: "•", ok: "✓", brand: "•" };

function mk(kind, key, severity, title, detail, pillText, pillCls) {
  return {
    id: `${kind}:${key}`,
    type: kind,
    severity,
    title,
    detail,
    pill: { text: pillText, cls: pillCls },
    mark: MARK[severity] || "•",
  };
}

// Plain Indian-grouped rupees for alert text (whole rupees, true minus).
function inr(n) {
  const v = Math.round(Number(n) || 0);
  const s = v < 0 ? "−" : "";
  return s + "₹" + Math.abs(v).toLocaleString("en-IN");
}
const pct = (frac) => Math.round(frac * 100) + "%";
function dueWord(daysLeft) {
  const d = Number(daysLeft) || 0;
  if (d < 0) return Math.abs(d) === 1 ? "yesterday" : `${Math.abs(d)} days ago`;
  if (d === 0) return "today";
  if (d === 1) return "tomorrow";
  return `in ${d} days`;
}
