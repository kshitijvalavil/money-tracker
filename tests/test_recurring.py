"""Tests for bot/recurring.py, the reference for suggest_recurring() in SQL.

Run: python3 -m pytest tests/test_recurring.py   (or plain python3 tests/test_recurring.py)

The fixture rows (tests/fixtures/recurring_transactions.csv, synthetic) are
shared with tests/suggest_recurring_test.sql. detect() on them must equal
tests/fixtures/recurring_expected.csv, and the SQL test checks that
suggest_recurring() returns the same rows. To regenerate the expected file
after an intentional rule change:
    REGEN_RECURRING_EXPECTED=1 python3 -m pytest tests/test_recurring.py
"""
import csv
import os
import sys
from datetime import date
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot.recurring import detect, normalise, occurrence  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
TX_CSV = os.path.join(FIXTURES, "recurring_transactions.csv")
EXPECTED_CSV = os.path.join(FIXTURES, "recurring_expected.csv")
EXPECTED_COLS = ["name", "type", "amount", "frequency", "account", "category",
                 "last_seen", "next_guess", "occurrences", "confidence"]


def load_fixture():
    with open(TX_CSV, newline="") as f:
        return [{"occurred_on": r["occurred_on"], "amount": r["amount"], "type": r["type"],
                 "note": r["note"], "account_id": r["account"], "category_id": r["category"] or None}
                for r in csv.DictReader(f)]


def by_name(suggestions):
    return {normalise(s["name"]): s for s in suggestions}


def tx(d, amount, note="Thing", typ="expense", acct="Bank", cat="Bills"):
    return {"occurred_on": d, "amount": amount, "type": typ, "note": note,
            "account_id": acct, "category_id": cat}


# --- occurrence(): anchored date math --------------------------------------------

def test_month_end_clamping_is_anchored():
    s = date(2026, 1, 31)
    assert [occurrence(s, "monthly", n) for n in range(5)] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]


def test_leap_year_february():
    assert occurrence(date(2028, 1, 31), "monthly", 1) == date(2028, 2, 29)
    assert occurrence(date(2027, 1, 30), "monthly", 1) == date(2027, 2, 28)
    assert occurrence(date(2027, 1, 29), "monthly", 1) == date(2027, 2, 28)


def test_yearly_on_29_feb():
    s = date(2028, 2, 29)
    assert occurrence(s, "yearly", 1) == date(2029, 2, 28)
    assert occurrence(s, "yearly", 4) == date(2032, 2, 29)


def test_quarterly_and_weekly():
    assert occurrence(date(2026, 11, 30), "quarterly", 1) == date(2027, 2, 28)
    assert occurrence(date(2026, 11, 30), "quarterly", 2) == date(2027, 5, 30)
    assert occurrence(date(2026, 12, 28), "weekly", 1) == date(2027, 1, 4)
    assert occurrence("2026-12-28", "weekly", 0) == date(2026, 12, 28)


def test_occurrence_bad_input():
    for bad in [("2026-01-01", "daily", 1), ("2026-01-01", "monthly", -1)]:
        try:
            occurrence(*bad)
        except ValueError:
            continue
        raise AssertionError(f"no error for {bad}")


# --- normalise() --------------------------------------------------------------------

def test_normalise():
    assert normalise("NETFLIX.COM 4829") == "netflix com"
    assert normalise("  Gym - FitLife ") == "gym fitlife"
    assert normalise("12345 !!") == ""
    assert normalise(None) == ""


# --- detect(): true positives on the shared fixture --------------------------------

