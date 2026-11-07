"""Old .xls reader + /api/read-xls (Sprint 15). Run: python3 tests/xlsreader_test.py"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures", "import")

from xlsreader import MAGIC, XlsReadError, read_xls  # noqa: E402

results = []


def check(cond, msg):
    results.append(bool(cond))
    if not cond:
        print("  [FAIL]", msg)


def raises(fn, status):
    try:
        fn()
    except XlsReadError as e:
        return e.status == status
    return False


# ---- the reader ----
with open(os.path.join(FIX, "hdfc-style.xls"), "rb") as f:
    data = f.read()
out = read_xls(data)
rows = out["sheets"][0]["rows"]
check(out["sheets"][0]["name"] == "Statement", "sheet name kept")
check(rows[0][0] == "SAMPLE BANK LTD", "junk rows above the header are returned (the browser finds the header)")
hdr = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
check(rows[hdr][4] == "Withdrawal Amt.", "header row read")
check(len([r for r in rows[hdr + 1:] if r and r[0] and "/" in str(r[0])]) == 18, "18 transaction rows")
check(all(c is None or isinstance(c, (str, int, float, bool)) for r in rows for c in r), "cells are plain JSON values")

check(raises(lambda: read_xls(b""), 400), "empty refused")
check(raises(lambda: read_xls(b"PK\x03\x04 not xls"), 415), "non-xls refused")
check(raises(lambda: read_xls(MAGIC + b"\x00" * 600), 422), "damaged xls gives a friendly 422")
check(raises(lambda: read_xls(MAGIC + b"\x00" * (12 * 1024 * 1024)), 413), "over 12 MB refused")

# ---- the endpoint ----
os.environ["DEMO_MODE"] = ""
import app as appmod  # noqa: E402

appmod.DEMO_MODE = False
c = appmod.app.test_client()
r = c.post("/api/read-xls", data=data, content_type="application/octet-stream")
check(r.status_code == 401, "signed out: 401")
r = c.post("/api/read-xls", data=data, content_type="application/octet-stream", headers={"Authorization": "Bearer x"})
check(r.status_code == 200 and r.get_json()["sheets"][0]["rows"], "signed in: rows back")
check("Content-Security-Policy" in r.headers, "security headers on the API response")
r = c.post("/api/read-xls", data=b"hello", headers={"Authorization": "Bearer x"})
check(r.status_code == 415 and "error" in r.get_json(), "wrong file: friendly error")
r = c.post("/api/read-xls", data=b"x" * (13 * 1024 * 1024), headers={"Authorization": "Bearer x"})
check(r.status_code == 413, "request over the server cap: 413")
appmod._RL_HITS.clear()
codes = [c.post("/api/read-xls", data=b"hello", headers={"Authorization": "Bearer x", "X-Forwarded-For": "9.9.9.9"}).status_code
         for _ in range(32)]
check(codes.count(429) >= 2, "rate limited after 30 per minute")
appmod._RL_HITS.clear()

print(f"xlsreader: {sum(results)} passed, {len(results) - sum(results)} failed")
sys.exit(0 if all(results) else 1)
