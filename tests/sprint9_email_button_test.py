"""Browser test for the "Check email now" button (Sprint 9), against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint9_email_button_test.py

On Transactions > From email, the button shows, says what it's doing while it
runs, reports the result, and fits on both a desktop and a phone screen. Demo
mode reads no real email.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def run(ctx, label):
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"{label} console.error: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"{label} pageerror: {e}"))
    pg.goto(BASE + "/imports", wait_until="networkidle")
    btn = pg.locator("#checkEmail")
    btn.wait_for(state="visible", timeout=5000)
    check(btn.inner_text().strip() == "Check email now", f"{label}: button labelled 'Check email now'")
    box = btn.bounding_box()
    vw = pg.evaluate("window.innerWidth")
    check(box and box["x"] >= 0 and box["x"] + box["width"] <= vw, f"{label}: button fully on screen")
    check(box and box["height"] >= 32, f"{label}: button big enough to tap ({box and box['height']}px)")
    check("Demo mode" in pg.inner_text("#scanStatus"), f"{label}: demo note shown")
    btn.click()
    pg.wait_for_selector("#scanStatus:has-text('no real email was read')", timeout=5000)
    check(not btn.is_disabled() and btn.inner_text().strip() == "Check email now",
          f"{label}: button ready again after the check")
    check(pg.locator("#rows tr").count() > 0, f"{label}: imports list still shown")
    over = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 1, f"{label}: no sideways scrolling ({over}px)")
    text = pg.inner_text("body")
    check(not any(ord(ch) >= 0x1F300 for ch in text), f"{label}: no emojis")
    pg.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        run(b.new_context(viewport={"width": 1366, "height": 860}, service_workers="block"), "web")
        run(b.new_context(**p.devices["iPhone 13"], service_workers="block"), "phone")
        b.close()
    for e in errors:
        print("  [browser error]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