def test_true_positives():
    s = by_name(detect(load_fixture()))
    nf = s["netflix com"]
    assert (nf["frequency"], nf["amount"], nf["occurrences"]) == ("monthly", Decimal("649.00"), 6)
    assert nf["account_id"] == "Card" and nf["category_id"] == "Entertainment"
    assert nf["next_guess"] == date(2026, 7, 5) and nf["confidence"] == Decimal("1.00")

    gym = s["gym fitlife"]
    assert gym["frequency"] == "weekly" and gym["occurrences"] == 8
    assert gym["last_seen"] == date(2026, 1, 26) and gym["next_guess"] == date(2026, 2, 2)

    ins = s["lic premium"]
    assert ins["frequency"] == "yearly" and ins["amount"] == Decimal("12000.00")
    assert ins["next_guess"] == date(2027, 3, 15)

    rent = s["rent"]
    assert rent["frequency"] == "monthly" and rent["occurrences"] == 5
    assert rent["next_guess"] == date(2026, 6, 30)  # anchored to the 31st, clamped

    sal = s["salary acme ltd"]
    assert sal["type"] == "income" and sal["frequency"] == "monthly"
    assert sal["amount"] == Decimal("50000.00")


# --- detect(): negatives --------------------------------------------------------------

def test_negatives_not_suggested():
    s = by_name(detect(load_fixture()))
    assert "bigbasket order" not in s, "random groceries"
    assert "laptop croma" not in s, "one-off big purchase"
    assert "electricity bill" not in s, "amounts drifting > 10%"
    assert "swiggy" not in s, "frequent spend outside every band"
    assert "apollo pharmacy" not in s, "chance cluster: under half the group kept"
    assert "blue tokai cafe" not in s, "median gap monthly but under 2/3 of gaps in band"
    assert "to savings" not in s, "transfers ignored"
    assert len(s) == 7


def test_drift_boundary_is_inclusive():
    rows = [tx(f"2026-0{m}-01", a) for m, a in [(1, 100), (2, 110), (3, 90), (4, 100)]]
    assert detect(rows)[0]["occurrences"] == 4                      # exactly 10% kept
    rows = [tx(f"2026-0{m}-01", a) for m, a in [(1, 100), (2, 111), (3, 89), (4, 100)]]
    assert detect(rows) == []                                       # only 2 left


def test_min_occurrences_and_bad_input():
    rows = [tx("2026-01-01", 100), tx("2026-02-01", 100)]
    assert detect(rows) == []
    assert len(detect(rows, min_occurrences=2)) == 1
    try:
        detect(rows, min_occurrences=1)
        raise AssertionError("min_occurrences=1 accepted")
    except ValueError:
        pass


def test_same_day_duplicates_count_once():
    rows = [tx("2026-01-01", 100), tx("2026-01-01", 100), tx("2026-02-01", 100)]
    assert detect(rows) == []                                       # 2 distinct dates
    rows.append(tx("2026-03-01", 100))
    (s,) = detect(rows)
    assert s["occurrences"] == 3 and s["frequency"] == "monthly"


def test_existing_recurring_excluded():
    s = by_name(detect(load_fixture(), existing_names=["Netflix.com", "RENT 2026"]))
    assert "netflix com" not in s and "rent" not in s and "gym fitlife" in s


def test_types_grouped_separately():
    rows = [tx(f"2026-0{m}-01", 100, note="Refund") for m in (1, 2, 3)]
    rows += [tx(f"2026-0{m}-15", 100, note="Refund", typ="income", cat="Gift") for m in (1, 2)]
    (s,) = detect(rows)
    assert s["type"] == "expense"


def test_median_gap_between_bands_is_rejected():
    # gaps 40,40,40 -> median 40: between monthly (<=35) and quarterly (>=85)
    rows = [tx(d, 100) for d in ["2026-01-01", "2026-02-10", "2026-03-22", "2026-05-01"]]
    assert detect(rows) == []


