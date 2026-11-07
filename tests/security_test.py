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
    # A header that is merely PRESENT must not be enough: Supabase must vouch
    # for the token. Stub the Supabase lookup so no network is needed.
    appmod.DEMO_MODE = False
    real_fetch = appmod._fetch_supabase_user
    calls = []

    def fake_fetch(token):
        calls.append(token)
        return {"good-token-a": "user-a", "good-token-b": "user-b"}.get(token)

    appmod._fetch_supabase_user = fake_fetch
    appmod._AUTH_CACHE.clear()
    appmod._RL_HITS.clear()
    try:
        r2 = client.post("/api/read-bill", json=payload)
        check(r2.status_code == 401, f"without a session the endpoint returns 401 (got {r2.status_code})")
        r3 = client.post("/api/read-bill", json=payload, headers={"Authorization": "x"})
        check(r3.status_code == 401, f"a made-up Authorization header is rejected (got {r3.status_code})")
        r4 = client.post("/api/read-bill", json=payload, headers={"Authorization": "Bearer forged"})
        check(r4.status_code == 401, f"a forged bearer token is rejected (got {r4.status_code})")
        r5 = client.post("/api/read-bill", json=payload, headers={"Authorization": "Basic good-token-a"})
        check(r5.status_code == 401, f"a non-Bearer scheme is rejected (got {r5.status_code})")
        check("Basic good-token-a" not in calls, "non-Bearer headers never reach Supabase")

        # The free parser needs a session as well.
        t = {"text": "Electricity bill\nAmount due Rs 1,234.00\nDue date 15/12/2026"}
        p1 = client.post("/api/parse-bill-text", json=t)
        check(p1.status_code == 401, f"parse-bill-text without a session returns 401 (got {p1.status_code})")
        p2 = client.post("/api/parse-bill-text", json=t, headers={"Authorization": "Bearer forged"})
        check(p2.status_code == 401, f"parse-bill-text with a forged token returns 401 (got {p2.status_code})")
        p3 = client.post("/api/parse-bill-text", json=t, headers={"Authorization": "Bearer good-token-a"})
        check(p3.status_code == 200, f"parse-bill-text with a valid token works (got {p3.status_code})")

        # Valid tokens are remembered briefly, not re-checked every request.
        before = len(calls)
        client.post("/api/parse-bill-text", json=t, headers={"Authorization": "Bearer good-token-a"})
        check(len(calls) == before, "a verified token is cached (no repeat Supabase call)")

        # Limits are per person: a spoofed X-Forwarded-For cannot dodge them,
        # and one person's limit does not lock out another.
        appmod._RL_HITS.clear()
        codes = []
        for i in range(7):
            codes.append(client.post("/api/parse-bill-text", json=t, headers={
                "Authorization": "Bearer good-token-a",
                "X-Forwarded-For": f"203.0.113.{i}, 10.0.0.1"}).status_code)
        check(codes[:5] == [200] * 5 and codes[5] == 429, f"user limit holds despite spoofed IPs (got {codes})")
        other = client.post("/api/parse-bill-text", json=t, headers={
            "Authorization": "Bearer good-token-b", "X-Forwarded-For": "198.51.100.9, 10.0.0.2"})
        check(other.status_code == 200, f"another user is not locked out (got {other.status_code})")
    finally:
        appmod._fetch_supabase_user = real_fetch
        appmod._AUTH_CACHE.clear()
        appmod.DEMO_MODE = True

    # ---- Maintenance mode: friendly 503 page, but health + status stay up ----
    appmod.MAINTENANCE = True
    try:
        for path in ("/", "/transactions", "/profile"):
            m = client.get(path)
            check(m.status_code == 503, f"maintenance: {path} answers 503 (got {m.status_code})")
        m = client.get("/")
        check(m.headers.get("Retry-After") is not None, "maintenance: Retry-After is sent")
        check("Captain Shipit" in m.get_data(as_text=True), "maintenance: the friendly updating page is shown")
        check(client.get("/healthz").status_code == 200, "maintenance: /healthz stays up so Render keeps the service")
        st = client.get("/api/status")
        check(st.status_code == 200 and st.get_json() == {"ok": True, "deploying": True}, "maintenance: /api/status says deploying")
        check(client.get("/static/css/deploying.css").status_code == 200, "maintenance: the page's own CSS still loads")
        check(client.get("/sw.js").status_code == 200, "maintenance: the service worker still loads")
        check(client.get("/deploying").status_code == 200, "maintenance: /deploying itself is a normal 200 (so it can be cached)")
        check("max-age" not in (client.get("/api/status").headers.get("Cache-Control") or ""), "maintenance: status is never cached")
    finally:
        appmod.MAINTENANCE = False
    check(client.get("/").status_code == 200, "normal mode: pages load again")
    check(client.get("/api/status").get_json() == {"ok": True, "deploying": False}, "normal mode: /api/status says not deploying")

    # ---- Client IP: trust the proxy-appended entry, not the client's ------
    with appmod.app.test_request_context(headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"}):
        check(appmod._client_ip() == "203.0.113.7", "client IP is the proxy-appended entry, not a spoofed first one")
    with appmod.app.test_request_context(headers={}, environ_base={"REMOTE_ADDR": "192.0.2.5"}):
        check(appmod._client_ip() == "192.0.2.5", "with no forwarding header the peer address is used")


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
