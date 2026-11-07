// Voice parser tests (Sprint 16), run against the shared fixtures that the
// Python twin also uses. Run: node tests/voiceparse_test.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseVoice } from "../static/js/voiceparse.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "voice_phrases.json"))).cases;
let pass = 0, fail = 0;
const near = (a, b) => Math.abs(Number(a) - Number(b)) < 0.005;

function check(cond, msg) { if (cond) pass++; else { fail++; console.log("  [FAIL]", msg); } }

for (const c of fixtures) {
  const out = parseVoice(c.say);
  const label = JSON.stringify(c.say);
  if (c.want && c.want.error) { check(out.error === c.want.error, `${label}: error ${c.want.error} (got ${out.error || "rows:" + out.rows.length})`); continue; }
  if (c.multi) {
    check(out.rows.length === c.want_rows.length, `${label}: ${c.want_rows.length} rows (got ${out.rows.length})`);
    c.want_rows.forEach((w, i) => {
      const r = out.rows[i] || {};
      for (const [k, v] of Object.entries(w)) check(k === "amount" ? near(r[k], v) : r[k] === v, `${label} row ${i}: ${k}=${JSON.stringify(v)} (got ${JSON.stringify(r[k])})`);
    });
    continue;
  }
  check(out.rows && out.rows.length === 1, `${label}: one row (got ${out.error || out.rows.length})`);
  const r = out.rows?.[0] || {};
  for (const [k, v] of Object.entries(c.want)) {
    if (k === "merchant_safe") { check(!/[<>]/.test(r.merchant || ""), `${label}: merchant has no angle brackets (${JSON.stringify(r.merchant)})`); continue; }
    if (k === "is_bill") { check(!!r.is_bill === v, `${label}: is_bill=${v} (got ${!!r.is_bill})`); continue; }
    if (k === "amount") { check(near(r.amount, v), `${label}: amount=${v} (got ${r.amount})`); continue; }
    check(r[k] === v, `${label}: ${k}=${JSON.stringify(v)} (got ${JSON.stringify(r[k])})`);
  }
  check(r.confidence >= 0.3 && r.confidence <= 1, `${label}: confidence in range (${r.confidence})`);
}

// A long ramble with one amount still yields one row; script text is stripped from the name.
check(parseVoice("so um i went out and after a while i paid 200 for parking somewhere downtown").rows.length === 1, "rambling sentence still parses");
check(!/[<>]/.test(parseVoice('paid 10 to <b>x</b>').rows[0].merchant), "tags stripped from merchant");

console.log(`voiceparse (js): ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
