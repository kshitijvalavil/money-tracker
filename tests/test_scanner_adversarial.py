"""Adversarial tests for the email scanner (QA, Sprint 1).

Malformed / hostile emails must never crash a scan, never produce rows the
database will refuse, and never lose good rows because of one bad email.

Checks made with known_bug() describe the behaviour we want but which the current
code does not have yet. They are reported as XFAIL (and as XPASS once fixed)
and do not fail the run, so CI stays green while the bug is open. Run with
QA_STRICT=1 to make them fail.

Run: python3 tests/test_scanner_adversarial.py
"""
import base64
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot import gmail_source
from bot.scanner import ROW_KEYS, build_push_request, load_dir, scan

STRICT = os.environ.get("QA_STRICT") == "1"
SENDER = "cbsalerts.sbi@alerts.sbi.bank.in"
MAX_AMOUNT = 999999999999.99          # numeric(14, 2) in pending_imports.amount

fails = 0
xfails = []


def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg)
        fails += 1


def known_bug(cond, bug, msg):
    """A check for an open bug: XFAIL while broken, XPASS once fixed."""
    global fails
    if cond:
        print(f"XPASS ({bug}): {msg} -- bug looks fixed, drop the tag")
    elif STRICT:
        print(f"FAIL ({bug}): {msg}")
        fails += 1
    else:
        xfails.append(f"{bug}: {msg}")


def msg(mid, body, sender=SENDER, subject="CBSSBI ALERT", date="2026-10-20"):
    return {"id": mid, "subject": subject, "sender": sender, "body": body, "date": date}


def write_eml(directory, name, raw):
    with open(os.path.join(directory, name), "wb") as f:
        f.write(raw if isinstance(raw, bytes) else raw.encode("utf-8"))


HDR = ("From: cbsalerts.sbi@alerts.sbi.bank.in\nSubject: CBSSBI ALERT\n"
       "Date: Mon, 26 Oct 2026 09:15:00 +0530\n")

# --- pure scan(): missing / weird fields --------------------------------------
check(scan([msg("n1", "Your A/C XXXXX000001 has been Debited on 20/10/26.")]) == [],
      "alert with no amount -> no row")
check(scan([msg("n2", "Your A/C XXXXX000001 Debited INR 0.00 on 20/10/26 -X.")]) == [],
      "zero amount -> no row (db requires amount > 0)")
check(scan([msg("n3", "Your A/C XXXXX000001 Debited INR 5.00 on 31/02/26 -X.")]) == [],
      "impossible date -> no row instead of a bad date")
check(scan([msg(None, "Debited INR 5.00 on 20/10/26")]) == [], "no message id -> no row")
check(scan([msg("n4", "")]) == [] and scan([msg("n5", "\x00\xff garbage")]) == [],
      "empty / binary body -> no row, no crash")
check(scan([{"id": "n6"}]) == [], "message with only an id -> no row, no crash")

r = scan([msg("p1", "Your A/C XXXXX000001 Debited INR 0.01 on 20/10/26 -X. Avl Bal INR 1")])
check(len(r) == 1 and r[0]["amount"] == 0.01, "smallest amount 0.01 survives")
r = scan([msg("p2", "Your A/C XXXXX000001 Credited INR 99,999,999,999.99 on 20/10/26 -X.")])
check(len(r) == 1 and r[0]["amount"] == 99999999999.99, "99,999,999,999.99 kept exactly")
r = scan([msg("p3", "Your A/C XXXXX000001 Credited INR 1,00,000.5 on 20/10/26 -X.")])
check(len(r) == 1 and r[0]["amount"] == 100000.5, "Indian digit grouping parsed")

# Amount larger than numeric(14,2): PostgREST would reject the WHOLE bulk
# insert (22003 numeric overflow), losing every good row in the same push.
r = scan([msg("big", "Your A/C XXXXX000001 Debited INR 12,34,56,78,90,123.45 on 20/10/26 -X."),
          msg("ok", "Your A/C XXXXX000001 Debited INR 10.00 on 20/10/26 -X.")])
check(all(row["amount"] <= MAX_AMOUNT for row in r),
      "SB-2 regression: scan() never emits an amount above numeric(14,2) max (would fail the whole push)")
check(any(row["source_message_id"] == "ok" for row in r), "good row next to a huge one is kept")

# Hostile text is passed through as data (the UI must escape it).
xss = "<img src=x onerror=alert(1)>"
r = scan([msg("x1", f"Your A/C XXXXX000001 Debited INR 1.00 on 20/10/26 -{xss}. Avl Bal INR 1")])
check(len(r) == 1 and xss in r[0]["note"], "note keeps hostile text verbatim (escaped at render)")
body = json.loads(build_push_request(r, "https://x.supabase.co", "jwt", "anon").data)
check(body[0]["note"] == r[0]["note"], "push payload round-trips hostile text unchanged")

