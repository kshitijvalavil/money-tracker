"""Read a bill photo into the few fields the Add-a-bill form needs.

The browser sends a downscaled photo; we ask Claude's vision model for the
merchant, amount, date and a couple of guesses, and hand back plain JSON. The
image is used only for this one call and is never stored or logged.
"""
import json
import os
import re
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
FREQS = {"weekly", "monthly", "quarterly", "yearly"}
MAX_AMOUNT = 999999999999.99  # numeric(14,2), matches the bills table

PROMPT = (
    "You are reading a photo or scan of a bill, invoice or receipt for a personal "
    "finance app. Reply with ONLY a JSON object, no prose, with these fields:\n"
    '  "name": the merchant or biller name, short plain text;\n'
    '  "amount": the total amount due as a number (digits only, no currency symbol);\n'
    '  "type": "expense", or "income" only for a payslip or a payment received;\n'
    '  "date": the due date or bill date as "YYYY-MM-DD", or null if none is shown;\n'
    '  "category_guess": a short spending category like "Utilities", "Internet" or "Rent", or null;\n'
    '  "frequency_guess": one of "weekly", "monthly", "quarterly", "yearly", or null if it does not look recurring;\n'
    '  "confidence": a number from 0 to 1 for how sure you are overall.\n'
    "Leave a field null rather than guessing wildly, and never invent an amount."
)


