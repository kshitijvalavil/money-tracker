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
