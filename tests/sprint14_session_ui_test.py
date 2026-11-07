"""Sprint 14 UI checks: two-step sign-in set-up, activity log, privacy notice, idle lock (demo mode).

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint14_session_ui_test.py

At phone and desktop size, light and dark:
  * Profile: set up an authenticator (QR shown, wrong code refused, right code turns it on),
    the activity list records it, turning it off works,
  * "Sign out of all devices" and the Your data card are present,
  * the privacy notice is public, readable, honest (names who holds the data) and fits the screen,
  * security.txt is served with a contact and an expiry,
  * the idle-lock logic: not idle at 29:59, idle at 30:00, locks exactly once.
"""
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")
results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def run(ctx, tag):
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    page.goto(BASE + "/profile", wait_until="networkidle")
    page.wait_for_timeout(500)
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 1, f"{tag} profile: no sideways scrolling ({over}px)")
    check(page.is_visible("#signoutAllBtn"), f"{tag} profile: sign out of all devices is there")
    check("Off" in page.inner_text("#mfaStatus"), f"{tag} profile: two-step starts off")
    check(page.is_hidden("#mfaSetup"), f"{tag} profile: set-up panel hidden at first")

    page.click("#mfaStart")
    page.wait_for_selector("#mfaSetup", state="visible", timeout=5000)
    check(page.get_attribute("#mfaQr", "src").startswith("data:image/svg+xml"), f"{tag} profile: QR code is shown")
    check(len(page.inner_text("#mfaSecret").strip()) >= 8, f"{tag} profile: text key shown for people who cannot scan")
    page.fill("#mfaCode", "000000")
    page.click("#mfaOn")
    page.wait_for_timeout(300)
    check("Off" in page.inner_text("#mfaStatus"), f"{tag} profile: a wrong code does not turn it on")
    check(page.is_visible("#mfaSetup"), f"{tag} profile: set-up stays open after a wrong code")
    page.fill("#mfaCode", "123456")
    page.click("#mfaOn")
    page.wait_for_timeout(600)
    check("On" in page.inner_text("#mfaStatus"), f"{tag} profile: the right code turns it on")
    check(page.is_visible("#mfaRemove") and page.is_hidden("#mfaStart"), f"{tag} profile: Turn off appears, Set up goes away")
    check("Two-step sign-in turned on" in page.inner_text("#activityList"), f"{tag} profile: activity log shows it turned on")
    page.click("#mfaRemove")
    page.wait_for_timeout(600)
    check("Off" in page.inner_text("#mfaStatus"), f"{tag} profile: turning it off works")
    check("turned off" in page.inner_text("#activityList"), f"{tag} profile: activity log shows it turned off")
    check(not errs, f"{tag} profile: no page errors ({errs[:1]})")

    # Privacy notice: public and honest
    page.goto(BASE + "/privacy", wait_until="networkidle")
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 1, f"{tag} privacy: no sideways scrolling ({over}px)")
    text = page.inner_text("body")
    for needle in ("Supabase", "Render", "12 months", "administrator access", "Download all my data", "Delete my account", "without delay"):
        check(needle in text, f"{tag} privacy: says '{needle}'")
    check(EMOJI.search(text) is None, f"{tag} privacy: no emoji")
    page.close()


def idle_logic(ctx):
    page = ctx.new_page()
    page.goto(BASE + "/profile", wait_until="networkidle")
    out = page.evaluate("""async () => {
      const m = await import('/static/js/session.js');
      const L = m.IDLE_MS, base = 1000000000000;
      const r = {
        limit: L,
        notYet: m.isIdle(base + L - 1, base),
        atLimit: m.isIdle(base + L, base),
        noRecord: m.isIdle(base, 0),
      };
      let now = base, locks = 0;
      localStorage.removeItem('mt_last_active');
      const stop = m.startIdleLock(() => { locks++; }, { now: () => now });
      now = base + L - 1000; document.dispatchEvent(new Event('visibilitychange'));
      r.locksBefore = locks;
      now = base + L + 1000; window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('focus'));
      r.locksAfter = locks;
      stop();
      return r;
    }""")
    check(out["limit"] == 30 * 60 * 1000, "idle: limit is 30 minutes")
    check(out["notYet"] is False, "idle: not idle one millisecond before 30 minutes")
    check(out["atLimit"] is True, "idle: idle at exactly 30 minutes")
    check(out["noRecord"] is False, "idle: no activity record is never treated as idle")
    check(out["locksBefore"] == 0, "idle: does not lock before the limit")
    check(out["locksAfter"] == 1, f"idle: locks exactly once after the limit (got {out['locksAfter']})")
    page.close()


def main():
    sec = urllib.request.urlopen(BASE + "/.well-known/security.txt", timeout=10).read().decode()
    check("Contact:" in sec and "Expires:" in sec, "security.txt has a contact and an expiry")
    check("github.com/kshitijvalavil/money-tracker/security/advisories/new" in sec, "security.txt points to private vulnerability reporting")
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        for scheme in ("light", "dark"):
            c = b.new_context(**p.devices["iPhone 13"], color_scheme=scheme, service_workers="block")
            run(c, f"phone {scheme}"); c.close()
            c = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, service_workers="block")
            run(c, f"desktop {scheme}"); c.close()
        c = b.new_context(viewport={"width": 1000, "height": 800}, service_workers="block")
        idle_logic(c); c.close()
        b.close()
    passed = sum(1 for s, _ in results if s == "PASS")
    print(f"\nsprint14_session_ui_test: {passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)
    print("All Sprint 14 session UI checks passed.")


if __name__ == "__main__":
    main()
