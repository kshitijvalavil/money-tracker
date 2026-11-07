#!/usr/bin/env python3
"""Live end-to-end security test against the REAL Supabase (and optionally the live site).

Uses a TEMPORARY test login that you create first (see docs/adding-test-users.md), signs in
over HTTPS exactly like a browser does, and checks everything Sprint 14 added:

  sign-in -> activity log -> consent -> two-step sign-in (enrol + verify, standard TOTP)
  -> a fresh password-only session is NOT enough (aal1) until the code is entered (aal2)
  -> export reads every table -> sign out of all devices kills every session
  -> live site bill endpoint accepts the real token and refuses forged ones
  -> delete my account removes the login and every row (the login then fails)

It ends by deleting the test user through the app's own delete_my_account(), so a pass
also proves that function. Not run in CI (it needs real credentials and the network).

    export SUPABASE_URL=https://<ref>.supabase.co
    export SUPABASE_ANON_KEY=<publishable or anon key>
    export E2E_EMAIL=e2e-xxxx@example.com E2E_PASSWORD=<its password>
    export SITE_URL=https://money-tracker-iabp.onrender.com     # optional
    python3 tests/live_e2e_security_test.py
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
EMAIL = os.environ.get("E2E_EMAIL", "")
PASSWORD = os.environ.get("E2E_PASSWORD", "")
SITE = os.environ.get("SITE_URL", "").rstrip("/")
if not (URL and ANON and EMAIL and PASSWORD):
    sys.exit("Set SUPABASE_URL, SUPABASE_ANON_KEY, E2E_EMAIL and E2E_PASSWORD.")

TABLES = ["accounts", "audit_log", "budgets", "categories", "category_rules", "goal_contributions",
          "goals", "investments", "labels", "net_worth_snapshots", "pending_imports", "recurring",
          "sync_status", "transaction_labels", "transactions", "user_consents", "user_prefs"]
fails = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def call(method, path, token=None, body=None, headers=None, base=None, raw=False):
    h = {"apikey": ANON, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request((base or URL) + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
            return r.status, (txt if raw else (json.loads(txt) if txt else None))
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def jwt_claims(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def totp(secret_b32, t=None):
    """Standard RFC 6238 code (SHA-1, 30 s, 6 digits), what any authenticator app shows."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((t or time.time()) // 30)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


_last_window = [0]


def challenge_and_verify(token, fid, code_fn):
    """Ask for a challenge, then answer it. Supabase insists both requests come from the same IP address.
    Some test networks (egress proxies) rotate the outgoing address, which shows up as
    'mfa_ip_address_mismatch'. A real browser keeps one address, so just try again in that case."""
    for _ in range(12):
        st, ch = call("POST", f"/auth/v1/factors/{fid}/challenge", token, {})
        if st != 200:
            return st, ch
        st, r = call("POST", f"/auth/v1/factors/{fid}/verify", token, {"challenge_id": ch["id"], "code": code_fn()})
        if isinstance(r, dict) and r.get("error_code") == "mfa_ip_address_mismatch":
            continue
        return st, r
    return st, r


