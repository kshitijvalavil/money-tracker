"""Tests for bot/review.py (Sprint 9): duplicate merging and category rules.
Synthetic rows only. Run: python3 tests/test_review.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import review  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg)
        fails += 1


def row(mid, source, amount, day="2026-11-02", channel=None, note="", ts=None,
        direction="expense", hint=None):
    r = {"source": source, "direction": direction, "amount": amount, "occurred_on": day,
         "account_hint": None, "note": note, "source_message_id": mid, "channel": channel}
    if ts is not None:
        r["ts"] = ts
    if hint:
        r["category_hint"] = hint
    return r


T = 1793650000
swiggy = row("s1", "receipt", 450, note="Swiggy order", ts=T, hint="Food")
gpay = row("g1", "google_pay", 450, channel="Google Pay", note="Paid Swiggy", ts=T + 60)
hdfc = row("h1", "hdfc_alert", 450, channel="Google Pay", note="UPI to swiggy@icici", ts=T + 120)

# --- three emails, one payment -------------------------------------------------
out = review.merge_duplicates([hdfc, gpay, swiggy])
check(len(out) == 1, f"3 emails -> 1 row ({len(out)})")
m = out[0]
check(m["source_message_id"] == "s1", "receipt is the main row")
check(m["merchant"] == "Swiggy", f"merchant from receipt ({m.get('merchant')})")
check(set(m["merged_message_ids"]) == {"g1", "h1"}, "other emails recorded")
check(m["merged_sources"] == ["Google Pay", "HDFC"], f"paid-by detail ({m['merged_sources']})")
check(m["channel"] == "Google Pay", "receipt picks up the Google Pay channel")

# --- two real orders of the same amount stay separate --------------------------
two = review.merge_duplicates([swiggy, dict(swiggy, source_message_id="s2"), gpay])
check(len(two) == 2, f"two receipts never merge with each other ({len(two)})")

# --- different amount / day / direction / far apart in time ---------------------
check(len(review.merge_duplicates([swiggy, dict(gpay, amount=452)])) == 2, "different amount stays apart")
check(len(review.merge_duplicates([swiggy, dict(gpay, occurred_on="2026-11-03")])) == 2, "different day stays apart")
check(len(review.merge_duplicates([swiggy, dict(gpay, direction="income")])) == 2, "income never merges with expense")
check(len(review.merge_duplicates([swiggy, dict(gpay, ts=T + 31 * 60)])) == 2, "over 30 minutes apart stays apart")
no_ts = {k: v for k, v in gpay.items() if k != "ts"}
check(len(review.merge_duplicates([swiggy, no_ts])) == 1, "no timestamp: same day merges")

# --- single rows pass through with empty merge fields -----------------------------
one = review.merge_duplicates([hdfc])[0]
check(one["merged_message_ids"] == [] and one["merged_sources"] == [], "lone row has empty merge lists")
check(one["merchant"] == "UPI to swiggy@icici", "merchant falls back to the note")

# --- rules -----------------------------------------------------------------------
cats = [{"id": "c-food", "name": "Food", "kind": "expense"},
        {"id": "c-groc", "name": "Groceries", "kind": "expense"},
        {"id": "c-sal", "name": "Salary", "kind": "income"}]
rules = [{"match_text": "swiggy", "category_id": "c-food"},
         {"match_text": "swiggy instamart", "category_id": "c-groc"},
         {"match_text": "acme", "category_id": "c-sal"}]
r = review.apply_rules([dict(swiggy)], rules, cats)[0]
check(r["suggested_category_id"] == "c-food", "rule matches the merchant")
r = review.apply_rules([row("i1", "receipt", 300, note="Swiggy Instamart order")], rules, cats)[0]
check(r["suggested_category_id"] == "c-groc", "longer rule wins")
r = review.apply_rules([row("x1", "hdfc_alert", 300, note="ACME refund")], rules, cats)[0]
check(r["suggested_category_id"] is None, "income rule never applies to an expense")
r = review.apply_rules([row("z1", "receipt", 99, note="Zomato order", hint="Food")], [], cats)[0]
check(r["suggested_category_id"] == "c-food", "parser hint used when no rule")
r = review.apply_rules([row("n1", "hdfc_alert", 99, note="ATM")], [], cats)[0]
check(r["suggested_category_id"] is None, "no rule, no hint -> no suggestion")

# --- to_save drops helper keys -----------------------------------------------------
saved = review.to_save(review.apply_rules(review.merge_duplicates([swiggy, gpay]), rules, cats)[0])
check("ts" not in saved and "category_hint" not in saved, "helper keys dropped")
check(saved["suggested_category_id"] == "c-food" and saved["merchant"] == "Swiggy", "saved row complete")

print("review tests:", "OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
