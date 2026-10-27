"""Sprint 2 release gate (Sherlock Bugs): Python reference bot/recurring.py.

Independent of tests/test_recurring.py (Byte Vader's own). Checks the date
math and the detection against the shared fixtures in tests/fixtures/sprint2/,
which the SQL gate (tests/sprint2_adversarial_recurring.sql) uses too, so the
Python reference and suggest_recurring() are held to the same answers.

Written against docs/sprint-2-contract.md (Manager decisions 1-14) before
the build landed. The
contract names occurrence(start, freq, n) but not the detection function, so
this looks for detect / detect_recurring / suggest / suggest_recurring and
feeds it transactions shaped like the table rows
({occurred_on, type, amount, note}). If the build uses another name or shape,
fix the ADAPTER section only.

Run: python3 tests/sprint2_adversarial_detection.py
"""
import json
import os
import random
import re
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures", "sprint2")

results = []


def check(cond, msg):
    results.append((bool(cond), msg))
    if not cond:
        print("FAIL:", msg)


def finish():
    fails = [m for ok, m in results if not ok]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
    print("SPRINT 2 DETECTION GATE " + ("PASSED" if not fails else f"FAILED ({len(fails)})"))
    sys.exit(1 if fails else 0)


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", str(s or "").lower())).strip()


try:
    from bot import recurring
except Exception as e:                       # not built yet
    check(False, f"bot/recurring.py imports ({type(e).__name__}: {e})")
    finish()

# --- ADAPTER -------------------------------------------------------------------
occurrence = getattr(recurring, "occurrence", None)
check(callable(occurrence), "bot.recurring.occurrence exists")
DETECT = next((getattr(recurring, n) for n in
               ("suggest_recurring", "detect_recurring", "suggest", "detect")
               if callable(getattr(recurring, n, None))), None)
check(DETECT is not None, "bot.recurring has a detection function "
      "(suggest_recurring / detect_recurring / suggest / detect)")


