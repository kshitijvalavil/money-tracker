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
        "channel":           "Google Pay" | "SBI" | None,  # how the money moved
    }
or None if the email isn't a transaction it understands.

De-duplication: emails carry no transaction reference, so we key on the Gmail
message id. The importer stores it on each transaction and skips ids seen before.
"""
import re
from datetime import datetime

_AMOUNT = r"(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d{1,2})?)"

# Channel rule (Sprint 6): any UPI payment is labelled "Google Pay"; any other
# bank transaction is labelled with the bank. UPI shows up as the word UPI, a
# VPA, or a UPI handle like name@okaxis / name@oksbi (Google Pay's handles).
_UPI = re.compile(r"\bUPI\b|\bVPA\b|@ok(?:axis|sbi|hdfcbank|icici)\b|\bGPay\b|Google\s*Pay", re.I)


def channel_for(text, bank):
    """'Google Pay' if the text describes a UPI payment, otherwise the bank."""
    return "Google Pay" if _UPI.search(text or "") else bank


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
        "channel": channel_for(body, "SBI"),
    }


# Other banks' transaction alerts: sender domain -> bank name. Only the
# banks' own alert domains; lookalike marketing senders (e.g. "credit card
# options" mailers) never match, and the body must read like a transaction.
BANKS = {
    "hdfcbank.net": "HDFC Bank", "hdfcbank.com": "HDFC Bank",
    "kotak.com": "Kotak", "kotak.bank.in": "Kotak",
    "icicibank.com": "ICICI Bank",
    "axisbank.com": "Axis Bank",
    "yesbank.in": "Yes Bank",
    "idfcfirstbank.com": "IDFC First",
    "sliceit.com": "Slice", "slicebank.com": "Slice",
}

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DEBIT = r"debited|spent|sent|paid|withdrawn|purchase|used for|for using"
_CREDIT = r"credited|received|deposited|refunded"
# Words that only appear in offers, never in a transaction alert.
_PROMO = re.compile(r"pre-?approved|offer|upgrade|apply now|eligible|limit enhancement", re.I)


def _any_date(text, email_date):
    m = re.search(r"\b(\d{2}[/-]\d{2}[/-]\d{2,4})\b", text)
    if m and _to_iso(m.group(1)):
        return _to_iso(m.group(1))
    m = re.search(r"\b(\d{1,2})[-\s](" + _MONTHS + r")[a-z]*[-,\s]+(\d{2,4})\b", text, re.I)
    if m:
        day, mon, year = m.groups()
    else:   # "Oct 26, 2026"
        m = re.search(r"\b(" + _MONTHS + r")[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b", text, re.I)
        if m:
            mon, day, year = m.groups()
    if m:
        year = year if len(year) == 4 else "20" + year
        try:
            return datetime.strptime(f"{day} {mon[:3].title()} {year}", "%d %b %Y").date().isoformat()
        except ValueError:
            pass
    return email_date


def parse_bank_alert(subject, sender, body, message_id=None, email_date=None):
    """Transaction alerts from HDFC, Kotak, ICICI, Axis, Yes, IDFC First and
    Slice. These differ in wording, so this reads the common parts: the first
    debit/credit verb, the first amount, the date, the masked account or card,
    and who the money went to (a VPA or a merchant)."""
    domain = (sender or "").split("@")[-1].lower().strip("> ")
    bank = next((name for known, name in BANKS.items()
                 if domain == known or domain.endswith("." + known)), None)
    if not bank:
        return None
    text = re.sub(r"\s+", " ", f"{subject or ''} . {body or ''}")

    verb = re.search(r"\b(" + _DEBIT + "|" + _CREDIT + r")\b", text, re.I)
    amt = re.search(_AMOUNT, text)
    if not verb or not amt:
        return None
    if _PROMO.search(text) and not re.search(r"\b(debited|credited)\b", text, re.I):
        return None
    direction = "income" if re.fullmatch(_CREDIT, verb.group(1), re.I) else "expense"
    amount = _to_float(amt.group(1))
    if amount <= 0:
        return None

    acct = re.search(r"(?:A/?c(?:count)?|Acct|card)\b[^0-9]{0,25}?[Xx*]+\s*(\d{3,})", text, re.I) \
        or re.search(r"ending(?:\s+(?:in|with))?\s+(\d{3,})", text, re.I)
    vpa = re.search(r"\b(?:to|from|by)\s+(?:VPA\s+)?([\w.\-]+@[a-z]+)", text, re.I)
    who = re.search(r"\b(?:at|towards|to)\s+([A-Z][\w&'\- ]{1,40}?)(?:\s+(?:on|via|using|ref)\b|\.(?:\s|$))", text)
    if vpa:
        note = ("UPI to " if direction == "expense" else "UPI from ") + vpa.group(1)
    elif who:
        note = who.group(1).strip()
    else:
        note = f"{bank} {'debit' if direction == 'expense' else 'credit'}"

    return {
        "source": re.sub(r"[^a-z]+", "", bank.lower().replace("bank", "")) + "_alert",
        "direction": direction,
        "amount": amount,
        "occurred_on": _any_date(text, email_date),
        "account_hint": acct.group(1) if acct else None,
        "note": note[:120],
        "balance_after": None,
        "source_message_id": message_id,
        "confidence": "high",
        "channel": channel_for(text, bank),
    }


def parse_google_pay(subject, sender, body, message_id=None, email_date=None):
    """Google Pay's own payment emails ("You paid ₹250 to Ravi")."""
    domain = (sender or "").split("@")[-1].lower().strip("> ")
    if domain != "google.com" and not domain.endswith(".google.com"):
        return None
    # Body first: subjects are often shortened ("to Asha" vs "to Asha Nair").
    text = re.sub(r"\s+", " ", f"{body or ''} . {subject or ''}")
    out = re.search(r"You (?:paid|sent) " + _AMOUNT + r" to (.+?)(?:\s+(?:on|using|via)\b|[.,]|$)", text, re.I)
    inc = re.search(r"(.+?) (?:paid|sent) you " + _AMOUNT, text, re.I)
    if not out and not inc:
        return None
    if out:
        direction, amount, who = "expense", _to_float(out.group(1)), out.group(2)
    else:
        direction, amount, who = "income", _to_float(inc.group(2)), inc.group(1).split(".")[-1]
    if amount <= 0:
        return None
    return {
        "source": "google_pay",
        "direction": direction,
        "amount": amount,
        "occurred_on": _any_date(text, email_date),
        "account_hint": None,
        "note": ("Paid " if direction == "expense" else "From ") + who.strip()[:100],
        "balance_after": None,
        "source_message_id": message_id,
        "confidence": "high",
        "channel": "Google Pay",
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
        "channel": None,       # paid by card or UPI; the bank alert says which
    }


# Registry of parsers. The statement-PDF parser (Phase 2b) plugs in here too.
PARSERS = [parse_sbi_alert, parse_bank_alert, parse_google_pay, parse_receipt]


def parse_email(subject, sender, body, message_id=None, email_date=None):
    """Try each parser; return the first transaction found, or None."""
    for parser in PARSERS:
        result = parser(subject, sender, body, message_id, email_date)
        if result:
            return result
    return None
