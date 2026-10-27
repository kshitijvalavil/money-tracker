"""Tests for bot/scanner.py and bot/gmail_source.py.

Synthetic emails only (tests/fixtures). No network: urlopen is mocked.
Run: python3 tests/test_scanner.py
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot import gmail_source
from bot.scanner import (ROW_KEYS, build_push_request, load_dir, push_to_supabase,
                         scan)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
SENDER = "cbsalerts.sbi@alerts.sbi.bank.in"

fails = 0
def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg); fails += 1


def sbi(mid, direction="Debited", amt="100.00", date="15/10/26"):
    return {"id": mid, "subject": "CBSSBI ALERT", "sender": SENDER, "date": "2026-10-20",
            "body": f"Your A/C XXXXX000001 {direction} INR {amt} on {date} -UPI. Avl Bal INR 1.00-SBI"}


# --- row shape ----------------------------------------------------------------
rows = scan([sbi("m1", "Credited", "5,000.00")])
check(len(rows) == 1, "one row from one alert")
r = rows[0]
check(set(r) == set(ROW_KEYS), f"row has exactly pending_imports keys ({sorted(r)})")
check("user_id" not in r and "status" not in r, "no user_id/status (db defaults)")
check(r["direction"] == "income" and r["amount"] == 5000.0, "credit -> income 5000")
check(r["occurred_on"] == "2026-10-15", f"date from body ({r['occurred_on']})")
check(r["account_hint"] == "000001", "account hint kept")
check(r["source"] == "sbi_alert" and r["source_message_id"] == "m1", "source + message id")
check(isinstance(r["note"], str), "note is a string")
json.dumps(rows)  # serialisable

# --- non-transactions ignored ---------------------------------------------------
junk = [
    {"id": "n1", "subject": "Loan", "sender": "sbi@communications.sbi.co.in",
     "body": "Download YONO and get a loan today!", "date": "2026-10-01"},
    {"id": "n2", "subject": "Digest", "sender": "noreply@medium.com",
     "body": "Total reads: Rs. 10", "date": "2026-10-01"},
    {"id": "n3", "subject": "Bill", "sender": "noreply@district.in",
     "body": "Please find the bill attached.", "date": "2026-10-01"},
    {"id": "", "subject": "x", "sender": SENDER, "body": sbi("x")["body"]},  # no id
]
check(scan(junk) == [], "marketing/newsletter/attachment-only/no-id dropped")

# Receipt with no date in body falls back to the email's date (RFC 2822 too).
rc = scan([{"id": "z1", "subject": "order", "sender": "noreply@zomato.com",
            "body": "Order TotalRs.123.00", "date": "Thu, 01 Oct 2026 10:00:00 +0530"}])
check(len(rc) == 1 and rc[0]["occurred_on"] == "2026-10-01", f"receipt date fallback ({rc})")
# ... and is dropped if there is no date at all (occurred_on is NOT NULL).
check(scan([{"id": "z2", "subject": "order", "sender": "noreply@zomato.com",
             "body": "Order TotalRs.123.00"}]) == [], "dateless receipt dropped")

# --- dedupe ---------------------------------------------------------------------
batch = [sbi("a"), sbi("b"), sbi("a", amt="999.00"), sbi("c"), sbi("b")]
ids = [r["source_message_id"] for r in scan(batch)]
check(ids == ["a", "b", "c"], f"in-batch duplicates dropped, first wins ({ids})")
check(scan(batch)[0]["amount"] == 100.0, "first occurrence kept")
ids = [r["source_message_id"] for r in scan(batch, existing_ids={"a", "c"})]
check(ids == ["b"], f"existing ids skipped ({ids})")
check(scan(iter(batch), existing_ids=iter(["b"])) != [], "accepts plain iterators")

# --- fixtures dir (.eml) ----------------------------------------------------------
fx = scan(load_dir(FIXTURES))
check(len(fx) == 3, f"3 transactions in fixtures ({len(fx)})")
check({r["source"] for r in fx} == {"sbi_alert", "receipt"}, "both parsers hit")
check(all(r["source_message_id"].startswith("fixture-") for r in fx), "Message-ID used as id")

# --- push_to_supabase request building ------------------------------------------
captured = []
class FakeResp(io.BytesIO):
    status = 201
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake_urlopen(req, timeout=None):
    captured.append(req)
    return FakeResp(b"")

status = push_to_supabase(fx, "https://proj.supabase.co/", "USER.JWT.TOKEN",
                          anon_key="ANON", opener=fake_urlopen)
check(status == 201, "returns HTTP status")
check(len(captured) == 1, "one bulk request")
req = captured[0]
check(req.get_method() == "POST", "POST")
check(req.full_url.startswith("https://proj.supabase.co/rest/v1/pending_imports"),
      f"endpoint ({req.full_url})")
check("on_conflict=user_id,source_message_id" in req.full_url, "on_conflict on unique key")
h = {k.lower(): v for k, v in req.header_items()}
check(h.get("apikey") == "ANON", "apikey header = anon key")
check(h.get("authorization") == "Bearer USER.JWT.TOKEN", "Bearer = user's access token")
check("resolution=ignore-duplicates" in h.get("prefer", ""), "Prefer ignore-duplicates")
check(h.get("content-type") == "application/json", "json content type")
sent = json.loads(req.data.decode())
check(sent == fx, "body is the rows")
check(all("user_id" not in s for s in sent), "user_id never sent (RLS/default fills it)")

captured.clear()
check(push_to_supabase([], "https://x", "jwt", anon_key="a", opener=fake_urlopen) is None
      and not captured, "empty batch sends nothing")
try:
    build_push_request(fx, "https://x", "", anon_key="a")
    check(False, "missing user JWT must raise")
except ValueError:
    pass

# --- gmail_source parsing of a canned API response ---------------------------------
canned = json.load(open(os.path.join(FIXTURES, "gmail_api_response.json")))
m1, m2 = [gmail_source.parse_api_message(m) for m in canned["messages"]]
check(m1["id"] == "18f0000000000001", "gmail id")
check(m1["subject"] == "CBSSBI ALERT" and m1["sender"] == SENDER, "headers parsed")
check(m1["date"] == "2026-10-01", f"internalDate -> ISO ({m1['date']})")
check("Debited INR 99.00" in m1["body"] and "<p>" not in m1["body"], "text/plain preferred")
check(m2["sender"].endswith("<noreply@zomato.com>"), "lower-case header names handled")
check("Rs.123.00" in m2["body"] and "<div>" not in m2["body"] and "color" not in m2["body"],
      f"html-only body stripped ({m2['body']!r})")

g = scan([m1, m2])
check([(r["source"], r["amount"]) for r in g] == [("sbi_alert", 99.0), ("receipt", 123.0)],
      f"gmail messages scan end-to-end ({g})")

# fetch_messages walks list -> get with the bearer token (mocked network).
calls = []
by_id = {m["id"]: m for m in canned["messages"]}
def gmail_urlopen(req, timeout=None):
    calls.append(req)
    url = req.full_url
    if "/messages?" in url:
        return FakeResp(json.dumps(canned["list"]).encode())
    mid = url.split("/messages/")[1].split("?")[0]
    return FakeResp(json.dumps(by_id[mid]).encode())
got = list(gmail_source.fetch_messages(token="GTOKEN", query="from:(alerts.sbi.bank.in)",
                                       opener=gmail_urlopen))
check([m["id"] for m in got] == list(by_id), "fetch_messages yields every listed message")
check(all(dict(c.header_items()).get("Authorization") == "Bearer GTOKEN" for c in calls),
      "gmail calls carry the token")
check("q=from%3A%28alerts.sbi.bank.in%29" in calls[0].full_url, "query passed to list")
check(all(c.get_method() == "GET" for c in calls), "gmail access is read-only (GET)")
calls.clear()
check(len(list(gmail_source.fetch_messages(token="T", max_messages=1, opener=gmail_urlopen))) == 1,
      "max_messages respected")

print(f"\n{'ALL SCANNER TESTS PASSED' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
