"""Phone layout checks (Sherlock Bugs' full sweep, 2 Nov), against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/phone_layout_test.py

On a phone, the Transactions history and the Dashboard's "Recent transactions"
were wide tables: the account and amount columns ran off the right edge. Each
row is now a small card. This checks, at iPhone size, that every page fits the
screen without sideways scrolling and that every transaction's amount is fully
on screen; and, at desktop size, that those two lists are still tables.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
PAGES = ["/", "/transactions", "/bills", "/imports", "/accounts", "/budgets",
         "/goals", "/investments", "/insights", "/profile"]

errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def amounts_on_screen(page, table_sel):
    return page.evaluate("""(sel) => {
        const rows = [...document.querySelectorAll(sel + " tr[data-tx]")];
        const off = rows.filter((r) => {
            const a = r.querySelector(".amt").getBoundingClientRect();
            return a.left < 0 || a.right > window.innerWidth;
        });
        return { rows: rows.length, off: off.length };
    }""", table_sel)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)

        phone = b.new_context(**p.devices["iPhone 13"], service_workers="block")
        for path in PAGES:
            page = phone.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_timeout(600)
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 1, f"phone {path}: no sideways scrolling (overflow {over}px)")
            if path in ("/", "/transactions"):
                sel = "#recent" if path == "/" else "#rows"
                page.wait_for_selector(sel + " tr[data-tx]")
                r = amounts_on_screen(page, sel)
                check(r["rows"] > 0 and r["off"] == 0,
                      f"phone {path}: all {r['rows']} amounts on screen ({r['off']} cut off)")
                disp = page.eval_on_selector(sel + " tr[data-tx]", "e => getComputedStyle(e).display")
                check(disp == "grid", f"phone {path}: rows shown as cards ({disp})")
            page.close()

        web = b.new_context(viewport={"width": 1366, "height": 860}, service_workers="block")
        for path, sel in (("/", "#recent"), ("/transactions", "#rows")):
            page = web.new_page()
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_selector(sel + " tr[data-tx]")
            disp = page.eval_on_selector(sel + " tr[data-tx]", "e => getComputedStyle(e).display")
            check(disp == "table-row", f"web {path}: still a table ({disp})")
            head = page.is_visible(sel.replace("#recent", ".tx-table").replace("#rows", "#txTable") + " thead th")
            check(head, f"web {path}: column headings visible")
            page.close()
        b.close()

    for e in errors:
        print("  [browser error]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
