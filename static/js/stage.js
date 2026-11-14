// Put rows into the review queue (pending_imports) as one import batch.
// Shared by Import a file and the assistant, so both get the same category
// suggestions, the same de-duplication and the same one-tap undo.
//
// rows: [{ date, amount, direction: "expense" | "income", note, account, category, subcategory, dedupe }]

// 64-bit-ish key from two 32-bit hashes (no crypto.subtle: it's missing on plain-http phone tests).
export function hashKey(s) {
  let a = 0x811c9dc5, b = 5381;
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); a = Math.imul(a ^ c, 16777619) >>> 0; b = ((b * 33) ^ c) >>> 0; }
  return a.toString(16).padStart(8, "0") + b.toString(16).padStart(8, "0");
}

const SYN = [
  [/eat|food|dinner|lunch|breakfast|restaurant|cafe|beverage|swiggy|zomato|grocer|kitchen|snack/i, "Food"],
  [/transport|cab|uber|ola|auto|metro|fuel|petrol|flight|train|bus|travel/i, "Transport"],
  [/health|medic|pharma|doctor|hospital|clinic/i, "Health"],
  [/rent\b/i, "Rent"],
  [/bill|electric|water|gas|phone|mobile|internet|broadband|subscription|recharge/i, "Bills"],
  [/shop|apparel|cloth|amazon|flipkart|laundry/i, "Shopping"],
  [/fun|movie|social|entertain|netflix|party|game/i, "Entertainment"],
  [/salary|payroll|wage/i, "Salary"], [/freelance|invoice/i, "Freelance"], [/gift/i, "Gift"], [/invest|mutual|sip|stock|ipo/i, "Investment"],
];

// Best guess at one of the person's own categories: an exact name in the file,
// then their saved rules, then common words.
export function suggestCategory(r, categories, rules) {
  const mine = categories.filter((c) => c.kind === r.direction);
  const by = (n) => mine.find((c) => c.name.toLowerCase() === String(n).toLowerCase().trim());
  const hit = (r.subcategory && by(r.subcategory)) || (r.category && by(r.category));
  if (hit) return hit.id;
  const text = `${r.note || ""} ${r.category || ""} ${r.subcategory || ""}`.toLowerCase();
  const rule = rules.find((x) => text.includes(String(x.match_text).toLowerCase()));
  if (rule && mine.some((c) => c.id === rule.category_id)) return rule.category_id;
  for (const [re, name] of SYN) if (re.test(text) && by(name)) return by(name).id;
  return null;
}

const KINDS = ["xlsx", "xls", "csv", "pdf", "docx"];
const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (d) => (d ^ (Math.random() * 16) >> (d / 4)).toString(16)));

// Returns { batchId, added, skipped }. Throws the Supabase error on failure.
export async function stageRows(db, rows, { fileName = "file", kind = "other", usedClaude = false } = {}) {
  const [c, ru] = await Promise.all([db.from("categories").select("id, name, kind"),
    db.from("category_rules").select("match_text, category_id")]);
  const categories = c.data || [], rules = ru.error ? [] : ru.data || [];
  const batchId = uuid();
  const ins = await db.from("import_batches").insert({ id: batchId, file_name: String(fileName).slice(0, 120) || "file",
    file_kind: KINDS.includes(kind) ? kind : "other", row_count: rows.length, used_claude: usedClaude });
  if (ins.error) throw ins.error;
  const payload = rows.map((x) => {
    const cat = [x.category, x.subcategory].filter(Boolean).join(" / ").slice(0, 80);
    const note = (x.note || [x.category, x.subcategory].filter(Boolean).join(" - ")).slice(0, 200);
    return {
      source: "file", direction: x.direction, amount: x.amount, occurred_on: x.date, note,
      merchant: note.trim() ? note.trim().slice(0, 80) : null,
      account_hint: x.account ? String(x.account).slice(0, 80) : null, category_hint: cat || null,
      suggested_category_id: suggestCategory(x, categories, rules), import_batch_id: batchId,
      source_message_id: "file:" + hashKey(x.dedupe),
    };
  });
  // Which of these are already in (from an earlier import of the same rows)?
  const ids = payload.map((p) => p.source_message_id);
  const seen = new Set();
  for (let i = 0; i < ids.length; i += 150) {
    const q = await db.from("pending_imports").select("source_message_id").in("source_message_id", ids.slice(i, i + 150));
    if (q.error) throw q.error;
    (q.data || []).forEach((r) => seen.add(r.source_message_id));
  }
  const fresh = payload.filter((p) => !seen.has(p.source_message_id));
  for (let i = 0; i < fresh.length; i += 200) {
    const up = await db.from("pending_imports").upsert(fresh.slice(i, i + 200), { onConflict: "user_id,source_message_id", ignoreDuplicates: true });
    if (up.error) throw up.error;
  }
  await db.from("import_batches").update({ added: fresh.length, skipped: rows.length - fresh.length }).eq("id", batchId);
  return { batchId, added: fresh.length, skipped: rows.length - fresh.length };
}
