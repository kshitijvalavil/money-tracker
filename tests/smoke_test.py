"""Browser smoke test for Money Tracker (runs against demo mode).

Starts nothing itself — point it at a running server. Example:

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/smoke_test.py

It drives the real UI in a headless browser: loads the dashboard, adds a
transaction, adds an account, sets a budget, and checks both report charts
render — failing on any browser console/page error. Needs playwright and a
Chromium (set CHROME_PATH if not auto-found).
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")  # set to a dir to save screenshots

errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        pg = b.new_page(viewport={"width": 1100, "height": 1000})
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(600)
        check("₹" in pg.inner_text("#balance"), "dashboard balance renders")
        check(pg.locator("#accounts tr").count() >= 1, "dashboard lists accounts")
        check(pg.locator("#recent tr").count() >= 1, "dashboard lists recent transactions")
        check(pg.locator("#budgets .bar-row").count() >= 1, "dashboard shows budget bars")
        shot(pg, "01-dashboard.png")

        pg.goto(BASE + "/transactions", wait_until="networkidle")
        pg.wait_for_timeout(500)
        before = pg.locator("#rows tr").count()
        pg.fill("#amount", "777")
        pg.select_option("#category", label="Food")
        pg.fill("#note", "Smoke test lunch")
        pg.click("button[type=submit]")
        pg.wait_for_timeout(600)
        check(pg.locator("#rows tr").count() == before + 1, "transaction added")
        check("Smoke test lunch" in pg.inner_text("#rows"), "new transaction shows")
        shot(pg, "02-transactions.png")

        pg.goto(BASE + "/accounts", wait_until="networkidle")
        pg.wait_for_timeout(500)
        a_before = pg.locator("#rows tr").count()
        pg.fill("#name", "Test Wallet")
        pg.select_option("#type", "wallet")
        pg.fill("#opening", "500")
        pg.click("button[type=submit]")
        pg.wait_for_timeout(600)
        check(pg.locator("#rows tr").count() == a_before + 1, "account added")
        shot(pg, "03-accounts.png")

        pg.goto(BASE + "/budgets", wait_until="networkidle")
        pg.wait_for_timeout(500)
        pg.select_option("#category", label="Transport")
        pg.fill("#limit", "1500")
        pg.click("button[type=submit]")
        pg.wait_for_timeout(600)
        check("Transport" in pg.inner_text("#progress"), "budget set and shows")
        shot(pg, "04-budgets.png")

        check(pg.evaluate("() => typeof window.supabase !== 'undefined'"), "supabase-js loaded")

        pg.goto(BASE + "/reports", wait_until="networkidle")
        pg.wait_for_timeout(900)
        check(pg.evaluate("() => typeof window.Chart !== 'undefined'"), "Chart.js loaded")
        check(pg.evaluate("() => !!Chart.getChart('pie')"), "pie chart drawn")
        check(pg.evaluate("() => !!Chart.getChart('bars')"), "bar chart drawn")
        shot(pg, "05-reports.png")

        b.close()

    print("\n=== SMOKE TEST RESULTS ===")
    for status, msg in results:
        print(f"  [{status}] {msg}")
    if errors:
        print("\n=== BROWSER ERRORS ===")
        for e in errors:
            print("  " + e)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed; {len(errors)} browser errors")
    return 1 if (fails or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
