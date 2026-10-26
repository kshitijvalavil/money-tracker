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


def parse_sbi_alert(subject, sender, body, message_id=None):
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


# Registry of parsers. Statement-PDF and merchant-receipt parsers (Phase 2/3)
# will be added here behind the same interface.
PARSERS = [parse_sbi_alert]


def parse_email(subject, sender, body, message_id=None):
    """Try each parser; return the first transaction found, or None."""
    for parser in PARSERS:
        result = parser(subject, sender, body, message_id)
        if result:
            return result
    return None