# Every row has the same keys, so a PostgREST bulk insert is well-formed.
rows = scan([msg("k1", "A/C XXXXX000001 Debited INR 1.00 on 20/10/26 -a. Avl Bal INR 1"),
             msg("k2", "Order Total: Rs. 99.00", sender="orders@zomato.com")])
check(len(rows) == 2 and all(set(x) == set(ROW_KEYS) for x in rows),
      "mixed sources give identical key sets")

# --- .eml files: encodings, HTML-only, broken charsets ------------------------
with tempfile.TemporaryDirectory() as d:
    b64 = base64.b64encode("Your A/C XXXXX000003 Debited ₹ 2,345.60 on 20/10/26 -café. "
                           "Avl Bal INR 1.00".encode("utf-8")).decode()
    write_eml(d, "a_b64.eml", HDR + "Message-ID: <b64@x>\nContent-Type: text/plain; "
              "charset=utf-8\nContent-Transfer-Encoding: base64\n\n" + b64 + "\n")
    write_eml(d, "b_qp_latin1.eml", HDR + "Message-ID: <qp@x>\nContent-Type: text/plain; "
              "charset=iso-8859-1\nContent-Transfer-Encoding: quoted-printable\n\n"
              "Your A/C XXXXX000003 Debited INR 1.00 on 20/10/26 -caf=E9. Avl Bal INR 1.00\n")
    write_eml(d, "c_garbage.eml", b"garbage\x00\xff\xfe not an email at all")
    rows = {r["source_message_id"]: r for r in scan(load_dir(d))}
    check(rows.get("b64@x", {}).get("amount") == 2345.6 and rows["b64@x"]["note"] == "café",
          "base64 utf-8 body with ₹ parsed")
    check(rows.get("qp@x", {}).get("note") == "café", "quoted-printable latin-1 body decoded")
    check(len(rows) == 2, "garbage .eml ignored without a crash")

with tempfile.TemporaryDirectory() as d:
    write_eml(d, "html_only.eml", HDR + "Message-ID: <html@x>\nMIME-Version: 1.0\n"
              "Content-Type: text/html; charset=utf-8\n\n<html><body><p>Your A/C XXXXX000003 "
              "<b>Debited</b> INR 500.00 on 20/10/26 -NEFT. Avl Bal INR 1.00</p></body></html>\n")
    got = scan(load_dir(d))
    check(len(got) == 1 and got[0]["amount"] == 500.0,
          "SB-3 regression: HTML-only .eml is parsed like the Gmail path does (tags stripped)")

with tempfile.TemporaryDirectory() as d:
    write_eml(d, "a_good.eml", HDR + "Message-ID: <good@x>\nContent-Type: text/plain\n\n"
              "Your A/C XXXXX000003 Debited INR 10.00 on 20/10/26 -X. Avl Bal INR 1.00\n")
    write_eml(d, "b_badcharset.eml", HDR + "Message-ID: <bad@x>\nContent-Type: text/plain; "
              "charset=x-klingon\n\nYour A/C XXXXX000003 Debited INR 10.00 on 20/10/26 -X.\n")
    try:
        got = scan(load_dir(d))
        crashed = False
    except Exception as e:                  # LookupError: unknown encoding
        got, crashed = [], True
    check(not crashed and any(r["source_message_id"] == "good@x" for r in got),
          "SB-1 regression: one email with an unknown charset must not crash the whole --from-dir scan")

# --- Gmail API: HTML-only and malformed payloads ------------------------------
def api(mid, mime, text, **extra):
    data = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    return {"id": mid, "internalDate": "1792980000000",
            "payload": {"mimeType": mime, "headers": [{"name": "From", "value": SENDER}],
                        "body": {"data": data}}, **extra}

got = scan([gmail_source.parse_api_message(api(
    "g1", "text/html", "<p>Your A/C XXXXX000003 <b>Debited</b> INR 500.00 on 20/10/26 -NEFT. "
                       "Avl Bal INR 1.00</p><script>alert(1)</script>"))])
check(len(got) == 1 and got[0]["amount"] == 500.0 and "script" not in got[0]["note"],
      "Gmail HTML-only alert parsed, script stripped")
for bad in ({"id": "g2"},
            {"id": "g3", "payload": {"mimeType": "text/plain", "body": {"data": "!!!notb64"}}},
            {"id": "g4", "payload": {"mimeType": "multipart/mixed", "parts": None}},
            {"id": "g5", "payload": None, "snippet": None}):          # SB-8
    try:
        check(scan([gmail_source.parse_api_message(bad)]) == [], f"malformed Gmail {bad['id']} -> no row")
    except Exception as e:
        check(False, f"malformed Gmail {bad['id']} crashed: {type(e).__name__}: {e}")

for x in xfails:
    print("XFAIL", x)
print(f"\n{'ALL ADVERSARIAL SCANNER TESTS PASSED' if fails == 0 else str(fails) + ' FAILURES'}"
      + (f" ({len(xfails)} known bug(s) open)" if xfails else ""))
sys.exit(1 if fails else 0)
