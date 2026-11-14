#!/usr/bin/env python3
"""Live browser end-to-end test: a real Chromium driving the LIVE site as a brand-new person.

Needs a TEMPORARY test login (see docs/adding-test-users.md). It goes through the real screens:

  sign in -> consent screen appears -> accept -> every page loads with ZERO CSP violations and
  page errors -> Profile shows the activity log -> set up two-step sign-in with a real authenticator
  code -> download all data (17 tables) -> sign out -> sign in again: the code step appears -> enter code
  -> self-hosted OCR runs under the live policy -> "Delete my account" -> the login no longer works.

    export SITE_URL=https://money-tracker-iabp.onrender.com
    export E2E_EMAIL=e2e-xxxx@example.com E2E_PASSWORD=<its password>
    python3 tests/live_ui_e2e_test.py

Supabase insists a code challenge and its answer come from the same IP address; some test networks
rotate the outgoing address, so every code entry is retried (a real phone keeps one address).
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time

from playwright.sync_api import sync_playwright

SITE = os.environ.get("SITE_URL", "").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL", "")
PASSWORD = os.environ.get("E2E_PASSWORD", "")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
if not (SITE and EMAIL and PASSWORD):
    sys.exit("Set SITE_URL, E2E_EMAIL and E2E_PASSWORD.")

PAGES = ["/", "/transactions", "/accounts", "/budgets", "/bills", "/calendar", "/goals", "/investments",
         "/net-worth", "/forecast", "/insights", "/alerts", "/ask", "/imports", "/profile"]
TABLES = ["accounts", "audit_log", "budgets", "categories", "category_rules", "goal_contributions", "goals",
          "investments", "labels", "net_worth_snapshots", "pending_imports", "recurring", "sync_status",
          "transaction_labels", "transactions", "user_consents", "user_prefs"]
fails = []
_last_window = [0]


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def totp(secret_b32, t=None):
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    h = hmac.new(key, struct.pack(">Q", int((t or time.time()) // 30)), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def fresh_code(secret):
    """A code works once per 30-second window: wait for a new window if the last one was used."""
    w = int(time.time() // 30)
    if w <= _last_window[0]:
        time.sleep((w + 1) * 30 - time.time() + 1)
    _last_window[0] = int(time.time() // 30)
    return totp(secret)


def enter_code_until(page, input_sel, button_sel, secret, done, tries=10):
    """Type the current code and submit; if the server refuses it (IP mismatch on a rotating network) try again."""
    for _ in range(tries):
        page.fill(input_sel, fresh_code(secret))
        page.click(button_sel)
        for _ in range(16):
            page.wait_for_timeout(500)
            try:
                if done():
                    return True
            except Exception:
                pass          # page is navigating
        _last_window[0] = 0   # a refused code was not consumed, the same window is fine
    return False


WATCH = ("window.__v = []; document.addEventListener('securitypolicyviolation', "
         "(e) => window.__v.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline')));")


def main():
    launch = {"args": ["--no-sandbox"]}
    if os.path.exists(CHROME):
        launch["executable_path"] = CHROME
    if os.environ.get("HTTPS_PROXY"):
        launch["proxy"] = {"server": os.environ["HTTPS_PROXY"]}
    print(f"=== Live browser test against {SITE} ===")
    with sync_playwright() as p:
        b = p.chromium.launch(**launch)
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, service_workers="block", accept_downloads=True)
        ctx.add_init_script(WATCH)
        page = ctx.new_page()
        errs, csp_msgs = [], []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: csp_msgs.append(m.text) if ("Content Security Policy" in m.text or "Refused to" in m.text) else None)
        page.on("dialog", lambda d: d.accept())

        # 1. Sign in -> consent screen
        page.goto(SITE + "/login", wait_until="networkidle")
        check(page.is_visible("#loginForm") and page.is_hidden("#mfaForm"), "login page shows the password form first")
        check("Privacy notice" in page.inner_text("body"), "login page links the privacy notice")
        page.fill("#email", EMAIL); page.fill("#password", PASSWORD); page.click("#submit")
        page.wait_for_url("**/consent", timeout=30000)
        check(page.url.endswith("/consent"), "first sign-in lands on the privacy consent screen, not the app")
        check("Before you start" in page.inner_text("body"), "consent screen explains the notice")
        page.click("#accept")
        page.wait_for_timeout(800)
        check(page.url.endswith("/consent"), "cannot continue without ticking the box")
        page.check("#agree"); page.click("#accept")
        page.wait_for_url(SITE + "/", timeout=30000)
        page.wait_for_load_state("networkidle")
        check(page.url == SITE + "/", "accepting the notice opens the app")

        # 2. Every page, as a real signed-in person
        for path in PAGES:
            pg = ctx.new_page()
            perrs = []
            pg.on("pageerror", lambda e, perrs=perrs: perrs.append(str(e)))
            pg.goto(SITE + path, wait_until="networkidle")
            pg.wait_for_timeout(700)
            v = pg.evaluate("window.__v")
            over = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            stayed = pg.url.rstrip("/") == (SITE + path).rstrip("/")
            check(stayed and not v and not perrs and over <= 1,
                  f"{path}: loads signed in, no CSP violations, no page errors, no sideways scroll {v[:1] or perrs[:1] or ''}")
            pg.close()

        # 3. Profile: activity log, set up two-step
        page.goto(SITE + "/profile", wait_until="networkidle")
        page.wait_for_timeout(800)
        act = page.inner_text("#activityList")
        check("Accepted the privacy notice" in act, "activity log shows 'Accepted the privacy notice'")
        check("Off" in page.inner_text("#mfaStatus"), "two-step starts off")
        page.click("#mfaStart")
        page.wait_for_selector("#mfaSetup", state="visible", timeout=15000)
        check(page.get_attribute("#mfaQr", "src").startswith("data:image/svg+xml"), "a QR code is shown (data: image, allowed by the policy)")
        secret = page.inner_text("#mfaSecret").strip()
        check(len(secret) >= 16, "a typed-in key is shown too")
        ok = enter_code_until(page, "#mfaCode", "#mfaOn", secret, lambda: "On" in page.inner_text("#mfaStatus").split(":")[0])
        check(ok, "a real authenticator code turns two-step on")
        page.wait_for_timeout(800)
        check("Two-step sign-in turned on" in page.inner_text("#activityList"), "activity log records it")

        # 4. Download everything
        with page.expect_download(timeout=60000) as dl:
            page.click("#exportBtn")
        data = json.load(open(dl.value.path()))
        check(sorted(data["tables"].keys()) == sorted(TABLES), "download covers all 17 tables")
        check(len(data["tables"]["categories"]) == 12 and data["tables"]["accounts"][0]["name"] == "Cash", "download holds this person's own data")

        # 5. Sign out, sign in again: the code step appears
        page.click("#signoutBtn")
        page.wait_for_url("**/login", timeout=30000)
        page.fill("#email", EMAIL); page.fill("#password", PASSWORD); page.click("#submit")
        page.wait_for_selector("#mfaForm", state="visible", timeout=30000)
        check(page.is_hidden("#loginForm"), "after the password, the sign-in page asks for the 6-digit code")
        page.goto(SITE + "/transactions")          # try to skip the second step
        page.wait_for_timeout(1500)
        check("/login" in page.url, "skipping the code step bounces back to sign-in")
        page.fill("#email", EMAIL) if page.is_visible("#email") else None
        if page.is_visible("#loginForm"):
            page.fill("#email", EMAIL); page.fill("#password", PASSWORD); page.click("#submit")
            page.wait_for_selector("#mfaForm", state="visible", timeout=30000)
        ok = enter_code_until(page, "#mfaCode", "#mfaSubmit", secret, lambda: "/login" not in page.url)
        check(ok, "the right code completes sign-in")
        page.wait_for_url(SITE + "/", timeout=30000)
        check(page.url == SITE + "/", "and the app opens (no consent screen again)")

        # 6. Self-hosted OCR under the live policy
        page.goto(SITE + "/bills", wait_until="networkidle")
        page.add_script_tag(url="/static/vendor/tesseract/tesseract.min.js")
        page.wait_for_timeout(300)
        text = page.evaluate("""async () => {
          const c = document.createElement('canvas'); c.width = 900; c.height = 240;
          const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, 900, 240);
          g.fillStyle = '#000'; g.font = 'bold 64px Arial'; g.fillText('ELECTRICITY BILL', 30, 90); g.fillText('Amount Rs 1234', 30, 190);
          const base = location.origin + '/static/vendor/tesseract';
          const w = await Tesseract.createWorker('eng', 1, { workerPath: base + '/worker.min.js', corePath: base, langPath: base + '/lang' });
          try { const r = await w.recognize(c.toDataURL('image/png')); return r.data.text; } finally { await w.terminate(); }
        }""")
        check("ELECTRICITY" in (text or "").upper() and "1234" in (text or ""), f"OCR runs on the live site under the live policy (read {text!r})")
        check(not page.evaluate("window.__v"), "OCR caused no policy violations")

        check(not errs and not csp_msgs, f"whole session: no page errors and no CSP console errors ({(errs + csp_msgs)[:2]})")

        # 7. Delete my account through the real screen
        page.goto(SITE + "/profile", wait_until="networkidle")
        page.fill("#delPw", PASSWORD); page.fill("#delConfirm", "DELETE")
        page.click("#delSubmit")
        page.wait_for_url("**/login", timeout=45000)
        check("/login" in page.url, "Delete my account signs the person out and returns to sign-in")
        page.fill("#email", EMAIL); page.fill("#password", PASSWORD); page.click("#submit")
        page.wait_for_timeout(2500)
        check(page.is_visible("#loginForm") and page.is_hidden("#mfaForm") and "/login" in page.url, "the deleted login can no longer sign in")
        b.close()

    print("\n=== RESULT:", "ALL PASSED" if not fails else f"{len(fails)} FAILURE(S)", "===")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
