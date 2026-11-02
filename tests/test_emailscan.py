"""Tests for the "Check email now" button's server side (Sprint 9).

bot/emailscan.py and the /api/scan-email routes in app.py. Fully offline:
every Google / Supabase call goes through a fake opener. Synthetic emails only.
Run: python3 tests/test_emailscan.py
"""
import base64
import json
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# app.py reads these at import time: a real (non-demo) setup.
os.environ.pop("DEMO_MODE", None)
os.environ["SUPABASE_URL"] = "https://proj.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "anon-key"

from bot import emailscan, gmail_source  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    if not cond:
        print("FAIL:", msg)
        fails += 1


class Resp:
    def __init__(self, body, status=200):
        self._b = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.status = status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


def gmail_msg(mid, sender, subject, body):
    return {"id": mid, "internalDate": "1792800000000", "payload": {
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
        "mimeType": "text/plain", "body": {"data": b64(body)}}}


MAILBOX = {
    "s1": gmail_msg("s1", "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
                    "Your A/C XXXXX000001 Debited INR 250.00 on 15/10/26 -UPI. Avl Bal INR 1.00-SBI"),
    "h1": gmail_msg("h1", "alerts@hdfcbank.net", "Account update",
                    "Rs.1200.00 has been debited from account **4321 to VPA shop@okaxis on 16-10-26."),
    "old": gmail_msg("old", "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
                     "Your A/C XXXXX000001 Credited INR 900.00 on 10/10/26 -UPI. Avl Bal INR 1.00-SBI"),
    "ad": gmail_msg("ad", "offers@sbi.co.in", "Loan offer", "Get a loan today with YONO!"),
}


def make_opener(log, user=None, token="ya29.fake", existing=("old",), fail=None):
    user = user if user is not None else {"id": "u1", "email": "Boss@Example.com"}

    def opener(req, timeout=None):
        url = req.full_url
        log.append((req.get_method(), url, dict(req.header_items()), req.data))
        if fail and fail in url:
            raise urllib.error.URLError("down")
        if url.endswith("/auth/v1/user"):
            return Resp(user)
        if url.startswith(emailscan.TOKEN_URL):
            return Resp({"access_token": token} if token else {"error": "invalid_grant"})
        if "/rest/v1/pending_imports?select" in url:
            return Resp([{"source_message_id": e} for e in existing])
        if "/rest/v1/transactions?select" in url:
            return Resp([])
        if "/rest/v1/pending_imports?on_conflict" in url:
            return Resp(b"", 201)
        if "gmail.googleapis.com" in url and "/messages?" in url:
            return Resp({"messages": [{"id": k} for k in MAILBOX]})
        if "gmail.googleapis.com" in url and "/messages/" in url:
            mid = url.split("/messages/")[1].split("?")[0]
            return Resp(MAILBOX[mid])
        raise AssertionError("unexpected URL " + url)
    return opener


ENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec",
       "GMAIL_REFRESH_TOKEN": "rt", "GMAIL_OWNER_EMAIL": "boss@example.com"}

# --- configuration + owner check ------------------------------------------
check(emailscan.configured(ENV), "all four keys -> configured")
check(not emailscan.configured({**ENV, "GMAIL_REFRESH_TOKEN": " "}), "blank key -> not configured")
check(not emailscan.configured({}), "no keys -> not configured")
check(emailscan.is_owner(" BOSS@example.com ", ENV), "owner match ignores case/space")
check(not emailscan.is_owner("someone@example.com", ENV), "other user is not owner")
check(not emailscan.is_owner("", {}), "no owner set -> nobody is owner")

# --- default query covers every bank + Google Pay -------------------------
q = gmail_source.default_query()
for needle in ("alerts.sbi.bank.in", "hdfcbank", "kotak", "icici", "axisbank", "yesbank",
               "idfc", "slice", "googlepay-noreply@google.com", "newer_than:30d"):
    check(needle in q, f"default query includes {needle}")
check(gmail_source.default_query(7).endswith("newer_than:7d"), "days parameter used")

# --- who_is ----------------------------------------------------------------
log = []
u = emailscan.who_is("jwt1", "https://proj.supabase.co/", "anon", opener=make_opener(log))
check(u and u["id"] == "u1", "who_is returns the user")
check(log[0][1] == "https://proj.supabase.co/auth/v1/user", "who_is calls /auth/v1/user")
check(log[0][2].get("Authorization") == "Bearer jwt1" and log[0][2].get("Apikey") == "anon",
      "who_is sends the user's token + anon key")
check(emailscan.who_is("", "https://x", "a", opener=make_opener([])) is None, "no token -> None")
check(emailscan.who_is("j", "https://x", "a", opener=make_opener([], user={})) is None,
      "invalid token -> None")
check(emailscan.who_is("j", "https://x", "a", opener=make_opener([], fail="auth")) is None,
      "network error -> None")

