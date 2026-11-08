"""Sprint 14 UI checks: "Your data" on Profile (download my data, delete my account), demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint14_privacy_ui_test.py

Checks, at iPhone and desktop size, in light and dark:
  * the Profile page has no sideways scroll and no page errors,
  * "Download all my data" saves a JSON file that covers all 17 tables and
    holds the demo user's real records (accounts, transactions, ...),
  * the file is for download only: nothing is sent to the server,
  * "Delete my account" asks for the password and the word DELETE, does nothing
    in demo mode, and the new password rule says 12 characters.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
TABLES = ["accounts", "audit_log", "budgets", "categories", "category_rules", "goal_contributions",
          "goals", "investments", "labels", "net_worth_snapshots", "pending_imports",
          "recurring", "sync_status", "transaction_labels", "transactions", "user_consents", "user_prefs"]
results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def run(ctx, tag):
    page = ctx.new_page()
    errs, posts = [], []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("request", lambda r: posts.append(r.url) if r.method in ("POST", "PUT", "PATCH", "DELETE") else None)
    page.goto(BASE + "/profile", wait_until="networkidle")
    page.wait_for_timeout(600)

    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 1, f"{tag}: no sideways scrolling (overflow {over}px)")
    check(not errs, f"{tag}: no page errors ({errs[:1]})")
    check(page.locator("#dataCard").count() == 1, f"{tag}: the Your data card is there")
    check("12 characters" in page.inner_text("#pwForm") or "12 characters" in page.inner_text("body"),
          f"{tag}: password rule says 12 characters")
    check(page.get_attribute("#pw1", "minlength") == "12", f"{tag}: new-password field enforces 12")
    check(page.get_attribute("#delPw", "autocomplete") == "current-password",
          f"{tag}: delete asks for the current password")

    # Download
    before = len(posts)
    with page.expect_download(timeout=10000) as dl:
        page.click("#exportBtn")
    d = dl.value
    check(d.suggested_filename.startswith("money-tracker-export-") and d.suggested_filename.endswith(".json"),
          f"{tag}: file is named money-tracker-export-DATE.json ({d.suggested_filename})")
    data = json.load(open(d.path()))
    check(data.get("app") == "Money Tracker" and data.get("exported_at"), f"{tag}: file says what it is and when")
    check(sorted(data["tables"].keys()) == sorted(TABLES), f"{tag}: file covers all 17 tables")
    check(data["row_counts"]["transactions"] == len(data["tables"]["transactions"]) > 0,
          f"{tag}: transactions are in the file ({data['row_counts']['transactions']})")
    check(len(data["tables"]["accounts"]) > 0 and len(data["tables"]["goals"]) > 0,
          f"{tag}: accounts and goals are in the file")
    page.wait_for_timeout(300)
    check("Saved" in page.inner_text("#exportMsg"), f"{tag}: the page says how many records were saved")
    check(len(posts) == before, f"{tag}: download sent nothing to any server ({posts[before:]})")

    # Delete: wrong confirmation word, then demo-mode guard
    page.fill("#delPw", "whatever-password")
    page.fill("#delConfirm", "delete")
    page.click("#delSubmit")
    page.wait_for_timeout(300)
    check(page.url.endswith("/profile"), f"{tag}: lowercase 'delete' does not delete or navigate away")
    page.fill("#delConfirm", "DELETE")
    page.click("#delSubmit")
    page.wait_for_timeout(300)
    check(page.url.endswith("/profile"), f"{tag}: demo mode never deletes or signs out")
    page.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        for scheme in ("light", "dark"):
            phone = b.new_context(**p.devices["iPhone 13"], color_scheme=scheme,
                                  service_workers="block", accept_downloads=True)
            run(phone, f"phone {scheme}")
            phone.close()
            desk = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme,
                                 service_workers="block", accept_downloads=True)
            run(desk, f"desktop {scheme}")
            desk.close()
        b.close()
    passed = sum(1 for s, _ in results if s == "PASS")
    print(f"\nsprint14_privacy_ui_test: {passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)
    print("All Sprint 14 privacy UI checks passed.")


if __name__ == "__main__":
    main()
