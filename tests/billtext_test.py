"""Free OCR-text bill parser tests (Sprint 6).

    python3 tests/billtext_test.py

parse_bill_text turns the plain text that on-device OCR reads off a bill photo
into form-ready fields, with no API call. These checks pin the field extraction
(merchant, total, date, category, frequency, type) and that garbage never crashes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from billreader import parse_bill_text  # noqa: E402

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def run():
    # ---- A typical electricity bill --------------------------------------
    power = """Acme Power & Light
    Invoice No: 55213
    Billing date: 15/03/2026
    Energy charges        Rs 1,240.00
    Fixed charges         Rs 110.50
    Grand Total           Rs 1,350.50
    Due monthly"""
    r = parse_bill_text(power)
    check(r["name"] == "Acme Power & Light", f"merchant name read (got {r['name']!r})")
    check(r["amount"] == 1350.50, f"grand total wins over line items (got {r['amount']})")
    check(r["type"] == "expense", "a bill is an expense")
    check(r["date"] == "2026-03-15", f"dd/mm/yyyy date parsed (got {r['date']})")
    check(r["category_guess"] == "Utilities", f"power -> Utilities (got {r['category_guess']})")
    check(r["frequency_guess"] == "monthly", f"'monthly' detected (got {r['frequency_guess']})")
    check(r["ocr"] is True, "result is flagged as the free OCR path")
    check(0 < r["confidence"] <= 0.95, f"confidence in range (got {r['confidence']})")

    # ---- Currency symbol + ISO date + internet category ------------------
    net = "Jio Fiber\nBroadband plan\nAmount due: ₹ 799.00\nDate 2026-02-01"
    r = parse_bill_text(net)
    check(r["amount"] == 799.00, f"rupee + amount-due parsed (got {r['amount']})")
    check(r["date"] == "2026-02-01", f"ISO date parsed (got {r['date']})")
    check(r["category_guess"] == "Internet", f"broadband -> Internet (got {r['category_guess']})")

    # ---- Month-name date + income detection ------------------------------
    pay = "ACME CORP\nPayslip for March\nNet salary credited\nTotal 85,000.00\n28 Mar 2026"
    r = parse_bill_text(pay)
    check(r["type"] == "income", "a payslip/salary is income")
    check(r["date"] == "2026-03-28", f"'28 Mar 2026' parsed (got {r['date']})")
    check(r["amount"] == 85000.00, f"comma thousands parsed (got {r['amount']})")

    # ---- Rent, 'per month' -> monthly ------------------------------------
    rent = "Monthly house rent\nAmount payable $1,800.00 per month"
    r = parse_bill_text(rent)
    check(r["category_guess"] == "Rent", f"rent -> Rent (got {r['category_guess']})")
    check(r["frequency_guess"] == "monthly", f"'per month' -> monthly (got {r['frequency_guess']})")
    check(r["amount"] == 1800.00, f"dollar amount parsed (got {r['amount']})")

    # ---- Garbage / empty never crashes -----------------------------------
    for bad in ["", "   ", "no numbers or useful text here", None]:
        r = parse_bill_text(bad)
        check(r["amount"] is None, f"no amount from garbage {bad!r} (got {r['amount']})")
        check(r["type"] == "expense", "garbage defaults to expense")
        check("confidence" in r, "garbage still returns a well-formed result")

    # ---- Absurd amount is rejected ---------------------------------------
    r = parse_bill_text("Total Rs 9999999999999999.00")
    check(r["amount"] is None, f"out-of-range amount rejected (got {r['amount']})")


def main():
    run()
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nbilltext_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All bill-text parser checks passed.")


if __name__ == "__main__":
    main()
