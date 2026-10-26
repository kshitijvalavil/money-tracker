"""Tests for bot/parsers.py.

Uses synthetic SBI-style alert text (no real personal data) that matches the
real email template structure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.parsers import parse_email, parse_sbi_alert

SENDER = "cbsalerts.sbi@alerts.sbi.bank.in"
TMPL = ("Greetings from SBI ! Your A/C XXXXX{acct} {dir} INR {amt} on {date} "
        "-{note}. Avl Bal INR {bal}-SBI Please do not reply to this auto generated email.")

# Synthetic cases: (acct, amount string, dd/mm/yy, direction, expected float)
CASES = [
    ("000001", "7,000.00", "26/10/26", "Credited", 7000.0),
    ("000002", "10,000.00", "06/10/26", "Credited", 10000.0),
    ("000001", "600.00", "03/09/26", "Credited", 600.0),
    ("000001", "1,250.50", "15/10/26", "Debited", 1250.50),
    ("000002", "99.00", "01/08/26", "Debited", 99.0),
]

fails = 0
def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg); fails += 1

for acct, amt, date, direction, expected in CASES:
    body = TMPL.format(acct=acct, dir=direction, amt=amt, date=date,
                       note="UPI transfer", bal="1,234.56")
    t = parse_email("CBSSBI ALERT", SENDER, body, message_id="id-" + date + direction)
    check(t is not None, f"parsed {date} {direction}")
    check(t["amount"] == expected, f"{date} amount == {expected} (got {t['amount']})")
    check(t["direction"] == ("income" if direction == "Credited" else "expense"),
          f"{date} direction from {direction}")
    check(t["account_hint"] == acct, f"{date} account tail {acct}")
    check(t["occurred_on"].startswith("2026-"), f"{date} iso -> {t['occurred_on']}")
    check(t["source_message_id"] == "id-" + date + direction, f"{date} dedup id kept")

# Detailed field extraction (synthetic values)
t = parse_sbi_alert("CBSSBI ALERT", SENDER,
    "Your A/C XXXXX000001 Credited INR 5,000.00 on 15/01/26 "
    "-UPI transfer. Avl Bal INR 1,234.56-SBI")
check(t["balance_after"] == 1234.56, f"balance ({t['balance_after']})")
check(t["occurred_on"] == "2026-01-15", f"exact date ({t['occurred_on']})")
check("UPI transfer" in t["note"], f"note ({t['note']!r})")

# Non-transaction emails are ignored
check(parse_email("Your pre-approved loan", "sbi@communications.sbi.co.in",
                  "Download YONO and get a loan today!") is None, "marketing ignored")
check(parse_email("Groww Digest", "noreply@digest.groww.in",
                  "Sensex up 0.66% today") is None, "newsletter ignored")
check(parse_email("Your Zomato order", "noreply@zomato.com",
                  "Your order was delivered") is None, "non-bank sender ignored")

print(f"\n{'ALL PARSER TESTS PASSED' if fails == 0 else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
