"""Recurring bills and subscriptions: date math and detection (pure functions).

This is the reference implementation from docs/sprint-2-contract.md. The SQL
in supabase/migrations/0008_suggest_recurring.sql must behave the same.
tests/suggest_recurring_test.sql checks that on the same fixture rows.

occurrence(start, freq, n)
    Occurrence n (0-based) of a schedule anchored to `start`, so dates never
    drift: weekly is start + 7n days. Monthly, quarterly and yearly add
    1/3/12 x n months and clamp the day to the month's length:
    31 Jan -> 28/29 Feb -> 31 Mar, not 28 Mar.
    This matches Postgres `start + make_interval(months => k)`.

detect(transactions, min_occurrences=3, existing_names=())
    Suggest recurring items from past transactions. Rules, applied in order:
      1. Only income/expense rows. Group by (normalised note, type). The
         normalised note is lowercased, every character that is not a letter
         or whitespace becomes a space, runs of whitespace collapse to one
         space and the ends are trimmed. Rows whose note normalises to '' are
         ignored. Groups whose key equals the normalised name of an existing
         recurring row (any type) are excluded.
      2. Groups with fewer than min_occurrences rows are dropped.
      3. Keep the rows whose amount is within +-10% of the group's median
         amount: |amount - median| <= 0.10 x median.
      4. occurrences = number of distinct dates among the kept rows. It must
         be >= min_occurrences.
      5. gaps = days between consecutive distinct dates. The frequency comes
         from the median gap, with inclusive bands: 6-8 weekly, 26-35 monthly,
         85-95 quarterly, 355-375 yearly. Any other median gap: not recurring.
      6. Output:
           name        the note of the latest kept row (trimmed, max 80 chars)
           amount      the median of the kept amounts, rounded half up to 2 dp
           account_id, category_id  taken from the latest kept row
           last_seen   the latest kept date
           next_guess  the first occurrence(first kept date, freq, k), k >= 1,
                       that falls after last_seen
           confidence  (gaps inside the band / all gaps)
                       x (kept rows / group rows), rounded half up to 2 dp
         The latest row is the one with the greatest (occurred_on, str(id)).
         Rows are sorted by confidence desc, occurrences desc, then name key
         and type ascending (code-point order).
"""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly")
_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
# Inclusive bands for the median gap in days.
BANDS = (("weekly", 6, 8), ("monthly", 26, 35), ("quarterly", 85, 95), ("yearly", 355, 375))
AMOUNT_TOLERANCE = Decimal("0.10")
_CENT = Decimal("0.01")


def _as_date(d):
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def occurrence(start, freq, n):
    """Date of occurrence n (n >= 0) of a schedule anchored to start."""
    start = _as_date(start)
    if n < 0:
        raise ValueError("n must be >= 0")
    if freq == "weekly":
        return start + timedelta(days=7 * n)
    if freq not in _MONTHS:
        raise ValueError(f"bad frequency {freq!r}")
    months = start.month - 1 + _MONTHS[freq] * n
    year, month = start.year + months // 12, months % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def normalise(note):
    """Lowercase, non-letters to spaces, collapse whitespace, trim."""
    s = "".join(c if c.isalpha() or c.isspace() else " " for c in (note or "").lower())
    return " ".join(s.split())


def _median(values):
    v = sorted(values)
    return (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2


def _frequency_for(gap):
    for freq, lo, hi in BANDS:
        if lo <= gap <= hi:
            return freq, lo, hi
    return None, None, None


def _round2(x):
    return Decimal(x).quantize(_CENT, rounding=ROUND_HALF_UP)


def detect(transactions, min_occurrences=3, existing_names=()):
    """Suggest recurring items. transactions: dicts with occurred_on, amount,
    type, note, account_id, category_id and optionally id."""
    if min_occurrences is None or min_occurrences < 2:
        raise ValueError("min_occurrences must be >= 2")
    excluded = {normalise(n) for n in existing_names}
    groups = {}
    for t in transactions:
        if t.get("type") not in ("income", "expense"):
            continue
        key = normalise(t.get("note"))
        if not key or key in excluded:
            continue
        groups.setdefault((key, t["type"]), []).append(t)

    out = []
    for (key, typ), rows in groups.items():
        if len(rows) < min_occurrences:
            continue
        med = _median([Decimal(str(r["amount"])) for r in rows])
        kept = [r for r in rows
                if abs(Decimal(str(r["amount"])) - med) <= med * AMOUNT_TOLERANCE]
        dates = sorted({_as_date(r["occurred_on"]) for r in kept})
        if len(dates) < min_occurrences:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        freq, lo, hi = _frequency_for(_median([Decimal(g) for g in gaps]))
        if not freq:
            continue
        in_band = sum(1 for g in gaps if lo <= g <= hi)
        latest = max(kept, key=lambda r: (_as_date(r["occurred_on"]), str(r.get("id") or "")))
        last_seen = dates[-1]
        k = 1
        while occurrence(dates[0], freq, k) <= last_seen:
            k += 1
        out.append({
            "name": (latest.get("note") or "").strip(" ")[:80],
            "type": typ,
            "amount": _round2(_median([Decimal(str(r["amount"])) for r in kept])),
            "frequency": freq,
            "account_id": latest.get("account_id"),
            "category_id": latest.get("category_id"),
            "last_seen": last_seen,
            "next_guess": occurrence(dates[0], freq, k),
            "occurrences": len(dates),
            "confidence": _round2(Decimal(in_band * len(kept)) / Decimal(len(gaps) * len(rows))),
            "_key": key,
        })
    out.sort(key=lambda s: (-s["confidence"], -s["occurrences"], s["_key"], s["type"]))
    for s in out:
        del s["_key"]
    return out