class BillReadError(Exception):
    """A failure with an HTTP status and a message safe to show the user."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _demo_sample():
    """A fixed, realistic result so the feature and its tests work with no API key."""
    return {
        "name": "Acme Power & Light",
        "amount": 84.50,
        "type": "expense",
        "date": None,
        "category_guess": "Utilities",
        "frequency_guess": "monthly",
        "confidence": 0.92,
        "demo": True,
    }


def _clean(obj):
    """Coerce the model's JSON into safe, form-ready values."""
    if not isinstance(obj, dict):
        obj = {}
    name = str(obj.get("name") or "").strip()[:80]

    amount = obj.get("amount")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        amount = None
    if amount is not None and not (0 < amount <= MAX_AMOUNT):
        amount = None

    typ = obj.get("type")
    if typ not in ("expense", "income"):
        typ = "expense"

    date = obj.get("date")
    if not (isinstance(date, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", date)):
        date = None

    cat = obj.get("category_guess")
    cat = cat.strip()[:60] if isinstance(cat, str) and cat.strip() else None

    freq = obj.get("frequency_guess")
    if freq not in FREQS:
        freq = None

    conf = obj.get("confidence")
    try:
        conf = min(max(float(conf), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = None

    return {"name": name, "amount": amount, "type": typ, "date": date,
            "category_guess": cat, "frequency_guess": freq, "confidence": conf}


def _call_model(body, api_key, timeout):
    """POST to the Messages API and return the parsed response, or raise BillReadError."""
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status = 429 if e.code == 429 else 502
        msg = ("The bill reader is busy right now. Wait a moment and try again."
               if e.code == 429 else
               "Couldn't read the bill just now. Please try again.")
        raise BillReadError(status, msg)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BillReadError(504, "Reading the bill timed out. Check your connection and try again.")


# ---------------------------------------------------------------------------
# Free, on-device path: the browser reads the photo's text with open-source OCR
# (Tesseract.js) and sends us the plain text. We extract the fields here with
# deterministic rules — no API, no key, no per-use cost. Lower accuracy than the
# vision model, so the form is only pre-filled and the person confirms it.
# ---------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# Total-style labels, most specific first so "grand total" beats a bare "total".
_TOTAL_LABELS = (
    r"(?:grand\s*total|amount\s*due|total\s*due|amount\s*payable|balance\s*due|"
    r"total\s*amount|amount\s*paid|total\s*paid|net\s*payable|total)")
_CURRENCY = r"(?:₹|rs\.?|inr|\$)"

_CATEGORY_HINTS = [
    (r"electric|power|energy|utilit", "Utilities"),
    (r"water|sewage", "Utilities"),
    (r"\bgas\b|lpg|cylinder", "Utilities"),
    (r"internet|broadband|wi-?fi|fibre|fiber", "Internet"),
    (r"mobile|postpaid|prepaid|recharge|airtime|telecom", "Phone"),
    (r"\brent\b|lease", "Rent"),
    (r"insurance|premium|policy", "Insurance"),
    (r"netflix|prime|spotify|hotstar|youtube|subscription", "Subscriptions"),
    (r"gym|fitness|clinic|hospital|pharmac", "Health"),
    (r"loan|\bemi\b|credit\s*card", "Loan"),
]
_FREQ_HINTS = [
    (r"per\s*week|weekly", "weekly"),
    (r"per\s*month|monthly|/mo\b", "monthly"),
    (r"quarter", "quarterly"),
    (r"per\s*year|yearly|annual", "yearly"),
]


def _iso(y, mo, d):
    from datetime import date
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _bill_amount(text):
    # Prefer a number sitting next to a total-style label.
    near = re.finditer(_TOTAL_LABELS + r"[^\d₹$]{0,15}" + _CURRENCY + r"?\s*([\d,]+(?:\.\d{1,2})?)",
                       text, re.I)
    for m in near:
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0 < v <= MAX_AMOUNT:
            return v
    # Else the largest currency-tagged amount (a bill's total is usually biggest).
    tagged = []
    for m in re.finditer(_CURRENCY + r"\s*([\d,]+(?:\.\d{1,2})?)", text, re.I):
        try:
            tagged.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    tagged = [v for v in tagged if 0 < v <= MAX_AMOUNT]
    if tagged:
        return max(tagged)
    # Else the largest bare 2-decimal number.
    bare = []
    for m in re.finditer(r"\b(\d[\d,]*\.\d{2})\b", text):
        try:
            bare.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    bare = [v for v in bare if 0 < v <= MAX_AMOUNT]
    return max(bare) if bare else None


def _bill_name(lines):
    skip = re.compile(
        r"(invoice|receipt|^bill\b|tax|gst|total|amount|date|due|order\s*no|"
        r"customer|qty|description|subtotal|www\.|@|http)", re.I)
    for ln in lines:
        s = ln.strip(" .:-|")
        if len(s) < 3 or s[0].isdigit():
            continue
        if len(re.sub(r"[^A-Za-z]", "", s)) < 3:
            continue
        if skip.search(s):
            continue
        return s[:80]
    return (lines[0].strip()[:80] if lines else "")


def _bill_date(text):
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if mo > 12 and d <= 12:  # clearly month-first; swap to day-first
            d, mo = mo, d
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b", text)
    if m:
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            return _iso(int(m.group(3)), mo, int(m.group(1)))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            return _iso(int(m.group(3)), mo, int(m.group(2)))
    return None


def _first_hint(text, hints):
    for pat, value in hints:
        if re.search(pat, text, re.I):
            return value
    return None


def parse_bill_text(text):
    """Extract form-ready bill fields from OCR text. Free and deterministic;
    never raises — unreadable text just yields mostly-empty fields the person
    can fill in. Returns the same shape as read_bill, with "ocr": True."""
    text = (text or "").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]

    low = text.lower()
    typ = "income" if re.search(r"payslip|salary|credited|payment received", low) else "expense"

    result = _clean({
        "name": _bill_name(lines),
        "amount": _bill_amount(text),
        "type": typ,
        "date": _bill_date(text),
        "category_guess": _first_hint(low, _CATEGORY_HINTS),
        "frequency_guess": _first_hint(low, _FREQ_HINTS),
    })
    conf = 0.25 + (0.35 if result["amount"] is not None else 0) + \
        (0.2 if result["name"] else 0) + (0.1 if result["date"] else 0)
    result["confidence"] = round(min(conf, 0.95), 2)
    result["ocr"] = True
    return result


def read_bill(image_b64, mime, *, demo=False, api_key=None, model=None, timeout=30):
    """Return form-ready fields read from a base64 image, or raise BillReadError."""
    if demo:
        return _demo_sample()
    if not image_b64:
        raise BillReadError(400, "No image was received. Try again.")
    if mime not in ALLOWED_MIME:
        raise BillReadError(400, "That file type isn't supported. Use a JPEG or PNG photo.")
    if not api_key:
        raise BillReadError(
            503, "Bill reading isn't switched on yet. The ANTHROPIC_API_KEY still needs "
                 "to be added on the server.")

    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime, "data": image_b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }
    payload = _call_model(body, api_key, timeout)

    text = "".join(part.get("text", "") for part in payload.get("content", [])
                   if isinstance(part, dict) and part.get("type") == "text")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise BillReadError(422, "I couldn't read the details off that image. Try a clearer, straight-on photo.")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise BillReadError(422, "I couldn't read the details off that image. Try a clearer, straight-on photo.")

    result = _clean(parsed)
    if not result["name"] and result["amount"] is None:
        raise BillReadError(422, "That doesn't look like a bill I can read. Try a clearer photo.")
    return result
