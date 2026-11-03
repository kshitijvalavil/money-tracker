"""Browser test for Sprint 11 (net worth, Wealth, bills calendar, goals
forecast), against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint11_networth_test.py

Net worth: hero = assets − liabilities, a 12-month trend, and a breakdown of
what you own and owe. Wealth: invested/current/gain, allocation donut, holdings
with a link to manage them. Bills calendar: a month grid with due bills and a
30-day upcoming list. Goals: the new On track / Behind forecast pill. Every
page fits 1440 and 390 px with no sideways scroll and shows no emoji.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")

EMOJI = "() => /[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u.test(document.body.innerText)"

errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        # ---- Net worth -------------------------------------------------------
        pg.goto(BASE + "/net-worth", wait_until="networkidle")
        pg.wait_for_timeout(700)
        check("₹" in pg.inner_text("#nwValue"), "net worth hero shows an amount")
        check("₹" in pg.inner_text("#nwAssets"), "assets total shows")
        check("₹" in pg.inner_text("#nwLiab"), "liabilities total shows")
        check(pg.locator("#assets .nw-row").count() >= 1, "what-you-own lists at least one row")
        check(pg.locator("#liabs .nw-row").count() >= 1, "what-you-owe lists the credit card")
        check(pg.locator("#trend").count() == 1 and not pg.is_visible("#trendEmpty"),
              "12-month trend chart renders")
        check("₹" in pg.inner_text("#nwChange"), "change-this-year shows")
        check(pg.evaluate(EMOJI) is False, "no emojis on Net worth")
        shot(pg, "s11-networth.png")

        # ---- Wealth ----------------------------------------------------------
        pg.goto(BASE + "/wealth", wait_until="networkidle")
        pg.wait_for_timeout(700)
        check("₹" in pg.inner_text("#sumValue"), "wealth current value shows")
        check("₹" in pg.inner_text("#sumInvested"), "invested total shows")
        check(pg.locator("#alloc").count() == 1, "allocation donut renders")
        check(pg.locator("#holdings .wh-row").count() >= 1, "holdings list populated")
        check(pg.locator("#holdings a[href='/investments'], .card-head a[href='/investments']").count() >= 1,
              "a link to manage investments is present")
        check(pg.evaluate(EMOJI) is False, "no emojis on Wealth")
        shot(pg, "s11-wealth.png")

        # ---- Bills calendar --------------------------------------------------
        pg.goto(BASE + "/calendar", wait_until="networkidle")
        pg.wait_for_timeout(700)
        check(pg.locator("#grid .cal-cell").count() >= 28, "a month grid of day cells renders")
        check(pg.locator("#grid .cal-bill").count() >= 1, "at least one bill shows on the grid")
        check(pg.locator("#upcoming .cal-up").count() >= 1, "the 30-day upcoming list is populated")
        check("₹" in pg.inner_text("#monthTotal") or "No bills" in pg.inner_text("#monthTotal"),
              "month total line shows")
        check(pg.evaluate(EMOJI) is False, "no emojis on Bills calendar")
        shot(pg, "s11-calendar.png")

        # ---- Goals forecast --------------------------------------------------
        pg.goto(BASE + "/goals", wait_until="networkidle")
        pg.wait_for_timeout(600)
        japan = pg.locator("#goals .goal-row", has_text="Japan trip")
        jtxt = japan.inner_text()
        check("Save ₹" in jtxt and "a month" in jtxt, "dated goal keeps its monthly plan line")
        check(japan.locator(".goal-forecast").count() == 1, "dated goal shows a forecast line")
        check(("On track" in jtxt) or ("Behind" in jtxt), "forecast shows an On track / Behind pill")
        check("Saving ₹" in jtxt, "forecast states the saving pace")
        check(pg.evaluate(EMOJI) is False, "no emojis on Goals")
        shot(pg, "s11-goals.png")

        # ---- No sideways scroll on phone and desktop -------------------------
        for w, h in ((1440, 1000), (390, 844)):
            pg.set_viewport_size({"width": w, "height": h})
            for path in ("/net-worth", "/wealth", "/calendar", "/goals"):
                pg.goto(BASE + path, wait_until="networkidle")
                pg.wait_for_timeout(400)
                over = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                check(over <= 1, f"{path}: no horizontal scroll at {w}px ({over}px)")
        b.close()

    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
