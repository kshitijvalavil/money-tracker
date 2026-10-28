"""Security hardening checks (Sprint 6).

Runs with no network and no browser, via Flask's test client:

    python3 tests/security_test.py

Covers: every response carries the security headers, the CSP locks down
sources while still allowing Supabase, the paid /api/read-bill endpoint is
rate limited (429 with Retry-After), auth is required for it outside demo, and
debug is off by default.
"""
import os
import sys

# Configure BEFORE importing the app — it reads these at import time.
os.environ["DEMO_MODE"] = "1"
os.environ["READ_BILL_RATE_MAX"] = "5"
os.environ["READ_BILL_RATE_WINDOW"] = "60"
os.environ.pop("FLASK_DEBUG", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def run():
    client = appmod.app.test_client()

    # ---- Security headers on a normal page -------------------------------
    r = client.get("/")
    h = r.headers
    csp = h.get("Content-Security-Policy", "")
    check(bool(csp), "a Content-Security-Policy is sent")
    check("default-src 'self'" in csp, "CSP defaults to self")
    check("object-src 'none'" in csp, "CSP blocks plugins (object-src none)")
    check("frame-ancestors 'none'" in csp, "CSP blocks framing (clickjacking)")
    check("*.supabase.co" in csp, "CSP still allows Supabase connections")
    check(h.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options: nosniff")
    check(h.get("X-Frame-Options") == "DENY", "X-Frame-Options: DENY")
    check("strict-origin" in (h.get("Referrer-Policy") or ""), "a Referrer-Policy is set")
    check("max-age=" in (h.get("Strict-Transport-Security") or ""), "HSTS is set")
    check("microphone=()" in (h.get("Permissions-Policy") or ""), "Permissions-Policy restricts sensors")

    # ---- Debug is off by default -----------------------------------------
    check(appmod.app.debug is False, "Flask debug is off by default")

    # ---- Rate limit on the paid vision endpoint (demo mode) --------------
    payload = {"image": "x", "mime": "image/png"}
    codes = [client.post("/api/read-bill", json=payload).status_code for _ in range(6)]
    check(codes[:5] == [200] * 5, f"first 5 reads allowed (got {codes[:5]})")
    check(codes[5] == 429, f"the 6th read is rate limited with 429 (got {codes[5]})")
    last = client.post("/api/read-bill", json=payload)
    check(last.headers.get("Retry-After") is not None, "a Retry-After header is sent on 429")

    # ---- Auth required outside demo mode ---------------------------------
    appmod.DEMO_MODE = False
    try:
        r2 = client.post("/api/read-bill", json=payload)
        check(r2.status_code == 401, f"without a session the endpoint returns 401 (got {r2.status_code})")
    finally:
        appmod.DEMO_MODE = True


def main():
    run()
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsecurity_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All security checks passed.")


if __name__ == "__main__":
    main()
