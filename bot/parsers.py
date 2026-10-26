"""Parse bank/transaction emails into normalized transaction dicts.

Each parser takes the raw email fields and returns a dict shaped like:
    {
        "source":            "sbi_alert",          # which parser produced it
        "direction":         "income" | "expense",
        "amount":            5000.0,                 # positive float
        "occurred_on":       "2026-01-15",           # ISO date
        "account_hint":      "123456",               # masked-account tail, or None
        "note":              "UPI transfer",
        "balance_after":     1234.56,                # or None
        "source_message_id": "<gmail id>",           # for de-duplication
        "confidence":        "high" | "low",
    }
or None if the email isn't a transaction it understands.

De-duplication: emails carry no transaction reference, so we key on the Gmail
message id. The importer stores it on each transaction and skips ids seen before.
"""
import re
from datetime import datetime

_AMOUNT = r"(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d{1,2})?)"


def _to_float(s):
    return float(s.replace(",", ""))


def _to_iso(datestr):
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(datestr, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_sbi_alert(subject, sender, body, message_id=None, email_date=None):
    """SBI 'CBSSBI ALERT' emails from alerts.sbi.bank.in.

    Example body (synthetic):
      "... Your A/C XXXXX123456 Credited INR 5,000.00 on 15/01/26
       -UPI transfer. Avl Bal INR 1,234.56-SBI ..."
    """
    if "sbi" not in (sender or "").lower():
        return None

    m = re.search(r"\b(Credited|Debited)\b\s+" + _AMOUNT, body, re.I)
    if not m:
        return None
    direction = "income" if m.group(1).lower() == "credited" else "expense"
    amount = _to_float(m.group(2))

    date_m = re.search(r"\bon\s+(\d{2}[/-]\d{2}[/-]\d{2,4})", body)
    occurred_on = _to_iso(date_m.group(1)) if date_m else None

    acct_m = re.search(r"A/[Cc]\s*[Xx*]*(\d{3,})", body)
    account_hint = acct_m.group(1) if acct_m else None

    bal_m = re.search(r"Avl\s*Bal\s+" + _AMOUNT, body, re.I)
    balance_after = _to_float(bal_m.group(1)) if bal_m else None

    # Note: the text between the date and "Avl Bal".
    note = ""
    note_m = re.search(r"\d{2}[/-]\d{2}[/-]\d{2,4}\s*-?(.*?)(?:Avl\s*Bal|$)", body, re.S)
    if note_m:
        note = re.sub(r"\s+", " ", note_m.group(1)).strip(" .-")

    return {
        "source": "sbi_alert",
        "direction": direction,
        "amount": amount,
        "occurred_on": occurred_on,
        "account_hint": account_hint,
        "note": note,
        "balance_after": balance_after,
        "source_message_id": message_id,
        "confidence": "high",
    }


# Merchant receipts: sender domain -> (merchant name, suggested category).
MERCHANTS = {
    "dominos.co.in": ("Domino's", "Food"),
    "zomato.com": ("Zomato", "Food"),
    "swiggy.in": ("Swiggy", "Food"),
    "swiggy.com": ("Swiggy", "Food"),
    "district.in": ("District", "Entertainment"),
    "bookmyshow.com": ("BookMyShow", "Entertainment"),
    "amazon.in": ("Amazon", "Shopping"),
    "amazon.com": ("Amazon", "Shopping"),
    "uber.com": ("Uber", "Transport"),
    "olacabs.com": ("Ola", "Transport"),
}

# Total patterns, most specific first (so "Grand Total" wins over a bare "Total").
_TOTAL_PATTERNS = [
    r"Grand\s*Total", r"Amount\s*Paid", r"Total\s*Paid",
    r"Order\s*Total", r"Bill\s*Total", r"Total\s*Amount", r"Total",
]


def parse_receipt(subject, sender, body, message_id=None, email_date=None):
    """Merchant order/receipt emails (Domino's, Zomato, …) with the total in
    the body. Returns an expense. Attachment-only receipts (e.g. a PDF bill)
    have no total in the body and return None — handled by the statement/PDF
    path instead."""
    domain = (sender or "").split("@")[-1].lower().strip(">")
    merchant = category = None
    for known, (name, cat) in MERCHANTS.items():
        if known in domain:
            merchant, category = name, cat
            break
    if not merchant:
        return None

    amount = None
    for label in _TOTAL_PATTERNS:
        m = re.search(label + r"[\s:\-|]*" + _AMOUNT, body, re.I)
        if m:
            amount = _to_float(m.group(1))
            break
    if amount is None or amount <= 0:
        return None  # e.g. the amount is in a PDF attachment, not the body

    date_m = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4})", body)
    occurred_on = _to_iso(date_m.group(1)) if date_m else email_date

    return {
        "source": "receipt",
        "direction": "expense",
        "amount": amount,
        "occurred_on": occurred_on,
        "account_hint": None,
        "note": merchant + " order",
        "category_hint": category,
        "balance_after": None,
        "source_message_id": message_id,
        "confidence": "low",   # merchant receipts are noisier than bank emails
    }


# Registry of parsers. The statement-PDF parser (Phase 2b) plugs in here too.
PARSERS = [parse_sbi_alert, parse_receipt]


def parse_email(subject, sender, body, message_id=None, email_date=None):
    """Try each parser; return the first transaction found, or None."""
    for parser in PARSERS:
        result = parser(subject, sender, body, message_id, email_date)
        if result:
            return result
    return None