# --- gmail_access_token ------------------------------------------------------
log = []
check(emailscan.gmail_access_token(ENV, opener=make_opener(log)) == "ya29.fake", "token exchanged")
body = log[0][3].decode()
check("grant_type=refresh_token" in body and "refresh_token=rt" in body, "refresh-token grant sent")
check(log[0][0] == "POST", "token request is a POST")
try:
    emailscan.gmail_access_token(ENV, opener=make_opener([], token=None))
    check(False, "bad refresh token should raise")
except emailscan.ScanError as e:
    check(e.status == 502 and "Gmail" in e.message, "bad refresh token -> friendly ScanError")

# --- run_scan ----------------------------------------------------------------
log = []
out = emailscan.run_scan("jwt1", "https://proj.supabase.co", "anon", "ya29.fake",
                         opener=make_opener(log))
check(out == {"checked": 4, "added": 2}, f"4 emails read, 2 new transactions ({out})")
push = [x for x in log if "on_conflict" in x[1]]
check(len(push) == 1, "one insert into pending_imports")
rows = json.loads(push[0][3])
check({r["source_message_id"] for r in rows} == {"s1", "h1"}, "already-imported + ads skipped")
check(push[0][2].get("Authorization") == "Bearer jwt1", "rows inserted AS the user (RLS)")
check(all("user_id" not in r and "status" not in r for r in rows), "no user_id/status sent")
gq = [x for x in log if "/messages?" in x[1]][0][1]
check("newer_than%3A30d" in gq, "Gmail searched with the 30-day all-banks query")
check(all("gmail.googleapis.com" not in x[1] or x[2].get("Authorization") == "Bearer ya29.fake"
          for x in log), "Gmail calls use the Gmail token, never the user's")

for where, word in (("gmail.googleapis.com", "read"), ("on_conflict", "save")):
    try:
        emailscan.run_scan("jwt1", "https://proj.supabase.co", "anon", "t",
                           opener=make_opener([], fail=where))
        check(False, f"{where} failure should raise")
    except emailscan.ScanError as e:
        check(word in e.message and "down" not in e.message,
              f"{where} failure -> friendly message, no internals ({e.message})")

# --- Flask routes --------------------------------------------------------------
import app as appmod  # noqa: E402

client = appmod.app.test_client()
real = (emailscan.who_is, emailscan.gmail_access_token, emailscan.run_scan)
calls = []


def fake_who(jwt, url, key, opener=None):
    return {"id": "u-" + jwt, "email": "boss@example.com" if jwt.startswith("boss") else "x@y.z"} \
        if jwt else None


emailscan.who_is = fake_who
emailscan.gmail_access_token = lambda env=None, opener=None: "tok"


def fake_run(jwt, url, key, token, **kw):
    calls.append((jwt, token))
    return {"checked": 5, "added": 1}


emailscan.run_scan = fake_run

for k in ENV:
    os.environ.pop(k, None)
r = client.get("/api/scan-email", headers={"Authorization": "Bearer boss1"})
check(r.get_json() == {"available": False, "reason": "not_configured"}, "status: not configured")
r = client.post("/api/scan-email", headers={"Authorization": "Bearer boss1"})
check(r.status_code == 503 and not calls, "scan refused (503) when keys not set")

os.environ.update(ENV)
r = client.post("/api/scan-email")
check(r.status_code == 401 and not calls, "no sign-in -> 401")
r = client.post("/api/scan-email", headers={"Authorization": "Bearer other1"})
check(r.status_code == 403 and r.get_json().get("reason") == "premium" and not calls,
      "other signed-in user -> 403 Premium, no scan")
r = client.get("/api/scan-email", headers={"Authorization": "Bearer other1"})
check(r.get_json() == {"available": False, "reason": "premium"}, "status for others: premium")
r = client.get("/api/scan-email", headers={"Authorization": "Bearer boss1"})
check(r.get_json() == {"available": True, "reason": "owner"}, "status for owner: available")
r = client.post("/api/scan-email", headers={"Authorization": "Bearer boss1"})
check(r.status_code == 200 and r.get_json() == {"checked": 5, "added": 1}, "owner scan works")
check(calls == [("boss1", "tok")], "scan runs with the owner's token + Gmail token")

codes = [client.post("/api/scan-email", headers={"Authorization": "Bearer boss1"}).status_code
         for _ in range(appmod._SCAN_MAX)]
check(codes[-1] == 429, f"rate limited after {appmod._SCAN_MAX} scans ({codes})")
r = client.post("/api/scan-email", headers={"Authorization": "Bearer boss2"})
check(r.status_code == 200, "rate limit is per user")


def boom(*a, **k):
    raise emailscan.ScanError("Couldn't read your email just now.", 502)


emailscan.run_scan = boom
r = client.post("/api/scan-email", headers={"Authorization": "Bearer boss3"})
check(r.status_code == 502 and "Couldn't read" in r.get_json()["error"], "scan error passed on")
check(r.headers.get("Content-Security-Policy"), "security headers on the API response")

emailscan.who_is, emailscan.gmail_access_token, emailscan.run_scan = real
print("emailscan tests:", "OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
