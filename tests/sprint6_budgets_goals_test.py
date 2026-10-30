"""Browser test for the Sprint 6 Budgets and Goals polish, against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint6_budgets_goals_test.py

Budgets: left-to-spend summary, per-category status, most-urgent-first order,
pace marker only on the current month, month stepping, edit and remove.
Goals: summary strip and the "save X a month" plan line.
"""
import os
import re
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

        # ---- Budgets ---------------------------------------------------------
        pg.goto(BASE + "/budgets", wait_until="networkidle")
        pg.wait_for_timeout(600)
        check(pg.is_visible("#summary"), "left-to-spend summary shows")
        check("₹" in pg.inner_text("#sumLeft"), "summary has an amount")
        check(re.search(r"spent of", pg.inner_text("#sumOf")) is not None, "summary says spent of total")
        rows = pg.locator("#progress .budget-row")
        n = rows.count()
        check(n >= 2, f"budget rows render ({n})")
        check(all(rows.nth(i).locator(".pill").count() == 1 for i in range(n)), "every row has a status pill")
        used = pg.evaluate("""() => [...document.querySelectorAll('#progress .budget-row')].map(r => {
            const w = parseFloat(r.querySelector('.bar > div').style.width); return w; })""")
        check(used == sorted(used, reverse=True), f"most-used category first ({used})")
        check(pg.locator("#progress .pace").count() == n and pg.is_visible("#sumPace"),
              "pace marker on the current month")
        check(pg.is_disabled("#monthNext"), "next month disabled on the current month")
        check(all(("left" in t or "over" in t) for t in pg.locator("#progress .budget-left").all_inner_texts()),
              "each row says left or over")
        shot(pg, "bg-01.png")

        # Edit prefills the form and switches the button label.
        first = rows.first
        name = first.locator(".budget-name").inner_text().split("\n")[0].strip()
        first.locator("[data-edit]").click()
        check(pg.inner_text("#saveBtn") == "Update limit", "edit switches button to Update limit")
        check(float(pg.input_value("#limit")) > 0, "edit prefills the limit")
        pg.fill("#limit", "99999")
        pg.click("#saveBtn")
        pg.wait_for_timeout(400)
        row = pg.locator("#progress .budget-row", has_text=name)
        check("99,999" in row.inner_text(), "updated limit shows")
        check(pg.locator("#progress .budget-row").count() == n, "update did not add a row")

        # Previous month: no pace marker, next enabled.
        pg.click("#monthPrev")
        pg.wait_for_timeout(400)
        check(pg.locator("#progress .pace").count() == 0 and pg.is_hidden("#sumPace"),
              "no pace marker on a past month")
        check(not pg.is_disabled("#monthNext"), "next enabled on a past month")
        pg.click("#monthNext")
        pg.wait_for_timeout(400)
        check(pg.is_disabled("#monthNext"), "back on the current month")

        # Remove.
        pg.locator("#progress .budget-row", has_text=name).locator("[data-del]").click()
        pg.wait_for_timeout(400)
        check(pg.locator("#progress .budget-row").count() == n - 1, "remove deletes the limit")

        # Phone width: no sideways scroll.
        m = b.new_page(viewport={"width": 390, "height": 844})
        m.on("pageerror", lambda e: errors.append(f"pageerror (mobile): {e}"))
        for path in ("/budgets", "/goals"):
            m.goto(BASE + path, wait_until="networkidle")
            m.wait_for_timeout(400)
            over = m.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f"{path}: no horizontal scroll at 390px ({over}px)")
            shot(m, f"bg-m{path.replace('/', '-')}.png")

        # ---- Goals -----------------------------------------------------------
        pg.goto(BASE + "/goals", wait_until="networkidle")
        pg.wait_for_timeout(500)
        check(pg.is_visible("#summary"), "goals summary shows")
        check("₹" in pg.inner_text("#sumSaved") and "of 3" in pg.inner_text("#sumDone"),
              "summary has saved total and reached count")
        japan = pg.locator("#goals .goal-row", has_text="Japan trip")
        txt = japan.inner_text()
        check(re.search(r"Save ₹[\d,.]+ a month", txt) is not None, f"dated goal shows a monthly plan ({txt!r})")
        check("months left" in txt or "month left" in txt, "dated goal shows time left")
        undated = pg.locator("#goals .goal-row", has_text="New laptop").inner_text()
        check("to go" in undated, "undated goal shows amount to go")
        check(pg.evaluate("() => /[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u.test(document.body.innerText)") is False,
              "no emojis on Goals")
        check(pg.is_visible("#cutPlan"), "spend-cut analysis shows for dated goals")
        check(pg.locator("#cutRows .cut-row").count() >= 1, "cut plan lists spending categories")
        check(re.search(r"need about ₹[\d,.]+ a month", pg.inner_text("#cutIntro")) is not None,
              "cut plan states the monthly need")
        check("a month" in pg.inner_text("#cutTotal"), "cut plan totals what trimming frees")
        names = pg.locator("#cutRows .cut-name").all_inner_texts()
        check(not any(n.lower() in ("rent", "bills") for n in names), f"fixed costs never suggested ({names})")
        shot(pg, "bg-02-goals.png")
        b.close()

    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
