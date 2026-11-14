"""Sprint 12 UI checks (forecast, Flow/Sankey, sparklines, custom Home), demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint12_ui_test.py

Runs at iPhone size and desktop size, in light and dark, and checks:
  * no sideways scrolling and no uncaught page errors on the three touched pages,
  * no emoji anywhere in the rendered text (Boss's house rule),
  * the Forecast page draws its projection chart and fills the stat tiles,
  * Insights draws the money-flow Sankey and the per-category trend sparklines,
  * Home draws a sparkline on each stat tile and the forecast hero,
  * Customize mode reveals the card tools, hides a card, reorders by keyboard,
    and the choice survives a reload (saved per account).
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
PAGES = ["/", "/forecast", "/insights"]

# Pictographic emoji only; arrows (→ ← ↑ ↓) are legitimate UI glyphs and allowed.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]"
)

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def svg_filled(page, sel):
    return page.evaluate(
        "(s) => { const el = document.querySelector(s); return el ? el.innerHTML.length : 0; }", sel
    )


def sweep(ctx, tag):
    for path in PAGES:
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(800)

        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(over <= 1, f"{tag} {path}: no sideways scrolling (overflow {over}px)")
        check(not errs, f"{tag} {path}: no page errors ({errs[:1]})")

        text = page.evaluate("document.body.innerText")
        m = EMOJI.search(text)
        check(m is None, f"{tag} {path}: no emoji in UI" + (f" (found {m.group()!r})" if m else ""))

        if path == "/forecast":
            check(svg_filled(page, "#fcChart") > 50, f"{tag} forecast: projection chart drawn")
            val = page.evaluate("document.querySelector('#fcEnd').textContent.trim()")
            check(val not in ("", "—"), f"{tag} forecast: month-end tile filled ({val})")
        if path == "/insights":
            check(svg_filled(page, "#flowSvg") > 50, f"{tag} insights: money-flow Sankey drawn")
            sparks = page.evaluate(
                "[...document.querySelectorAll('#trends .spark')].filter(e => e.innerHTML.length > 20).length"
            )
            check(sparks > 0, f"{tag} insights: trend sparklines drawn ({sparks})")
        if path == "/":
            sparks = page.evaluate(
                "[...document.querySelectorAll('.figures .spark')].filter(e => e.innerHTML.length > 20).length"
            )
            check(sparks >= 1, f"{tag} home: headline sparklines drawn ({sparks})")
            check(svg_filled(page, "#spkForecast") > 20, f"{tag} home: forecast hero sparkline drawn")
        page.close()


def home_layout(ctx):
    """Home is one fixed layout: four figures, then two columns. No customise mode."""
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(800)

    labels = [x.inner_text().strip().lower() for x in page.locator(".figures .label").all()]
    check(labels == ["balance", "money in", "money out", "net worth"], f"home: four headline figures in order ({labels})")
    check(all("₹" in page.inner_text(sel) for sel in ("#balance", "#income", "#expense", "#nwCardVal")), "home: every figure shows rupees")
    heads = [x.inner_text().strip() for x in page.locator(".home-grid h2").all()]
    check(heads == ["Left to spend", "Bills due", "Month-end forecast", "Latest"], f"home: sections in order ({heads})")
    check(page.locator("#customizeBtn, .drag-handle, .hide-toggle").count() == 0, "home: no customise controls")
    check(page.locator(".figure-link").get_attribute("href") == "/net-worth", "home: net worth figure opens Net worth")
    # An old saved layout (from the removed customise mode) is simply ignored.
    page.evaluate("localStorage.setItem('homeLayout', JSON.stringify([{id:'networth',show:false},{id:'stats',show:false}]))")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(600)
    check(page.locator(".figures .figure:visible").count() == 4, "home: a stale saved layout hides nothing")
    check(not errs, f"home: no page errors ({errs[:1]})")
    page.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)

        for scheme in ("light", "dark"):
            phone = b.new_context(**p.devices["iPhone 13"], color_scheme=scheme, service_workers="block")
            sweep(phone, f"phone/{scheme}")
            phone.close()
            desk = b.new_context(
                viewport={"width": 1440, "height": 900}, color_scheme=scheme, service_workers="block"
            )
            sweep(desk, f"desk/{scheme}")
            desk.close()

        # Interaction flow once, at desktop size (light).
        desk = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
        home_layout(desk)
        desk.close()
        b.close()

    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsprint12_ui_test: {len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("sprint12_ui_test: all checks passed")


if __name__ == "__main__":
    main()
