"""Sprint 6 hotfix checks, against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint6_hotfix_test.py

1. Stale design after a deploy: the service worker served CSS/JS cache-first
   under a cache name that never changed, so returning browsers kept the old
   release's stylesheet. /sw.js now carries a cache name stamped with a hash
   of the static files, and that hash changes when any static file changes.
2. Adding a bill from the web app: Transactions has an "Add a bill" button
   that opens the bill form directly.
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")

errors, results = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def version_changes_with_static_files():
    import app as appmod
    real = appmod.app.static_folder
    tmp = tempfile.mkdtemp()
    try:
        copy = os.path.join(tmp, "static")
        shutil.copytree(real, copy)
        appmod.app.static_folder = copy
        v1 = appmod._asset_version()
        with open(os.path.join(copy, "css", "style.css"), "a") as f:
            f.write("\n/* changed */\n")
        v2 = appmod._asset_version()
        check(v1 == appmod.ASSET_VERSION, "version is a pure function of the static files")
        check(v1 != v2, "changing style.css changes the cache version")
    finally:
        appmod.app.static_folder = real
        shutil.rmtree(tmp)


def main():
    version_changes_with_static_files()
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        pg = b.new_page(viewport={"width": 1100, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        sw = pg.request.get(f"{BASE}/sw.js")
        body = sw.text()
        m = re.search(r'const CACHE = "money-tracker-([0-9a-f]{12})"', body)
        check(m is not None, "sw.js cache name is stamped with a content hash")
        check('"money-tracker-v1"' not in body, "the old fixed cache name is gone")
        check(sw.headers.get("cache-control") == "no-cache", "sw.js is never cached by the browser")
        check("javascript" in (sw.headers.get("content-type") or ""), "sw.js served as JavaScript")

        # The page registers the worker and the worker controls it.
        pg.goto(BASE + "/transactions", wait_until="networkidle")
        pg.evaluate("async () => { await navigator.serviceWorker.ready; }")
        pg.wait_for_timeout(500)
        keys = pg.evaluate("async () => await caches.keys()")
        check(m and f"money-tracker-{m.group(1)}" in keys, f"browser cache uses the stamped name ({keys})")

        # Add a bill from Transactions.
        link = pg.locator("#addBillLink")
        check(link.is_visible() and "Add a bill" in link.inner_text(), "Transactions shows an Add a bill button")
        link.click()
        pg.wait_for_url(re.compile(r"/bills#add$"))
        pg.wait_for_timeout(1200)
        check(pg.inner_text("#formTitle") == "Add a bill", "lands on the Add a bill form")
        check(pg.evaluate("() => document.activeElement && document.activeElement.id") == "fName",
              "bill name field is focused, ready to type")
        check(pg.locator(".seg-nav a[href='/bills']").get_attribute("aria-current") == "page",
              "Bills & subscriptions tab is current")
        b.close()

    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
