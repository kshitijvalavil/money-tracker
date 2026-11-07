// Privacy tools: download everything the app holds about you, as one JSON file.
//
// The browser reads each table with the signed-in session, so database row-level
// security guarantees you only ever get your own rows. Nothing is sent anywhere.

// Every table that stores a user's data. Keep in step with supabase/migrations.
export const EXPORT_TABLES = [
  "accounts", "audit_log", "budgets", "categories", "category_rules", "goal_contributions",
  "goals", "investments", "labels", "net_worth_snapshots", "pending_imports",
  "recurring", "sync_status", "transaction_labels", "transactions", "user_consents", "user_prefs",
];

const PAGE = 1000;   // Supabase returns at most 1000 rows per request

// Paging needs a stable order. Most tables are keyed by id; these are not.
const ORDER_BY = {
  transaction_labels: ["transaction_id", "label_id"],
  user_consents: ["user_id"],
  sync_status: ["user_id"],
  user_prefs: ["user_id"],
};

// Read one table completely, a page at a time. Throws if any page fails, so an
// export is never silently incomplete.
export async function readAll(db, table) {
  const rows = [];
  for (let from = 0; ; from += PAGE) {
    let q = db.from(table).select("*");
    for (const col of ORDER_BY[table] || ["id"]) q = q.order(col, { ascending: true });
    const { data, error } = await q.range(from, from + PAGE - 1);
    if (error) throw new Error(`${table}: ${error.message || "could not be read"}`);
    rows.push(...(data || []));
    if (!data || data.length < PAGE) break;
  }
  return rows;
}

// Build the export object: who, when, and every table's rows.
export async function exportMyData(db, email = "") {
  const tables = {};
  for (const t of EXPORT_TABLES) tables[t] = await readAll(db, t);
  const counts = Object.fromEntries(EXPORT_TABLES.map((t) => [t, tables[t].length]));
  return {
    app: "Money Tracker",
    exported_at: new Date().toISOString(),
    account: email,
    row_counts: counts,
    tables,
  };
}

// Hand the browser a file to save. Revokes the temporary link straight away.
export function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