def test_two_thirds_of_gaps_must_be_in_band():
    # gaps 30,2,2,30,30: median 30 (monthly) but only 3/5 in band -> rejected
    days = ["2026-01-10", "2026-02-09", "2026-02-11", "2026-02-13", "2026-03-15", "2026-04-14"]
    assert detect([tx(d, 180) for d in days]) == []
    # gaps 30,2,30,30: 3/4 in band -> accepted
    days = ["2026-01-10", "2026-02-09", "2026-02-11", "2026-03-13", "2026-04-12"]
    (s,) = detect([tx(d, 180) for d in days])
    assert s["frequency"] == "monthly" and s["confidence"] == Decimal("0.75")
    # exactly 2/3 is enough: gaps 30,30,2
    days = ["2026-01-10", "2026-02-09", "2026-03-11", "2026-03-13"]
    assert len(detect([tx(d, 180) for d in days])) == 1


def test_kept_rows_must_be_at_least_half_the_group():
    # 3 monthly rows at 2000 hidden among 4 random amounts -> 3/7 kept: rejected
    rows = [tx(d, 2000) for d in ["2026-01-05", "2026-02-05", "2026-03-05"]]
    rows += [tx(d, a) for d, a in [("2026-01-12", 90), ("2026-01-20", 4000),
                                   ("2026-02-14", 700), ("2026-02-25", 3500)]]
    assert detect(rows) == []
    rows.pop()                                   # 3/6 kept: exactly half is enough
    assert len(detect(rows)) == 1


def test_random_spend_gives_no_suggestions():
    import json
    sets = json.load(open(os.path.join(FIXTURES, "sprint2", "random_spend.json")))["sets"]
    for i, rows in enumerate(sets):
        assert detect(rows) == [], f"random spend set {i}"


def test_confidence_reflects_irregularity():
    # gaps 31, 28, 31, 50: median 31 monthly; 3 of 4 gaps in band; 5/6 rows kept
    rows = [tx(d, 100) for d in ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-21"]]
    rows.append(tx("2026-06-01", 500))
    (s,) = detect(rows)
    assert s["confidence"] == Decimal("0.63")  # 3/4 * 5/6 = 0.625 -> half up


# --- the file the SQL test compares against ----------------------------------------

def _as_expected_rows(suggestions):
    return [[str(s["name"]), s["type"], str(s["amount"]), s["frequency"], s["account_id"],
             s["category_id"], s["last_seen"].isoformat(), s["next_guess"].isoformat(),
             str(s["occurrences"]), str(s["confidence"])] for s in suggestions]


def test_expected_csv_matches_detect():
    got = _as_expected_rows(detect(load_fixture()))
    if os.environ.get("REGEN_RECURRING_EXPECTED"):
        with open(EXPECTED_CSV, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(EXPECTED_COLS)
            w.writerows(got)
    with open(EXPECTED_CSV, newline="") as f:
        r = csv.reader(f)
        assert next(r) == EXPECTED_COLS
        assert list(r) == got


# Date-math grid shared with the SQL test (checks the SQL date math, and
# public.recurring_occurrence from 0007 when it exists, against occurrence()).
OCC_CSV = os.path.join(FIXTURES, "recurring_occurrences.csv")
OCC_STARTS = ["2026-01-31", "2026-01-30", "2026-01-29", "2027-01-31", "2028-02-29",
              "2026-03-31", "2026-08-31", "2026-11-30", "2026-12-28", "2026-05-15"]


def _occurrence_grid():
    return [[s, f, str(n), occurrence(s, f, n).isoformat()]
            for s in OCC_STARTS for f in ("weekly", "monthly", "quarterly", "yearly")
            for n in range(0, 25)]


def test_occurrence_grid_csv_matches():
    got = _occurrence_grid()
    if os.environ.get("REGEN_RECURRING_EXPECTED"):
        with open(OCC_CSV, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["start_date", "frequency", "n", "occurs_on"])
            w.writerows(got)
    with open(OCC_CSV, newline="") as f:
        r = csv.reader(f)
        assert next(r) == ["start_date", "frequency", "n", "occurs_on"]
        assert list(r) == got


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL:", name, repr(e))
    print(f"\n{'ALL RECURRING TESTS PASSED' if not failed else str(failed) + ' FAILURES'}")
    sys.exit(1 if failed else 0)
