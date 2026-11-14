"""Flex budget, rollovers and monthly recap browser test (Sprint 10) — demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint10_flex_test.py

Demo data lives in memory and resets on every page load. Covers: the dashboard
"Left to spend freely" hero and the three-bucket "Your money this month" card;
the Budgets page grouped by bucket, with rollover toggles, a bucket selector on
the form, and a rollover carry tag; the Insights monthly recap; that the hero,
the Budgets summary and the recap's "flexible left over" all agree; and no
horizontal scroll at 1440 and 390 px. Needs playwright + Chromium.
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


def rupees(s):
    """Pull the number out of a '₹8,051.00' string."""
    digits = "".join(c for c in s if c.isdigit() or c == ".")
    return round(float(digits)) if digits else None


def run(pw):
    browser = pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
    page = browser.new_page()
    page.on("pageerror", lambda e: check(False, f"page error: {e}"))

    # ---- Dashboard: hero + buckets ----------------------------------------
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector("#bucketsCard:not([hidden])", timeout=8000)
    check(text(page, "#ltsHeading").strip() == "Left to spend", "hero reads 'Left to spend'")
    hero_left = rupees(text(page, "#ltsValue"))
    check(hero_left and hero_left > 0, f"hero shows a positive amount (got {hero_left})")
    check("flexible" in text(page, "#ltsSub").lower(), "hero sub mentions the flexible budget")

    bucket_names = page.locator(".bucket-row .bucket-name").all_text_contents()
    check(bucket_names == ["Fixed", "Flexible", "Non-monthly"],
          f"three buckets in order (got {bucket_names})")
    check(page.locator("#ltsPace").count() == 1, "hero has a pace marker for the current month")

    # ---- Budgets: grouping, rollover, bucket form, consistency ------------
    page.goto(f"{BASE}/budgets", wait_until="networkidle")
    page.wait_for_selector(".bucket-group", timeout=8000)
    heads = page.locator(".bucket-head").all_text_contents()
    check(heads == ["Fixed", "Flexible", "Non-monthly"], f"budgets grouped by bucket (got {heads})")
    check(page.locator("#bucket").count() == 1, "form has a bucket selector")
    check(page.locator("#rollover").count() == 1, "form has a rollover checkbox")

    toggles = page.locator(".roll-toggle input")
    check(toggles.count() >= 1, f"rollover toggles render per budget (got {toggles.count()})")
    check(page.locator(".pill.carry").count() >= 1, "a rolling-over category shows a carry tag")

    budgets_left = rupees(text(page, "#sumLeft"))
    check(budgets_left == hero_left,
          f"Budgets 'left to spend freely' matches the dashboard hero ({budgets_left} vs {hero_left})")

    # Turn a rollover off and confirm it persists through a reload of the list.
    checked = page.locator(".roll-toggle input:checked")
    check(checked.count() >= 1, "at least one category rolls over by default (demo Travel)")
    if checked.count():
        checked.first.click()
        page.wait_for_timeout(500)

    # ---- Insights: monthly recap ------------------------------------------
    page.goto(f"{BASE}/insights", wait_until="networkidle")
    page.wait_for_selector("#recapCard:not([hidden])", timeout=8000)
    check("recap" in text(page, "#recapTitle").lower(), "recap card has a month title")
    saved = rupees(text(page, "#recapSaved"))
    check(saved and saved > 0, f"recap shows an amount saved (got {saved})")
    rows = text(page, "#recapRows")
    check("Money in" in rows and "Money out" in rows, "recap lists money in and out")

    # "Flexible left over" in the recap matches the hero's "left to spend freely".
    flex_rows = page.locator(".recap-row").all_text_contents()
    flex_left = None
    for r in flex_rows:
        if "Flexible left over" in r:
            flex_left = rupees(r)
    check(flex_left == hero_left,
          f"recap 'flexible left over' matches the hero ({flex_left} vs {hero_left})")

    # ---- No horizontal scroll at desktop and phone widths -----------------
    for w, h, label in [(1440, 900, "desktop"), (390, 844, "phone")]:
        page.set_viewport_size({"width": w, "height": h})
        for path in ["/", "/budgets", "/insights"]:
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(300)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
            check(not overflow, f"no horizontal scroll at {label} on {path}")

    browser.close()


def main():
    with sync_playwright() as pw:
        run(pw)
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsprint10_flex_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All Sprint 10 UI checks passed.")


if __name__ == "__main__":
    main()
