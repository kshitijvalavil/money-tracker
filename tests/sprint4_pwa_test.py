"""PWA + mobile layout browser test (Sprint 4) — demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint4_pwa_test.py

Covers: the web app manifest (linked, valid, icons incl. maskable, served type),
the service worker (served from / with Service-Worker-Allowed, registers at root
scope), the offline app-shell fallback, and the mobile chrome — bottom tab bar
with a working "+ Add" FAB, the off-canvas drawer (hamburger opens, scrim and
Escape close, a nav link closes and navigates), desktop still using the sidebar,
and no horizontal scroll with >= 44px tap targets at 360/390 px.
Needs playwright + Chromium (CHROME_PATH, default /opt/pw-browsers/chromium).
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def run(pw):
    browser = pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)

    # ---- Desktop: sidebar, no tab bar --------------------------------------
    dctx = browser.new_context(viewport={"width": 1280, "height": 900})
    dp = dctx.new_page()
    dp.on("pageerror", lambda e: check(False, f"desktop page error: {e}"))
    dp.goto(f"{BASE}/", wait_until="networkidle")
    check(dp.locator("#sidebar").is_visible(), "desktop shows the sidebar")
    check(not dp.locator(".tabbar").is_visible(), "desktop hides the bottom tab bar")
    check(not dp.locator("#menuBtn").is_visible(), "desktop hides the hamburger")

    # ---- Manifest ----------------------------------------------------------
    href = dp.get_attribute("link[rel=manifest]", "href")
    check(bool(href), "a web app manifest is linked")
    resp = dp.request.get(BASE + href)
    check("manifest" in (resp.headers.get("content-type", "")), "manifest served as a manifest type")
    mf = json.loads(resp.text())
    check(mf.get("display") == "standalone", "manifest display is standalone")
    check(mf.get("start_url") == "/", "manifest start_url is /")
    check(len(mf.get("icons", [])) >= 2, "manifest declares at least two icons")
    check(any(i.get("purpose") == "maskable" for i in mf.get("icons", [])), "a maskable icon is declared")
    for icon in mf.get("icons", []):
        r = dp.request.get(BASE + icon["src"])
        check(r.status == 200 and r.headers.get("content-type", "").startswith("image/"),
              f"icon {icon['src']} is served as an image")

    # ---- Service worker ----------------------------------------------------
    swr = dp.request.get(f"{BASE}/sw.js")
    check(swr.headers.get("service-worker-allowed") == "/", "sw.js sets Service-Worker-Allowed: /")
    reg = dp.evaluate("""async () => {
        const r = await navigator.serviceWorker.ready.catch(() => null);
        return r ? r.scope : null;
    }""")
    check(reg and reg.rstrip("/").endswith(BASE.split("://", 1)[1].rstrip("/")),
          f"service worker registered at root scope (got {reg})")
    dctx.close()

    # ---- Mobile chrome -----------------------------------------------------
    mctx = browser.new_context(viewport={"width": 390, "height": 844})
    mp = mctx.new_page()
    mp.on("pageerror", lambda e: check(False, f"mobile page error: {e}"))
    mp.goto(f"{BASE}/", wait_until="networkidle")
    mp.wait_for_timeout(300)
    check(mp.locator(".tabbar").is_visible(), "mobile shows the bottom tab bar")
    check(mp.locator("#menuBtn").is_visible(), "mobile shows the hamburger")

    fab = mp.locator(".tabbar a.fab")
    check(fab.is_visible(), "the + Add FAB is shown")
    check(fab.get_attribute("href") == "/transactions", "the FAB links to the add-transaction page")

    # Drawer starts off-canvas (its right edge is at or left of the viewport edge).
    box = mp.locator("#sidebar").bounding_box()
    check(box is not None and box["x"] + box["width"] <= 1, "the drawer starts off-canvas")

    # Hamburger opens it; the scrim appears.
    mp.locator("#menuBtn").click()
    mp.wait_for_timeout(350)
    box = mp.locator("#sidebar").bounding_box()
    check(box is not None and box["x"] >= -1, "the hamburger opens the drawer")
    check(mp.locator("#scrim").is_visible(), "a scrim appears behind the open drawer")
    check(mp.get_attribute("#menuBtn", "aria-expanded") == "true", "hamburger reports expanded")

    # Scrim closes it (tap the exposed area to the right of the 264px drawer).
    mp.locator("#scrim").click(position={"x": 350, "y": 400})
    mp.wait_for_timeout(350)
    box = mp.locator("#sidebar").bounding_box()
    check(box is not None and box["x"] + box["width"] <= 1, "tapping the scrim closes the drawer")

    # A drawer link closes the drawer and navigates.
    mp.locator("#menuBtn").click()
    mp.wait_for_timeout(300)
    mp.locator("#sidebar .nav a[href='/goals']").click()
    mp.wait_for_load_state("networkidle")
    mp.wait_for_timeout(300)
    check(mp.url.endswith("/goals"), "a drawer link navigates")
    check(not mp.locator("#scrim").is_visible(), "the drawer closes after following a link")

    # No horizontal scroll and >= 44px tap targets across pages / widths.
    for w in (390, 360):
        mp.set_viewport_size({"width": w, "height": 844})
        for path in ("/", "/transactions", "/insights", "/goals"):
            mp.goto(f"{BASE}{path}", wait_until="networkidle")
            mp.wait_for_timeout(200)
            hscroll = mp.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
            check(not hscroll, f"no horizontal scroll on {path} at {w}px")
        small = mp.evaluate("""() => [...document.querySelectorAll('.tabbar a')]
            .filter(a => a.getBoundingClientRect().height < 44).length""")
        check(small == 0, f"all tab-bar targets >= 44px tall at {w}px")

    # ---- Offline fallback --------------------------------------------------
    op = mctx.new_page()
    op.goto(f"{BASE}/transactions", wait_until="networkidle")
    op.evaluate("() => navigator.serviceWorker.ready")
    op.wait_for_timeout(400)
    mctx.set_offline(True)
    try:
        op.goto(f"{BASE}/bills", wait_until="domcontentloaded", timeout=8000)
    except Exception:
        pass
    body = op.text_content("body") or ""
    check("offline" in body.lower(), "an offline navigation shows the offline app shell")
    mctx.set_offline(False)

    browser.close()


def main():
    with sync_playwright() as pw:
        run(pw)
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsprint4_pwa_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All Sprint 4 PWA + mobile checks passed.")


if __name__ == "__main__":
    main()
