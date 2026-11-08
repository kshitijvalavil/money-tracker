"""Full card numbers are never kept (Sprint 14).

    python3 tests/test_card_masking.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.parsers import mask_card_numbers, parse_email  # noqa: E402

fails = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# 4111 1111 1111 1111 is the standard Visa test number (passes the Luhn checksum).
check(mask_card_numbers("Paid with 4111 1111 1111 1111 today") == "Paid with card ending 1111 today", "spaced 16-digit card is masked")
check(mask_card_numbers("card 4111-1111-1111-1111.") == "card card ending 1111.", "dashed card is masked")
check(mask_card_numbers("4111111111111111") == "card ending 1111", "plain 16-digit card is masked")
check(mask_card_numbers("378282246310005") == "card ending 0005", "15-digit (Amex style) card is masked")
check(mask_card_numbers("UPI ref 412345678901") == "UPI ref 412345678901", "12-digit UPI reference is left alone")
check(mask_card_numbers("Ref 1234567890123456") == "Ref 1234567890123456", "16 digits that fail the card checksum are left alone")
check(mask_card_numbers("Rs 1,23,456.00 on 03/11/2026") == "Rs 1,23,456.00 on 03/11/2026", "amounts and dates are left alone")
check(mask_card_numbers(None) is None and mask_card_numbers("") == "", "empty input is safe")

body = ("Your HDFC Bank Credit Card 4111 1111 1111 1111 has been used for Rs 450.00 "
        "at Swiggy on 03-11-2026. Available limit Rs 50,000.")
row = parse_email("Alert: Credit card used", "alerts@hdfcbank.net", body, "m1", "2026-11-03")
if row:
    blob = " ".join(str(v) for v in row.values())
    check("4111" not in blob.replace("1111", "") and "4111 1111" not in blob and "4111111111111111" not in blob,
          "a parsed email never carries the full card number")
else:
    check(True, "(email not parsed as a transaction; nothing to leak)")

print("\ntest_card_masking:", "ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
