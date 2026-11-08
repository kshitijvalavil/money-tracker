"""Sprint 14: the "we are updating" experience, in a real browser.

    BASE_URL is not used: this test starts its own demo server so it can stop it.
    python tests/sprint14_deploying_test.py

Checks:
  * /deploying looks right on phone and desktop (no sideways scroll, no emoji, readable),
  * while the server says it is still deploying the page keeps waiting; the moment it
    says it is done the page shows "Touchdown", the open button, and heads back,
  * a visitor who already has the app installed and then finds the server restarting
    (we stop it for real) sees the friendly page instead of a browser error, and the
    page takes them back into the app by itself once the server is up again.
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")
results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def start_server(port):
    env = dict(os.environ, DEMO_MODE="1")
    proc = subprocess.Popen([sys.executable, "-m", "gunicorn", "app:app", "--bind", f"127.0.0.1:{port}"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1); return proc
        except Exception:
            time.sleep(0.5)
    proc.kill(); raise SystemExit("server did not start")


def look(ctx, base, tag):
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    # Pretend the deploy is still running so the page stays in its waiting state.
    page.route("**/api/status", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"ok": true, "deploying": true}'))
    page.goto(base + "/deploying", wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(over <= 1, f"{tag}: no sideways scrolling ({over}px)")
    check(not errs, f"{tag}: no page errors ({errs[:1]})")
    text = page.inner_text("body")
    check("Captain Shipit" in text and "usually takes a minute" in text, f"{tag}: says an update is happening and roughly how long")
    check("safe" in text.lower(), f"{tag}: reassures that data is safe")
    check(EMOJI.search(text) is None, f"{tag}: no emoji")
    check(page.is_hidden("#open"), f"{tag}: no open button while still updating")
    page.close()


def main():
    port = free_port(); base = f"http://127.0.0.1:{port}"
    proc = start_server(port)
    try:
        with sync_playwright() as p:
            launch = {"args": ["--no-sandbox"]}
            if os.path.exists(CHROME):
                launch["executable_path"] = CHROME
            b = p.chromium.launch(**launch)

            for scheme in ("light", "dark"):
                c = b.new_context(**p.devices["iPhone 13"], color_scheme=scheme, service_workers="block")
                look(c, base, f"phone {scheme}"); c.close()
                c = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, service_workers="block")
                look(c, base, f"desktop {scheme}"); c.close()

            # Waits while deploying, then lands.
            c = b.new_context(viewport={"width": 1000, "height": 800}, service_workers="block")
            page = c.new_page()
            state = {"deploying": True}
            page.route("**/api/status", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body='{"ok": true, "deploying": %s}' % ("true" if state["deploying"] else "false")))
            page.goto(base + "/deploying", wait_until="domcontentloaded")
            page.wait_for_timeout(4200)
            check(page.get_attribute("#card", "data-state") == "deploying", "keeps waiting while the server says it is deploying")
            check(page.is_hidden("#open"), "no open button while deploying")
            state["deploying"] = False
            page.wait_for_selector("#card[data-state=landed]", timeout=8000)
            check("Touchdown" in page.inner_text("#title"), "shows Touchdown once the deploy is done")
            check(page.is_visible("#open"), "shows the Open Money Tracker button")
            check("start using the app" in page.inner_text("#chatter"), "tells the user they can start using the app")
            page.wait_for_url(base + "/", timeout=6000)
            check(page.url == base + "/", "heads back into the app by itself")
            c.close()

            # Real restart: installed visitor, server stops, friendly page, server returns.
            c = b.new_context(viewport={"width": 1000, "height": 800}, service_workers="allow")
            page = c.new_page()
            page.goto(base + "/", wait_until="networkidle")
            page.evaluate("navigator.serviceWorker.ready.then(() => true)")
            for _ in range(40):
                if page.evaluate("!!navigator.serviceWorker.controller"):
                    break
                page.wait_for_timeout(250)
                page.reload(wait_until="networkidle")
            check(page.evaluate("!!navigator.serviceWorker.controller"), "app is installed (service worker in control)")
            proc.terminate(); proc.wait(timeout=15)
            page.goto(base + "/transactions", wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            check("Captain Shipit" in body, "server stopped: the friendly updating page appears, not a browser error")
            check(page.is_hidden("#open"), "server stopped: still waiting, no open button")
            proc = start_server(port)
            page.wait_for_selector("#card[data-state=landed]", timeout=15000)
            check(True, "server back: the page notices and lands")
            for _ in range(48):
                try:
                    if "Captain Shipit" not in page.inner_text("body"):
                        break
                except Exception:
                    pass          # the page is reloading
                page.wait_for_timeout(250)
            page.wait_for_load_state("networkidle")
            check(page.url == base + "/transactions" and "Captain Shipit" not in page.inner_text("body"),
                  "server back: the app is back at the page they wanted")
            c.close()
            b.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
    passed = sum(1 for s, _ in results if s == "PASS")
    print(f"\nsprint14_deploying_test: {passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)
    print("All Sprint 14 deploying-page checks passed.")


if __name__ == "__main__":
    main()
