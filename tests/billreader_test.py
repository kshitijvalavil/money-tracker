"""Unit tests for billreader: validation, cleaning, and reply parsing.

    python3 tests/billreader_test.py

No network: the one model-call path is exercised with a stubbed _call_model.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billreader
from billreader import BillReadError, _clean, read_bill

results = []


def check(cond, msg):
    results.append(bool(cond))
    if not cond:
        print("  FAIL:", msg)


def expect_error(status, fn):
    try:
        fn()
        check(False, f"expected BillReadError {status}, got a result")
    except BillReadError as e:
        check(e.status == status, f"expected status {status}, got {e.status} ({e.message})")


# --- Demo path needs no key and returns a usable sample --------------------
d = read_bill("", "", demo=True)
check(d["name"] and d["amount"] and d["type"] == "expense", "demo sample has name/amount/type")
check(d.get("demo") is True, "demo sample is flagged demo")

# --- Validation ------------------------------------------------------------
expect_error(400, lambda: read_bill("", "image/jpeg", api_key="k"))           # no image
expect_error(400, lambda: read_bill("abc", "application/pdf", api_key="k"))   # bad mime
expect_error(503, lambda: read_bill("abc", "image/jpeg", api_key=None))       # no key

# --- Cleaning coerces model output into safe, form-ready values ------------
c = _clean({"name": "X" * 200, "amount": "12.5", "type": "weird", "date": "nope",
            "category_guess": "  Food ", "frequency_guess": "biweekly", "confidence": "2"})
check(len(c["name"]) == 80, "name is clipped to 80 chars")
check(c["amount"] == 12.5, "amount parsed from string")
check(c["type"] == "expense", "unknown type falls back to expense")
check(c["date"] is None, "invalid date becomes null")
check(c["category_guess"] == "Food", "category is trimmed")
check(c["frequency_guess"] is None, "unknown frequency becomes null")
check(c["confidence"] == 1.0, "confidence is clamped to 1")
check(_clean({"amount": "abc"})["amount"] is None, "unparseable amount is null")
check(_clean({"amount": -5})["amount"] is None, "non-positive amount is null")
check(_clean({"amount": 1e13})["amount"] is None, "too-large amount is null")

# --- Reply parsing (model call stubbed) ------------------------------------
_orig = billreader._call_model
try:
    billreader._call_model = lambda body, key, timeout: {"content": [
        {"type": "text", "text": 'Sure: {"name":"Acme","amount":10,"type":"expense",'
                                 '"date":"2026-01-02","category_guess":null,'
                                 '"frequency_guess":"monthly","confidence":0.8}'}]}
    r = read_bill("abc", "image/jpeg", api_key="k")
    check(r["name"] == "Acme" and r["amount"] == 10.0, "parses JSON embedded in prose")
    check(r["date"] == "2026-01-02" and r["frequency_guess"] == "monthly", "keeps valid date/frequency")

    billreader._call_model = lambda body, key, timeout: {"content": [{"type": "text", "text": "sorry, can't read it"}]}
    expect_error(422, lambda: read_bill("abc", "image/jpeg", api_key="k"))

    billreader._call_model = lambda body, key, timeout: {"content": [{"type": "text", "text": '{"name":"","amount":null}'}]}
    expect_error(422, lambda: read_bill("abc", "image/jpeg", api_key="k"))
finally:
    billreader._call_model = _orig

passed = sum(results)
print(f"\nbillreader_test: {passed}/{len(results)} checks passed")
if passed != len(results):
    sys.exit(1)
print("All billreader checks passed.")
