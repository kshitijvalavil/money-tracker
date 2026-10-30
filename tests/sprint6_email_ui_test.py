"""Browser test for Sprint 6 email import changes, against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint6_email_ui_test.py

Email review now lives under Transactions ("From email" tab): no separate
sidebar item, Transactions shows the waiting count, imports show their channel
("Google Pay" for UPI, the bank otherwise), and approving one labels the new
transaction with that channel.
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
        pg.wait_for_timeout(500)
        nav = pg.evaluate("() => [...document.querySelectorAll('.nav a')].map(a => a.getAttribute('href'))")
        check("/imports" not in nav, f"no separate Email imports item in the sidebar ({nav})")
        check(pg.inner_text("#importsBadge") == "5" and pg.is_visible("#importsBadge"),
              "Transactions shows 5 waiting from email")

        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(600)
        check(pg.locator(".nav a[href='/transactions']").get_attribute("class") == "active",
              "Transactions is active on the email view")
        tab = pg.locator(".seg-nav a[href='/imports']")
        check(tab.get_attribute("aria-current") == "page", "From email tab is current")
        check(tab.locator("[data-imports-count]").inner_text() == "5", "From email tab shows the count")
        pills = pg.locator("#rows .c-source .pill").all_text_contents()
        check("Google Pay" in pills and "HDFC Bank" in pills, f"imports show their channel ({pills})")
        if SHOTS:
            pg.screenshot(path=os.path.join(SHOTS, "email-01.png"), full_page=True)

        # Approve the Google Pay one -> its transaction carries the label.
        row = pg.locator("#rows tr", has=pg.locator(".pill", has_text="Google Pay"))
        row.locator(".approve").click()
        pg.wait_for_timeout(900)
        check(pg.locator("#rows .pill", has_text="Google Pay").count() == 0, "approved import leaves the list")
        check(tab.locator("[data-imports-count]").inner_text() == "4", "tab count drops to 4")

        # Demo data resets on reload, so look the transaction up on this page
        # through the same client the page uses.
        got = pg.evaluate("""async () => {
            const { db } = await import('/static/js/app.js');
            const tx = (await db.from('transactions').select('id, note')).data
                .find(t => t.note === 'UPI transfer from Ravi');
            if (!tx) return null;
            const labels = (await db.from('labels').select('id, name')).data;
            const links = (await db.from('transaction_labels').select('transaction_id, label_id')).data;
            return links.filter(l => l.transaction_id === tx.id)
                        .map(l => labels.find(x => x.id === l.label_id).name);
        }""")
        check(got is not None, "approved import became a transaction")
        check(got == ["Google Pay"], f"transaction is labelled Google Pay ({got})")

        b.close()

    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
