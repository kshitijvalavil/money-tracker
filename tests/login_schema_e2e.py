"""End-to-end sign-in check against the REAL Supabase project (Sherlock Bugs).

Guards against "Database error querying schema": signs in every account given
and fails if any sign-in is not HTTP 200 with an access token.

    SUPABASE_URL=... SUPABASE_ANON_KEY=... \
    LOGIN_ACCOUNTS='a@x.com:pass1,b@x.com:pass2' python3 tests/login_schema_e2e.py

Passwords come from the environment only; nothing is stored in the repo.
"""
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["SUPABASE_URL"].rstrip("/")
key = os.environ["SUPABASE_ANON_KEY"]
accounts = [a.split(":", 1) for a in os.environ["LOGIN_ACCOUNTS"].split(",") if a]
failed = 0
for email, password in accounts:
    req = urllib.request.Request(
        f"{url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body, status = json.load(r), r.status
    except urllib.error.HTTPError as e:
        body, status = json.loads(e.read() or b"{}"), e.code
    ok = status == 200 and bool(body.get("access_token"))
    failed += not ok
    print(("PASS" if ok else "FAIL"), email, status, "" if ok else body.get("msg"))
sys.exit(1 if failed else 0)
