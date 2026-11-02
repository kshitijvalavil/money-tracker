"""Review inbox helpers (Sprint 9).

Pure functions, no network:

* ``merge_duplicates(rows)`` — one Swiggy order often arrives as three emails
  (Swiggy's receipt, a Google Pay "you paid" email, the bank's debit alert).
  Rows with the same amount, direction and day from *different* kinds of
  sender are merged into one, keeping the receipt as the main row (it names
  the shop) and recording the others in ``merged_message_ids`` /
  ``merged_sources``. When both rows carry an email timestamp (``ts``, epoch
  seconds) they must also be within 30 minutes of each other.
* ``merchant_of(row)`` — a short shop name for the review list.
* ``apply_rules(rows, rules, categories)`` — fills ``suggested_category_id``
  from the user's rules first, then from the parser's category hint.

The rows are pending_imports dicts as built by ``bot.scanner.to_row`` plus the
optional ``ts`` and ``category_hint`` keys, which are dropped before saving.
"""

WINDOW_SECONDS = 30 * 60

# Which kind of email a row came from; one row of each kind per payment.
def _kind(row):
    src = (row.get("source") or "").lower()
    if src == "receipt":
        return "receipt"
    if (row.get("channel") or "") == "Google Pay" and src in ("google_pay", "gpay", "upi_receipt"):
        return "gpay"
    return "bank"


def _bank_name(row):
    """'hdfc_alert' -> 'HDFC'; a non-UPI bank row's channel already names the bank."""
    ch = row.get("channel")
    if ch and ch != "Google Pay":
        return str(ch)[:40]
    src = (row.get("source") or "").lower()
    if src.endswith("_alert"):
        name = src[: -len("_alert")].upper()
        if name:
            return name
    return "Bank"


# Lower number = better main row (the receipt knows the shop name).
_RANK = {"receipt": 0, "gpay": 1, "bank": 2}


def merchant_of(row):
    """Best short shop name: the receipt's merchant, else the note's first part."""
    if row.get("merchant"):
        return str(row["merchant"]).strip()[:80] or None
    note = (row.get("note") or "").strip()
    if not note:
        return None
    for suffix in (" order", " receipt"):
        if note.lower().endswith(suffix):
            note = note[: -len(suffix)]
            break
    return note[:80] or None


def _close(a, b):
    if a.get("ts") is not None and b.get("ts") is not None:
        try:
            return abs(float(a["ts"]) - float(b["ts"])) <= WINDOW_SECONDS
        except (TypeError, ValueError):
            return False
    return True  # no timestamps: same day is the best we can do


def _matches(a, b):
    return (a["direction"] == b["direction"]
            and round(float(a["amount"]), 2) == round(float(b["amount"]), 2)
            and a["occurred_on"] == b["occurred_on"]
            and _close(a, b))


def merge_duplicates(rows):
    """Merge rows that are the same payment seen in several emails.

    A group never holds two rows of the same kind, so two genuine Rs 450
    Swiggy orders on one day stay separate. Input order is kept for the
    surviving rows.
    """
    groups = []  # list of lists of rows
    for row in rows:
        kind = _kind(row)
        home = None
        for g in groups:
            if any(_kind(r) == kind for r in g):
                continue
            if all(_matches(row, r) for r in g):
                home = g
                break
        if home is None:
            groups.append([row])
        else:
            home.append(row)

    out = []
    for g in groups:
        g_sorted = sorted(g, key=lambda r: _RANK[_kind(r)])
        main = dict(g_sorted[0])
        others = g_sorted[1:]
        main["merchant"] = merchant_of(main)
        main["merged_message_ids"] = [r["source_message_id"] for r in others]
        sources = []
        for r in others:
            label = "Google Pay" if _kind(r) == "gpay" else _bank_name(r)
            if label not in sources:
                sources.append(label)
        main["merged_sources"] = sources[:5]
        if not main.get("channel"):
            # A receipt doesn't say how it was paid; the other emails do.
            if "Google Pay" in sources:
                main["channel"] = "Google Pay"
            elif sources:
                main["channel"] = sources[0]
        out.append(main)
    return out


def _haystack(row):
    parts = [row.get("merchant"), row.get("note"), row.get("account_hint")]
    return " ".join(p for p in parts if p).lower()


def apply_rules(rows, rules, categories):
    """Fill suggested_category_id on each row.

    rules: [{match_text, category_id}] (the user's own, any order).
    categories: [{id, name, kind}] (the user's own).
    A rule wins over the parser's hint; longer match texts win over shorter
    ones, so "swiggy instamart" beats "swiggy". A suggestion must fit the
    row's direction (an expense category for an expense).
    """
    kind_of = {c["id"]: c["kind"] for c in categories}
    by_name = {(c["name"].lower(), c["kind"]): c["id"] for c in categories}
    ordered = sorted(rules or (), key=lambda r: -len((r.get("match_text") or "").strip()))
    out = []
    for row in rows:
        row = dict(row)
        hay = _haystack(row)
        pick = None
        for rule in ordered:
            text = (rule.get("match_text") or "").strip().lower()
            if len(text) >= 2 and text in hay and kind_of.get(rule.get("category_id")) == row["direction"]:
                pick = rule["category_id"]
                break
        if pick is None and row.get("category_hint"):
            pick = by_name.get((str(row["category_hint"]).lower(), row["direction"]))
        row["suggested_category_id"] = pick
        out.append(row)
    return out


SAVE_KEYS = ("source", "direction", "amount", "occurred_on", "account_hint", "note",
             "source_message_id", "channel", "merchant", "suggested_category_id",
             "merged_message_ids", "merged_sources")


def to_save(row):
    """Only the columns pending_imports accepts (drops ts, category_hint, …)."""
    return {k: row.get(k) for k in SAVE_KEYS if k in row}
