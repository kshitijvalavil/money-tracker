"""Voice note parser (Sprint 16) — Python twin of static/js/voiceparse.js.

Both parsers are tested against tests/fixtures/voice_phrases.json so they cannot
drift. This one backs POST /api/parse-voice (used when the browser wants a
second opinion on a transcript); the browser parses on-device with the JS twin.
Nothing is saved here; the UI shows a confirm card first.
"""
import re

MAX_AMOUNT = 999999999999.99

WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
            "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
            "hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000}
HINDI_NUM = {"ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "cheh": 6,
             "saat": 7, "aath": 8, "nau": 9, "das": 10, "bees": 20, "pachaas": 50, "pachas": 50, "sau": 100,
             "hazaar": 1000, "hazar": 1000, "lakh": 100000}

INCOME = re.compile(r"\b(received|got|credited|credit|refund(?:ed)?|salary|income|earned|deposited|cashback|reimburs\w*)\b", re.I)
EXPENSE = re.compile(r"\b(paid|spent|bought|swiped|gave|debited|debit|purchased|billed|sent)\b", re.I)

CHANNELS = [
    (re.compile(r"\b(g[\s-]?pay|google\s*pay)\b", re.I), "Google Pay"),
    (re.compile(r"\bphone\s*pe\b", re.I), "PhonePe"), (re.compile(r"\bpaytm\b", re.I), "Paytm"),
    (re.compile(r"\bbhim\b", re.I), "BHIM"), (re.compile(r"\bupi\b", re.I), "UPI"),
    (re.compile(r"\bnet[\s-]?banking\b", re.I), "Net banking"),
    (re.compile(r"\b(imps|neft|rtgs)\b", re.I), "Bank transfer"),
    (re.compile(r"\b(credit|debit)?\s*card\b|\bswiped?\b", re.I), "Card"),
    (re.compile(r"\b(emi)\b", re.I), "EMI"), (re.compile(r"\bcash\b", re.I), "Cash"),
]
ACCOUNTS = [re.compile(r"\b(hdfc|icici|sbi|kotak|axis|yes\s*bank|idfc|amex|rbl|hsbc|citi|pnb|bob|canara)\b", re.I)]

CATS = [
    (re.compile(r"\b(chai|tea|coffee|lunch|dinner|breakfast|food|eat|snack|grocer|vegetable|veggies|restaurant|cafe|swiggy|zomato|dmart|biscuit|milk|soft drinks|beverage)\b", re.I), "Food"),
    (re.compile(r"\b(auto|cab|uber|ola|rickshaw|metro|bus|train|flights?|petrol|diesel|fuel|parking|toll|travel|taxi)\b", re.I), "Transport"),
    (re.compile(r"\b(medicine|pharmac|doctor|hospital|clinic|apollo|health|gym|fitness)\b", re.I), "Health"),
    (re.compile(r"\brent\b", re.I), "Rent"),
    (re.compile(r"\b(bill|electricity|water|gas|phone|mobile|internet|broadband|wifi|recharge|subscription|netflix|prime|spotify|hotstar|dth)\b", re.I), "Bills"),
    (re.compile(r"\b(clothes|apparel|shirt|shoes|amazon|flipkart|myntra|shopping|laundry|book)\b", re.I), "Shopping"),
    (re.compile(r"\b(movie|cinema|fun|party|game|concert|entertain)\b", re.I), "Entertainment"),
    (re.compile(r"\b(salary|payroll|wages?)\b", re.I), "Salary"),
    (re.compile(r"\b(freelance|invoice|client)\b", re.I), "Freelance"),
    (re.compile(r"\b(gift|present)\b", re.I), "Gift"),
    (re.compile(r"\b(invest|mutual fund|sip|stocks?|ipo|shares?)\b", re.I), "Investment"),
]
FREQ = [(re.compile(r"\b(daily|every day)\b", re.I), "weekly"), (re.compile(r"\b(weekly|every week|per week)\b", re.I), "weekly"),
        (re.compile(r"\b(monthly|every month|per month|a month)\b", re.I), "monthly"),
        (re.compile(r"\b(quarter(ly)?)\b", re.I), "quarterly"),
        (re.compile(r"\b(yearly|annual(ly)?|every year|per year)\b", re.I), "yearly")]

_clean = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()


def _digits_amount(text):
    text = re.sub(r"(\d[\d,]*)\s*(?:rupees?|rs\.?|₹)\s*(\d{1,2})\s*paise?",
                  lambda m: f"{m.group(1).replace(',', '')}.{m.group(2).zfill(2)}", text, flags=re.I)
    best = None
    for m in re.finditer(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k|lakhs?|lacs?|crores?|cr)?", text, re.I):
        digits = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        if re.fullmatch(r"\d{10}", digits) and not unit:
            continue
        v = float(digits)
        if unit == "k":
            v *= 1000
        elif unit.startswith("lakh") or unit.startswith("lac"):
            v *= 100000
        elif unit.startswith("cr"):
            v *= 10000000
        at = m.start(1)
        if v > 0 and (best is None or at < best["at"]):
            best = {"value": v, "at": at}
    return best


