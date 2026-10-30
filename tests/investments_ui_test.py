"""Browser test for the Investments page (Sprint 6), against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/investments_ui_test.py

Checks the summary, allocation chart and holdings table render from the demo
portfolio; adds, edits and deletes a holding; rejects bad input; escapes a
hostile name; and lays out without horizontal scroll at phone width.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")

errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def money_val(text):
    return float("".join(c for c in text if c.isdigit() or c == ".") or 0)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        pg = b.new_page(viewport={"width": 1100, "height": 1000})
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("dialog", lambda d: d.accept())

        pg.goto(BASE + "/investments", wait_until="networkidle")
        pg.wait_for_timeout(600)
        check(pg.locator(".nav a[href='/investments']").get_attribute("class") == "active",
              "Investments nav item is active")
        check(pg.locator("#holdingRows tr").count() == 5, "demo portfolio shows 5 holdings")
        check(pg.locator("#alloc").count() == 1, "allocation chart renders")
        value = money_val(pg.inner_text("#sumValue"))
        invested = money_val(pg.inner_text("#sumInvested"))
        check(value == 482600 and invested == 445000, f"summary totals ({value} / {invested})")
        check("pos" in (pg.locator("#sumGain span").get_attribute("class") or ""), "overall gain shown as positive")
        infy = pg.locator("#holdingRows tr", has_text="Infosys")
        check(infy.locator(".neg").count() == 1, "a holding in the red shows as a loss")
        check(pg.evaluate("() => /[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u.test(document.body.innerText)") is False,
              "no emojis on the page")
        shot(pg, "inv-01.png")

        # Add
        pg.fill("#name", "Bitcoin")
        pg.select_option("#kind", "crypto")
        pg.fill("#invested", "10000")
        pg.fill("#value", "12500")
        pg.click("#submitBtn")
        pg.wait_for_timeout(400)
        row = pg.locator("#holdingRows tr", has_text="Bitcoin")
        check(row.count() == 1, "added holding appears")
        check("+₹2,500" in row.inner_text().replace(" ", "") or "2,500" in row.inner_text(), "gain computed for new holding")
        check(pg.locator("#allocWrap").inner_text() == "" and pg.locator("#alloc").count() == 1,
              "allocation chart redrawn")

        # Edit
        row.locator("[data-edit]").click()
        check(pg.input_value("#name") == "Bitcoin" and pg.inner_text("#submitBtn") == "Save changes",
              "edit fills the form")
        pg.fill("#value", "8000")
        pg.click("#submitBtn")
        pg.wait_for_timeout(400)
        row = pg.locator("#holdingRows tr", has_text="Bitcoin")
        check(row.locator(".neg").count() == 1, "edited value now shows a loss")
        check(pg.locator("#holdingRows tr").count() == 6, "edit did not duplicate the row")
        check(pg.inner_text("#submitBtn") == "Add holding" and pg.is_hidden("#cancelEdit"), "form resets after save")

        # Hostile name is shown as text
        pg.fill("#name", "<img src=x onerror=alert(1)>")
        pg.fill("#invested", "1")
        pg.fill("#value", "1")
        pg.click("#submitBtn")
        pg.wait_for_timeout(400)
        check(pg.locator("#holdingRows img").count() == 0, "hostile name not rendered as HTML")
        check(pg.locator("#holdingRows tr", has_text="<img src=x").count() == 1, "hostile name shown as text")

        # Bad input
        n = pg.locator("#holdingRows tr").count()
        pg.fill("#name", "   ")
        pg.fill("#invested", "5")
        pg.fill("#value", "5")
        pg.click("#submitBtn")
        pg.wait_for_timeout(300)
        check(pg.locator("#holdingRows tr").count() == n, "blank name rejected")

        # Delete
        pg.locator("#holdingRows tr", has_text="Bitcoin").locator("[data-del]").click()
        pg.wait_for_timeout(400)
        check(pg.locator("#holdingRows tr", has_text="Bitcoin").count() == 0, "delete removes the holding")

        # Phone width
        m = b.new_page(viewport={"width": 390, "height": 844})
        m.on("pageerror", lambda e: errors.append(f"pageerror (mobile): {e}"))
        m.goto(BASE + "/investments", wait_until="networkidle")
        m.wait_for_timeout(500)
        overflow = m.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check(overflow <= 0, f"no horizontal page scroll at 390px ({overflow}px)")
        shot(m, "inv-02-mobile.png")
        b.close()

    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
