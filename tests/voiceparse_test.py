"""Voice parser (Python twin) tests against the shared fixtures, plus the
/api/parse-voice endpoint. Run: python3 tests/voiceparse_test.py"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from voiceparse import parse_voice  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "voice_phrases.json")
cases = json.load(open(FIX))["cases"]
results = []


def check(cond, msg):
    results.append(bool(cond))
    if not cond:
        print("  [FAIL]", msg)


def near(a, b):
    return abs(float(a) - float(b)) < 0.005


for c in cases:
    out = parse_voice(c["say"])
    label = json.dumps(c["say"])
    if c.get("want", {}).get("error"):
        check(out.get("error") == c["want"]["error"], f"{label}: error {c['want']['error']} (got {out.get('error') or 'rows:' + str(len(out['rows']))})")
        continue
    if c.get("multi"):
        check(len(out["rows"]) == len(c["want_rows"]), f"{label}: {len(c['want_rows'])} rows (got {len(out['rows'])})")
        for i, w in enumerate(c["want_rows"]):
            r = out["rows"][i] if i < len(out["rows"]) else {}
            for k, v in w.items():
                check(near(r.get(k), v) if k == "amount" else r.get(k) == v, f"{label} row {i}: {k}={v!r} (got {r.get(k)!r})")
        continue
    check(len(out.get("rows", [])) == 1, f"{label}: one row (got {out.get('error') or len(out['rows'])})")
    r = out["rows"][0] if out.get("rows") else {}
    for k, v in c["want"].items():
        if k == "merchant_safe":
            check("<" not in (r.get("merchant") or "") and ">" not in (r.get("merchant") or ""), f"{label}: safe merchant ({r.get('merchant')!r})")
        elif k == "is_bill":
            check(bool(r.get("is_bill")) == v, f"{label}: is_bill={v} (got {bool(r.get('is_bill'))})")
        elif k == "amount":
            check(near(r.get("amount"), v), f"{label}: amount={v} (got {r.get('amount')})")
        else:
            check(r.get(k) == v, f"{label}: {k}={v!r} (got {r.get(k)!r})")

# Parity spot-check: the JS twin must produce the same amounts (guard against drift).
import subprocess  # noqa: E402

js = subprocess.run(["node", "-e", """
import('./static/js/voiceparse.js').then(m => {
  const says = %s;
  console.log(JSON.stringify(says.map(s => { const r = m.parseVoice(s); return r.error ? {e: r.error} : r.rows.map(x => x.amount); })));
});
""" % json.dumps([c["say"] for c in cases])], cwd=ROOT, capture_output=True, text=True)
if js.returncode == 0:
    jsout = json.loads(js.stdout)
    for c, ja in zip(cases, jsout):
        out = parse_voice(c["say"])
        if out.get("error"):
            check(isinstance(ja, dict) and ja.get("e") == out["error"], f"parity {c['say']!r}: py error {out['error']} js={ja}")
        else:
            py = [round(float(r["amount"]), 2) for r in out["rows"]]
            jsa = [round(float(x), 2) for x in ja] if isinstance(ja, list) else None
            check(py == jsa, f"parity {c['say']!r}: py={py} js={ja}")
else:
    print("  [WARN] node parity check skipped:", js.stderr.strip()[:120])

# Endpoint.
import app as appmod  # noqa: E402

appmod.DEMO_MODE = False
cl = appmod.app.test_client()
check(cl.post("/api/parse-voice", json={"text": "paid 50 for tea"}).status_code == 401, "signed out: 401")
r = cl.post("/api/parse-voice", json={"text": "paid 450 to Starbucks on UPI"}, headers={"Authorization": "Bearer x"})
check(r.status_code == 200 and r.get_json()["rows"][0]["amount"] == 450, "signed in: parses")
r = cl.post("/api/parse-voice", json={"text": ""}, headers={"Authorization": "Bearer x"})
check(r.status_code == 200 and r.get_json().get("error") == "empty", "empty transcript: clean error, no crash")
r = cl.post("/api/parse-voice", json={"text": "x" * 5000}, headers={"Authorization": "Bearer x"})
check(r.status_code == 200, "very long transcript handled")
appmod._RL_HITS.clear()
codes = [cl.post("/api/parse-voice", json={"text": "paid 5 for tea"}, headers={"Authorization": "Bearer x", "X-Forwarded-For": "7.7.7.7"}).status_code for _ in range(42)]
check(codes.count(429) >= 2, "rate limited")
appmod._RL_HITS.clear()

print(f"voiceparse (py): {sum(results)} passed, {len(results) - sum(results)} failed")
sys.exit(0 if all(results) else 1)
