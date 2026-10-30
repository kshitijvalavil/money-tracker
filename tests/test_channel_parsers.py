"""Tests for the Sprint 6 bank-alert and Google Pay parsers and the channel
rule: any UPI payment -> "Google Pay", any other bank transaction -> the bank.

All samples are synthetic (made-up names, accounts and references) written to
match the structure of real alert templates.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.parsers import parse_email
from bot.scanner import to_row

fails = 0


def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg)
        fails += 1


def parse(sender, subject, body, email_date="2026-10-29"):
    return parse_email(subject, sender, body, message_id="m-" + str(abs(hash(body))), email_date=email_date)


# (sender, subject, body, direction, amount, date, account, channel, note contains)
CASES = [
    ("alerts@hdfcbank.net", "You have done a UPI txn. Check details!",
     "Dear Customer, Rs.250.00 has been debited from account **4321 to VPA ravi.k@okicici RAVI K "
     "on 29-10-26. Your UPI transaction reference number is 612345678901.",
     "expense", 250.0, "2026-10-29", "4321", "Google Pay", "ravi.k@okicici"),
    ("alerts@hdfcbank.net", "Alert : Update on your HDFC Bank Credit Card",
     "Thank you for using your HDFC Bank Credit Card ending 9876 for Rs 1,499.00 at AMAZON PAY on "
     "28-10-2026 14:22:10. Authorization code:- 012345",
     "expense", 1499.0, "2026-10-28", "9876", "HDFC Bank", "AMAZON PAY"),
    ("BankAlerts@kotak.com", "Kotak Bank: Transaction alert",
     "Sent Rs.120.00 from Kotak Bank AC X5678 to chai.point@oksbi on 27-10-26. UPI Ref 600011112222.",
     "expense", 120.0, "2026-10-27", "5678", "Google Pay", "chai.point@oksbi"),
    ("credit_cards@icicibank.com", "Transaction alert for your ICICI Bank Credit Card",
     "Your ICICI Bank Credit Card XX4455 has been used for a transaction of INR 2,340.00 on Oct 26, 2026 "
     "at DECATHLON SPORTS. Available Credit Limit: INR 45,000.00.",
     "expense", 2340.0, "2026-10-26", "4455", "ICICI Bank", "DECATHLON SPORTS"),
    ("alerts@axisbank.com", "Credit alert",
     "INR 15,000.00 credited to A/c no. XX7788 on 25-10-2026 at 10:01:44 IST by NEFT from ACME PVT LTD.",
     "income", 15000.0, "2026-10-25", "7788", "Axis Bank", "Axis Bank credit"),
    ("noreply@sliceit.com", "Payment successful",
     "You spent ₹349 at Swiggy on 24 Oct 2026 using your slice card ending 1122.",
     "expense", 349.0, "2026-10-24", "1122", "Slice", "Swiggy"),
    ("payments-noreply@google.com", "You paid ₹500.00 to Asha",
     "You paid ₹500.00 to Asha Nair on 23 Oct 2026. UPI transaction ID: 123456789012",
     "expense", 500.0, "2026-10-23", None, "Google Pay", "Asha Nair"),
    ("payments-noreply@google.com", "Asha Nair paid you ₹200.00",
     "Asha Nair paid you ₹200.00 on 22 Oct 2026.",
     "income", 200.0, "2026-10-22", None, "Google Pay", "Asha Nair"),
    ("cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
     "Greetings from SBI ! Your A/C XXXXX000001 Debited INR 75.00 on 21/10/26 -UPI/DR/600011/CAFE/YESB. "
     "Avl Bal INR 1,000.00-SBI",
     "expense", 75.0, "2026-10-21", "000001", "Google Pay", "UPI"),
    ("cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
     "Greetings from SBI ! Your A/C XXXXX000001 Credited INR 2,000.00 on 20/10/26 -Deposit by transfer "
     "from Mr. Test User. Avl Bal INR 3,000.00-SBI",
     "income", 2000.0, "2026-10-20", "000001", "SBI", "Deposit by transfer"),
]

for sender, subject, body, direction, amount, date, acct, channel, note in CASES:
    t = parse(sender, subject, body)
    tag = f"{sender} / {subject}"
    if direction is None:
        check(t is None, f"{tag}: should be skipped (got {t})")
        continue
    check(t is not None, f"{tag}: parsed")
    if not t:
        continue
    check(t["direction"] == direction, f"{tag}: direction {t['direction']} != {direction}")
    check(t["amount"] == amount, f"{tag}: amount {t['amount']} != {amount}")
    check(t["occurred_on"] == date, f"{tag}: date {t['occurred_on']} != {date}")
    check(t["account_hint"] == acct, f"{tag}: account {t['account_hint']} != {acct}")
    check(t["channel"] == channel, f"{tag}: channel {t['channel']} != {channel}")
    check(note in t["note"], f"{tag}: note {t['note']!r} lacks {note!r}")
    row = to_row(t)
    check(row is not None and row["channel"] == channel, f"{tag}: channel survives into the import row")

# Marketing and lookalike senders must never become transactions.
NOT_TRANSACTIONS = [
    ("update@banking2day.com", "Update: Your HDFC Bank credit card options",
     "Get an HDFC Bank credit card with Rs 5,000 cashback. Apply now."),
    ("noreply@update.banking2day.com", "Your YES BANK credit card Options",
     "Spent Rs 10,000? Earn rewards worth Rs 500 with the new card."),
    ("offers@hdfcbank.net", "Pre-approved offer for you",
     "You are eligible for a pre-approved personal loan of Rs 5,00,000. Apply now."),
    ("gwg-noreply@google.com", "Your Google Workspace invoice",
     "Your invoice for Rs 136.00 is ready."),
    ("alerts@hdfcbank.net.evil.example", "UPI txn",
     "Rs.250.00 has been debited from account **4321 to VPA x@okaxis on 29-10-26."),
    ("alerts@notkotak.com", "Transaction alert",
     "Sent Rs.120.00 from AC X5678 to y@oksbi on 27-10-26."),
]
for sender, subject, body in NOT_TRANSACTIONS:
    check(parse(sender, subject, body) is None, f"not a transaction: {sender} / {subject}")

# Missing date in the body falls back to the email date.
t = parse("alerts@axisbank.com", "Debit alert", "INR 99.00 debited from A/c no. XX7788. UPI/P2M/123/SHOP",
          email_date="2026-10-18")
check(t and t["occurred_on"] == "2026-10-18", f"date falls back to the email date ({t and t['occurred_on']})")
check(t and t["channel"] == "Google Pay", "UPI in the narration -> Google Pay")

# Receipts carry no channel.
t = parse("noreply@zomato.com", "Your Zomato order", "Grand Total ₹412.00 Order placed 19/10/2026")
check(t and t["channel"] is None and to_row(t)["channel"] is None, "receipts have no channel")

if fails:
    print(f"{fails} CHANNEL PARSER CHECK(S) FAILED")
    sys.exit(1)
print("ALL CHANNEL PARSER TESTS PASSED")