def as_date(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def run_detect(txs, min_occ=3):
    """Call the detection function; returns list of dicts."""
    rows = [{"occurred_on": t["occurred_on"], "type": t["type"], "amount": t["amount"],
             "note": t["note"]} for t in txs]
    try:
        out = DETECT(rows, min_occ)
    except TypeError:
        rows = [dict(r, occurred_on=as_date(r["occurred_on"])) for r in rows]
        out = DETECT(rows, min_occ)
    return [o if isinstance(o, dict) else o._asdict() for o in out]


def call_occ(start, freq, n):
    try:
        return as_date(occurrence(start, freq, n))
    except (TypeError, ValueError, AttributeError):
        return as_date(occurrence(as_date(start), freq, n))
# --- end ADAPTER ---------------------------------------------------------------

# --- date math ----------------------------------------------------------------
if callable(occurrence):
    for c in json.load(open(os.path.join(FIX, "date_cases.json"))):
        try:
            got = call_occ(c["start"], c["freq"], c["n"]).isoformat()
        except Exception as e:
            got = f"{type(e).__name__}: {e}"
        check(got == c["expected"],
              f"occurrence({c['start']}, {c['freq']}, {c['n']}) = {c['expected']} ({c['why']}); got {got}")

    # No drift over long runs: every monthly occurrence of a 31st is the
    # month's last day or the 31st, never an earlier day after a short month.
    start = date(2024, 1, 31)
    for n in range(0, 60):
        d = call_occ(start, "monthly", n)
        nxt = d + timedelta(days=1)
        check(d.day == 31 or nxt.day == 1, f"monthly from 31 Jan, n={n}: {d} is month end")
    # Weekly is exactly 7 days apart, including across year ends.
    start = date(2025, 12, 1)
    prev = call_occ(start, "weekly", 0)
    for n in range(1, 60):
        d = call_occ(start, "weekly", n)
        check((d - prev).days == 7, f"weekly n={n} is 7 days after n={n - 1}")
        prev = d

    for bad in ("daily", "Monthly", "", None):
        try:
            call_occ("2024-01-31", bad, 1)
            check(False, f"occurrence rejects frequency {bad!r}")
        except (ValueError, KeyError, TypeError):
            check(True, f"occurrence rejects frequency {bad!r}")

# --- detection on the shared fixture -------------------------------------------
if DETECT:
    fx = json.load(open(os.path.join(FIX, "detection.json")))
    # Python has no `recurring` table: drop transactions covered by an existing
    # bill name the same way the SQL excludes them.
    covered = {norm(b["name"]) for b in fx["existing_bills"]}
    txs = [t for t in fx["transactions"] if t["type"] in ("income", "expense")
           and norm(t["note"]) not in covered]
    got = run_detect(txs, fx["min_occurrences"])
    by = {(norm(g.get("name")), g.get("type")): g for g in got}

    for e in fx["expected"]:
        g = by.get((e["name"], e["type"]))
        check(g is not None, f"true positive: {e['name']} ({e['type']}) suggested")
        if not g:
            continue
        check(g.get("frequency") == e["frequency"],
              f"{e['name']}: frequency {e['frequency']} (got {g.get('frequency')})")
        check(int(g.get("occurrences", -1)) == e["occurrences"],
              f"{e['name']}: occurrences {e['occurrences']} (got {g.get('occurrences')})")
        check(str(g.get("last_seen"))[:10] == e["last_seen"],
              f"{e['name']}: last_seen {e['last_seen']} (got {g.get('last_seen')})")
        if "next_guess" in e:
            check(str(g.get("next_guess"))[:10] == e["next_guess"],
                  f"{e['name']}: next_guess {e['next_guess']} (got {g.get('next_guess')})")
        if "amount" in e:
            check(abs(float(g.get("amount", 0)) - e["amount"]) < 0.005,
                  f"{e['name']}: amount {e['amount']} (got {g.get('amount')})")
        # Decision 2: confidence = (gaps in band / gaps) x (kept rows / rows);
        # every fixture true positive is clean, so exactly 1.
        conf = g.get("confidence")
        check(conf is not None and abs(float(conf) - 1) < 0.005, f"{e['name']}: confidence 1 (got {conf})")

    for name in fx["must_not_suggest"]:
        hits = [g for g in got if norm(g.get("name")) == name]
        check(not hits, f"false positive: {name!r} must not be suggested (got {hits})")
    extra = {k for k in by} - {(e["name"], e["type"]) for e in fx["expected"]}
    check(not extra, f"no suggestions beyond the expected set (extra: {sorted(extra)})")

    # min_occurrences is honoured.
    got4 = {norm(g.get("name")) for g in run_detect(txs, 4)}
    check("lic premium" not in got4 and "netflix" in got4, f"min_occurrences=4 drops LIC (3), keeps Netflix (4): {got4}")

    # Decision 13: random spend must not produce suggestions (shared fixture,
    # same sets the SQL gate uses, plus two more seeds).
    for k, noise in enumerate(json.load(open(os.path.join(FIX, "random_spend.json")))["sets"]):
        fp = run_detect(noise, 3)
        check(not fp, f"random_spend.json set {k}: no suggestions; got "
              f"{[(g.get('name'), g.get('frequency'), g.get('occurrences'), g.get('confidence')) for g in fp][:5]}")
    merchants = ["Swiggy", "Zomato", "Uber", "Ola", "BigBasket", "Myntra", "Apollo Pharmacy",
                 "Petrol", "Chai point", "Book store", "Decathlon", "DMart"]
    for seed in range(3, 5):
        rnd = random.Random(seed)
        noise = [{"occurred_on": (date(2025, 5, 1) + timedelta(days=rnd.randrange(365))).isoformat(),
                  "type": "expense", "amount": round(rnd.uniform(50, 5000), 2),
                  "note": rnd.choice(merchants)} for _ in range(300)]
        fp = run_detect(noise, 3)
        check(not fp, f"random spend (seed {seed}, 300 tx) gives no suggestions; got "
              f"{[(g.get('name'), g.get('frequency'), g.get('occurrences')) for g in fp][:5]}")

    # Same merchant, same amount, but irregular days: not a bill.
    base = date(2026, 1, 1)
    irregular = [{"occurred_on": (base + timedelta(days=d)).isoformat(), "type": "expense",
                  "amount": 250, "note": "Chai point"} for d in (0, 2, 3, 11, 12, 13, 40, 41)]
    check(not run_detect(irregular, 3), "same amount at irregular days is not a bill")
    # Robust to one late payment: monthly with one 5-day-late month still monthly.
    late = [{"occurred_on": d, "type": "expense", "amount": 999, "note": "Hotstar"}
            for d in ("2026-01-10", "2026-02-10", "2026-03-15", "2026-04-10", "2026-05-10")]
    g = run_detect(late, 3)
    check(len(g) == 1 and g[0].get("frequency") == "monthly", f"one late month is still monthly ({g})")
    # Empty input.
    check(run_detect([], 3) == [], "no transactions -> no suggestions")

finish()