def verify_code(token, fid, secret, label):
    """Verify with a fresh code. A code works once per 30-second window, so wait for a new window if needed."""
    w = int(time.time() // 30)
    if w <= _last_window[0]:
        time.sleep((w + 1) * 30 - time.time() + 1)
    st, r = challenge_and_verify(token, fid, lambda: totp(secret))
    _last_window[0] = int(time.time() // 30)
    return st, r


def login():
    return call("POST", "/auth/v1/token?grant_type=password", body={"email": EMAIL, "password": PASSWORD})


print("=== Money Tracker live end-to-end security test ===")

# 1. Password sign-in
st, s1 = login()
check(st == 200 and s1.get("access_token"), "password sign-in works over HTTPS")
if st != 200:
    sys.exit(1)
tok = s1["access_token"]
uid = s1["user"]["id"]
check(jwt_claims(tok).get("aal") == "aal1", "a password-only session is assurance level aal1")

# 2. Activity log: add, read back, cannot edit or delete
st, _ = call("POST", "/rest/v1/audit_log", tok, {"event": "sign_in", "detail": {"via": "e2e"}}, {"Prefer": "return=minimal"})
check(st == 201, f"activity event is saved (HTTP {st})")
st, rows = call("GET", "/rest/v1/audit_log?select=event,user_id", tok)
check(st == 200 and any(r["event"] == "sign_in" for r in rows) and all(r["user_id"] == uid for r in rows), "activity log shows my event and only mine")
st, _ = call("PATCH", "/rest/v1/audit_log?event=eq.sign_in", tok, {"event": "idle_lock"}, {"Prefer": "return=minimal"})
check(st in (401, 403), f"editing an activity row is refused (HTTP {st})")
st, _ = call("DELETE", "/rest/v1/audit_log?event=eq.sign_in", tok, None, {"Prefer": "return=minimal"})
check(st in (401, 403), f"deleting an activity row is refused (HTTP {st})")
st, _ = call("POST", "/rest/v1/audit_log", tok, {"event": "made_up"}, {"Prefer": "return=minimal"})
check(st >= 400, f"an unknown event type is refused (HTTP {st})")
st, anon_rows = call("GET", "/rest/v1/audit_log?select=event", None, headers={"Authorization": "Bearer " + ANON})
check(st in (200, 401, 403) and not (isinstance(anon_rows, list) and anon_rows), "anonymous visitors see no activity rows")

# 3. Consent
st, _ = call("POST", "/rest/v1/user_consents?on_conflict=user_id", tok, {"user_id": uid, "notice_version": "e2e-test"},
             {"Prefer": "resolution=merge-duplicates,return=minimal"})
check(st in (200, 201, 204), f"consent is saved (HTTP {st})")
st, rows = call("GET", "/rest/v1/user_consents?select=notice_version", tok)
check(st == 200 and rows and rows[0]["notice_version"] == "e2e-test", "consent reads back")

# 4. Two-step sign-in: enrol and verify
st, f = call("POST", "/auth/v1/factors", tok, {"factor_type": "totp", "friendly_name": "e2e-" + str(int(time.time()))})
check(st == 200 and f.get("totp", {}).get("secret"), f"authenticator enrolment returns a secret and QR (HTTP {st})")
if st != 200:
    print("  details:", f)
    sys.exit(1)
fid, secret = f["id"], f["totp"]["secret"]
check("<svg" in f["totp"]["qr_code"], "the QR code is an SVG image (the browser library turns it into a data: URI, allowed by our CSP)")
st, ch = call("POST", f"/auth/v1/factors/{fid}/challenge", tok, {})
check(st == 200 and ch.get("id"), "a challenge is issued")
st, ok = verify_code(tok, fid, secret, "enrol")
check(st == 200 and ok.get("access_token"), f"the right code turns two-step on (HTTP {st})")
tok2 = ok["access_token"]
check(jwt_claims(tok2).get("aal") == "aal2", "after the code the session is assurance level aal2")

# 5. A brand-new password-only login is NOT enough any more
st, s2 = login()
check(st == 200 and jwt_claims(s2["access_token"]).get("aal") == "aal1", "a fresh password-only login is still aal1 (the app then demands the code)")
st, u = call("GET", "/auth/v1/user", s2["access_token"])
verified = [x for x in (u.get("factors") or []) if x.get("status") == "verified"]
check(st == 200 and len(verified) == 1, "the account lists exactly one verified authenticator (so the app knows to ask)")
# A mistyped code, then the right one straight away (what a real person does)
st, bad = challenge_and_verify(s2["access_token"], fid, lambda: "000000")
check(st == 422 and bad.get("error_code") == "mfa_verification_failed", f"a mistyped code at sign-in is refused (HTTP {st}, {bad.get('error_code')})")
st, ok2 = verify_code(s2["access_token"], fid, secret, "sign-in after a mistype")
check(st == 200 and ok2.get("access_token") and jwt_claims(ok2["access_token"]).get("aal") == "aal2", "the right code straight after a mistyped one is accepted and lifts the session to aal2")
tok3 = ok2["access_token"]

# 6. Export: every table readable, only mine
total = 0
for t in TABLES:
    st, rows = call("GET", f"/rest/v1/{t}?select=*", tok3)
    good = st == 200 and isinstance(rows, list)
    total += len(rows) if good else 0
    check(good, f"export can read {t} ({len(rows) if good else st} rows)")
st, cats = call("GET", "/rest/v1/categories?select=name", tok3)
check(len(cats) == 12, "a new login got its own 12 starter categories")

# 7. Live site: the real token is accepted, forged ones are not
if SITE:
    sample = {"text": "Electricity bill\nAmount due Rs 1,234.00\nDue date 15/12/2026"}
    st, _ = call("POST", "/api/parse-bill-text", tok3, sample, base=SITE, raw=True)
    check(st == 200, f"live site: bill endpoint accepts a real signed-in token (HTTP {st})")
    st, _ = call("POST", "/api/parse-bill-text", "forged.token.value", sample, base=SITE, raw=True)
    check(st == 401, f"live site: a forged token gets 401 (HTTP {st})")
    st, _ = call("POST", "/api/parse-bill-text", None, sample, base=SITE, raw=True)
    check(st == 401, f"live site: no token gets 401 (HTTP {st})")

# 8. Sign out of all devices
st, _ = call("POST", "/auth/v1/logout?scope=global", tok3, {}, raw=True)
check(st in (200, 204), f"sign out of all devices accepted (HTTP {st})")
st, _ = call("GET", "/auth/v1/user", tok, raw=True)
check(st in (401, 403), f"an older session no longer works after global sign-out (HTTP {st})")
st, _ = call("GET", "/auth/v1/user", tok3, raw=True)
check(st in (401, 403), f"the newest session no longer works either (HTTP {st})")

# 9. Delete my account (needs a fresh session; two-step is on so go through aal2 again)
st, s4 = login()
t4 = s4["access_token"]
st, ok4 = verify_code(t4, fid, secret, "before delete")
check(st == 200, f"fresh session passes two-step before deleting (HTTP {st})")
t4 = ok4["access_token"]
st, _ = call("POST", "/rest/v1/rpc/delete_my_account", t4, {}, raw=True)
check(st in (200, 204), f"delete_my_account succeeds (HTTP {st})")
st, r = call("POST", "/auth/v1/token?grant_type=password", body={"email": EMAIL, "password": PASSWORD})
check(st in (400, 401, 422) and not (isinstance(r, dict) and r.get("access_token")), f"the deleted login can no longer sign in (HTTP {st})")

print("\n=== RESULT:", "ALL PASSED" if not fails else f"{len(fails)} FAILURE(S)", "===")
sys.exit(1 if fails else 0)
