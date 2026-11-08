#!/usr/bin/env python3
"""Live login + data-isolation test (end-to-end, against the real Supabase).

This actually signs in as a tester over HTTPS using their email and password
(Supabase Auth / GoTrue), then reads every table through PostgREST with the
token it gets back, and proves the tester sees only their own rows and NONE of
the owner's. It is the real-world counterpart to tests/user_isolation_test.sql
(which proves the same thing at the database level inside CI).

It is NOT run in CI because it needs network access to Supabase and real
credentials. Run it by hand (or from an ops box) after adding a tester:

    export SUPABASE_URL="https://<ref>.supabase.co"
    export SUPABASE_ANON_KEY="<anon or publishable key>"
    export MT_TESTER_EMAIL="bharat.rao90@gmail.com"
    export MT_TESTER_PASSWORD="<the tester's password>"
    export MT_OWNER_USER_ID="<the owner's auth user id>"   # optional but recommended
    python3 tests/live_login_isolation_test.py

Exit code 0 = isolation proven, non-zero = a failure (details printed).
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
EMAIL = os.environ.get("MT_TESTER_EMAIL", "")
PASSWORD = os.environ.get("MT_TESTER_PASSWORD", "")
OWNER_ID = os.environ.get("MT_OWNER_USER_ID", "")  # optional

# Every table the app stores user data in (all have a user_id column).
TABLES = ["accounts", "audit_log", "budgets", "categories", "category_rules", "goal_contributions",
          "goals", "investments", "labels", "net_worth_snapshots", "pending_imports", "recurring",
          "sync_status", "transaction_labels", "transactions", "user_consents", "user_prefs"]

if not (URL and ANON and EMAIL and PASSWORD):
    sys.exit("Set SUPABASE_URL, SUPABASE_ANON_KEY, MT_TESTER_EMAIL and MT_TESTER_PASSWORD.")

fails = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def get(path, token):
    r = urllib.request.Request(URL + path, headers={"apikey": ANON, "Authorization": "Bearer " + token})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def login(email, pw):
    body = json.dumps({"email": email, "password": pw}).encode()
    r = urllib.request.Request(URL + "/auth/v1/token?grant_type=password", data=body,
                               headers={"apikey": ANON, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


print("=== Money Tracker live login + isolation test ===")
print(f"Signing in as {EMAIL} over HTTPS ...")
tok = login(EMAIL, PASSWORD)
at = tok.get("access_token")
uid = tok.get("user", {}).get("id")
check(bool(at), "real Supabase Auth login succeeded (received an access token)")
check(bool(uid), "token carries the tester's own user id")
if not at:
    print("\n=== RESULT: FAILED (could not sign in) ===")
    sys.exit(1)

print("\n-- Every row the tester can read must be their own --")
for t in TABLES:
    rows = get(f"/rest/v1/{t}?select=user_id", at)
    foreign = [x for x in rows if x.get("user_id") != uid]
    owner_rows = [x for x in rows if OWNER_ID and x.get("user_id") == OWNER_ID]
    msg = f"{t}: {len(rows)} rows visible, {len(foreign)} not the tester's"
    if OWNER_ID:
        msg += f", {len(owner_rows)} the owner's"
    check(len(foreign) == 0, msg)

if OWNER_ID:
    print("\n-- Direct attempt to read the owner's rows returns nothing --")
    for t in ("categories", "transactions", "accounts", "investments"):
        rows = get(f"/rest/v1/{t}?user_id=eq.{OWNER_ID}&select=user_id", at)
        check(len(rows) == 0, f"{t}: querying the owner's rows as the tester returns {len(rows)}")

print("\n-- Negative control: with no sign-in, RLS exposes nothing --")
r = urllib.request.Request(URL + "/rest/v1/categories?select=user_id",
                           headers={"apikey": ANON, "Authorization": "Bearer " + ANON})
with urllib.request.urlopen(r, timeout=30) as resp:
    anon_rows = json.loads(resp.read().decode())
check(len(anon_rows) == 0, f"anonymous (not signed in) sees {len(anon_rows)} rows")

print("\n=== RESULT:", "ALL PASSED" if not fails else f"{len(fails)} FAILURE(S)", "===")
sys.exit(1 if fails else 0)