def _words_amount(text):
    tokens = [t for t in re.split(r"[\s-]+", re.sub(r"[^a-z\s-]", " ", text.lower())) if t]
    total = cur = 0
    used = []
    any_ = False
    prev_unit = 0
    start = -1
    for i, tok in enumerate(tokens):
        n = WORD_NUM.get(tok, HINDI_NUM.get(tok))
        if n is None:
            prev_unit = 0
            continue
        if start < 0:
            start = i
        any_ = True
        used.append(i)
        if n >= 100:
            cur = (cur or 1) * n
            if n >= 1000:
                total += cur
                cur = 0
        elif 1 <= prev_unit <= 9 and n >= 20 and n % 10 == 0 and cur == prev_unit:
            cur = prev_unit * 100 + n
        else:
            cur += n
        prev_unit = n if n < 10 else 0
    v = total + cur
    contiguous = used and used[-1] - used[0] <= len(used) + 1
    return {"value": v} if (any_ and v > 0 and contiguous) else None


def _merchant(text):
    at = re.search(r"\bat\s+(.+)$", text, re.I)
    if at:
        cand = at.group(1)
    else:
        m = re.search(r"\b(?:to|from|for)\s+(.+)$", text, re.I)
        cand = re.sub(r"\s+\b(?:for|to)\b\s+.*$", "", m.group(1), flags=re.I) if m else text
    cand = re.sub(r"\b(paid|spent|bought|swiped|gave|received|got|credited|sent|purchased)\b", " ", cand, flags=re.I)
    cand = re.sub(r"(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d{1,2})?\s*(?:k|lakhs?|lacs?|crores?|cr|rupees?|rupee|paise)?", " ", cand, flags=re.I)
    for rx, _ in CHANNELS:
        cand = rx.sub(" ", cand)
    for rx in ACCOUNTS:
        cand = rx.sub(" ", cand)
    cand = re.sub(r"\b(card|on|via|using|my|the bill|bill|due|monthly|weekly|yearly|quarterly|daily|today|yesterday|net\s*banking|subscription|ride|trip|order|payment|txn|transaction|recharge)\b", " ", cand, flags=re.I)
    cand = re.sub(r"\b(?:on\s+)?the\s+\d{1,2}(?:st|nd|rd|th)?\b", " ", cand, flags=re.I)
    cand = re.sub(r"\band\b.*$", " ", cand, flags=re.I)
    cand = re.sub(r"[^\w\s'&.-]", " ", cand, flags=re.UNICODE)
    cand = " ".join(w for w in cand.split() if not (w.lower() in WORD_NUM or w.lower() in HINDI_NUM) or len(w) > 6)
    cand = re.sub(r"^(for|to|at|from|of)\s+", "", _clean(cand), flags=re.I)
    cand = re.sub(r"\s+(for|to|at)$", "", cand, flags=re.I)
    return cand[:80] if cand else None


def _parse_item(text):
    text = _clean(text)
    if not text:
        return {"error": "empty"}
    amt = _digits_amount(text)
    if amt:
        amount = amt["value"]
    else:
        w = _words_amount(text)
        amount = w["value"] if w else None
    if amount is None:
        return {"error": "no_amount"}
    if amount > MAX_AMOUNT:
        return {"error": "too_big"}
    amount = round(amount, 2)
    if amount <= 0:
        return {"error": "no_amount"}

    low = text.lower()
    if INCOME.search(low) and not EXPENSE.search(low):
        direction = "income"
    elif EXPENSE.search(low):
        direction = "expense"
    elif INCOME.search(low):
        direction = "income"
    else:
        direction = "expense"

    channel = next((name for rx, name in CHANNELS if rx.search(text)), None)
    account_hint = None
    for rx in ACCOUNTS:
        m = rx.search(text)
        if m:
            account_hint = re.sub(r"\s+", " ", m.group(1)).upper()
            break
    category_guess = next((name for rx, name in CATS if rx.search(low)), None)
    frequency_guess = next((name for rx, name in FREQ if rx.search(low)), None)
    is_bill = bool(re.search(r"\bbill\b", low)) or frequency_guess is not None

    date_word = None
    day_of_month = None
    if re.search(r"\btoday\b", low):
        date_word = "today"
    elif re.search(r"\byesterday\b", low):
        date_word = "yesterday"
    else:
        dm = re.search(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", low)
        if dm:
            day_of_month = int(dm.group(1))

    merchant = _merchant(text)
    confidence = 0.9
    if not INCOME.search(low) and not EXPENSE.search(low):
        confidence -= 0.2
    if not amt:
        confidence -= 0.15
    if not merchant:
        confidence -= 0.1
    return {"amount": amount, "direction": direction, "merchant": merchant or None, "channel": channel,
            "account_hint": account_hint, "category_guess": category_guess, "frequency_guess": frequency_guess,
            "is_bill": is_bill, "date_word": date_word, "day_of_month": day_of_month,
            "confidence": max(0.3, round(confidence, 2))}


def parse_voice(text):
    text = _clean(text)
    if not text:
        return {"rows": [], "error": "empty"}
    parts = [p for p in (_clean(x) for x in re.split(r"\s*,\s*|\s+and\s+", text, flags=re.I)) if p]
    have = [p for p in parts if _digits_amount(p) or _words_amount(p)]
    items = parts if len(have) >= 2 else [text]
    rows = []
    first_error = None
    for part in items:
        r = _parse_item(part)
        if r.get("error"):
            first_error = first_error or r["error"]
            continue
        rows.append(r)
    if not rows:
        return {"rows": [], "error": first_error or "no_amount"}
    if len(rows) > 1:
        fch = next((name for rx, name in CHANNELS if rx.search(text)), None)
        facc = None
        for rx in ACCOUNTS:
            m = rx.search(text)
            if m:
                facc = m.group(1).upper()
                break
        for r in rows:
            if not r["channel"] and fch:
                r["channel"] = fch
            if not r["account_hint"] and facc:
                r["account_hint"] = facc
    return {"rows": rows}
