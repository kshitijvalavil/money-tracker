"""Goals, labels and insights browser test (Sprint 3) — demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint3_ui_test.py

Demo data lives in memory and resets on every page load. Covers: the Goals
page (rings render, create a goal, add money raises the ring, over-funding
clamps to 100% and shows "Reached", delete), labels on Transactions (chips
render, edit via the label dialog, duplicates collapse, clear), the Insights
page (month-over-month metrics, top merchants, spending by label), the
dashboard "left to spend" card, and no horizontal scroll at 1440 and 390 px.
Needs playwright + Chromium (CHROME_PATH, default /opt/pw-browsers/chromium).
"""
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


def text(page, sel):
    return page.text_content(sel) or ""


def run(pw):
    browser = pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
    page = browser.new_page()
    page.on("pageerror", lambda e: check(False, f"page error: {e}"))

    # ---- Goals page --------------------------------------------------------
    page.goto(f"{BASE}/goals", wait_until="networkidle")
    page.wait_for_timeout(500)
    rings = page.locator("#goals svg.ring").count()
    check(rings >= 3, f"goals page shows progress rings (got {rings})")
    first_pct = text(page, "#goals .ring-label")
    check("25%" in first_pct, f"first goal starts at 25% (got {first_pct})")

    # Create a new goal via the form.
    page.fill("#name", "QA Goal")
    page.fill("#target", "1000")
    page.click("#form button[type=submit]")
    page.wait_for_timeout(400)
    check("QA Goal" in text(page, "#goals"), "newly created goal appears")

    # Add money to the first goal (prompt dialog) -> ring rises.
    page.once("dialog", lambda d: d.accept("100000"))
    page.locator("#goals [data-add]").first.click()
    page.wait_for_timeout(500)
    raised = text(page, "#goals .ring-label")
    check("75%" in raised, f"adding money raises the ring to 75% (got {raised})")

    # Over-fund a goal (QA Goal target 1000) -> clamps to 100% + "Reached".
    qa = page.locator("#goals .goal-row", has_text="QA Goal")
    page.once("dialog", lambda d: d.accept("5000"))
    qa.locator("[data-add]").click()
    page.wait_for_timeout(500)
    qa = page.locator("#goals .goal-row", has_text="QA Goal")
    check("100%" in (qa.locator(".ring-label").text_content() or ""), "over-funded goal clamps to 100%")
    check("Reached" in (qa.text_content() or ""), "over-funded goal shows Reached")

    # Delete the QA goal (confirm dialog).
    page.once("dialog", lambda d: d.accept())
    qa.locator("[data-del]").click()
    page.wait_for_timeout(400)
    check("QA Goal" not in text(page, "#goals"), "deleted goal disappears")

    # ---- Labels on Transactions -------------------------------------------
    page.goto(f"{BASE}/transactions", wait_until="networkidle")
    page.wait_for_timeout(800)
    chips = page.locator("#rows .chip:not(.add)").count()
    check(chips >= 1, f"seeded labels render as chips (got {chips})")

    # Edit labels on the first row; duplicates collapse to one chip.
    page.once("dialog", lambda d: d.accept("QALabel, QALabel"))
    page.locator("#rows [data-labels]").first.click()
    page.wait_for_timeout(500)
    qacount = page.locator("#rows .chip", has_text="QALabel").count()
    check(qacount == 1, f"duplicate label collapses to one chip (got {qacount})")

    # Clear labels on that same row (empty input removes them).
    page.once("dialog", lambda d: d.accept(""))
    page.locator("#rows [data-labels]").first.click()
    page.wait_for_timeout(500)
    first_row_chips = page.locator("#rows tr").first.locator(".chip:not(.add)").count()
    check(first_row_chips == 0, f"clearing labels removes the chips (got {first_row_chips})")

    # ---- Insights page -----------------------------------------------------
    page.goto(f"{BASE}/insights", wait_until="networkidle")
    page.wait_for_timeout(600)
    metrics = page.locator("#mom .stat").count()
    check(metrics == 3, f"month-over-month shows income/expense/net (got {metrics})")
    check("Income" in text(page, "#mom"), "mom labels present")
    check(page.locator("#merchants ol.rank li").count() >= 1, "top merchants list populated")
    check(page.locator("#labels .bar-row").count() >= 1, "spending-by-label bars populated")
    check("Subscriptions" in text(page, "#labels"), "a known label shows in spending-by-label")

    # ---- Dashboard left-to-spend card -------------------------------------
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_timeout(600)
    check(page.locator("#ltsCard").get_attribute("hidden") is None, "left-to-spend card is shown")
    check("₹" in text(page, "#ltsValue"), "left-to-spend shows an amount")

    # ---- No horizontal scroll at desktop and phone widths -----------------
    for w, h in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": w, "height": h})
        for path in ("/goals", "/insights"):
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(300)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth > window.innerWidth + 1")
            check(not overflow, f"no horizontal scroll on {path} at {w}px")

    browser.close()


def main():
    with sync_playwright() as pw:
        run(pw)
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsprint3_ui_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All Sprint 3 UI checks passed.")


if __name__ == "__main__":
    main()
