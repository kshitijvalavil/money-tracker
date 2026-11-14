"""Sprint 9 review list ("To review"), Needs-you strip and Rules, on desktop and phone.

Runs against the app in DEMO_MODE (in-memory data, reset on every page load).
    BASE_URL=http://127.0.0.1:8099 python tests/sprint9_review_inbox_test.py
Optional: SHOTS=/some/dir saves screenshots.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099").rstrip("/")
SHOTS = os.environ.get("SHOTS")
CHROME = "/opt/pw-browsers/chromium"
results, errors = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    print(("  [PASS] " if cond else "  [FAIL] ") + msg)


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def watch(pg):
    pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))


TX_COUNT = """async (note) => {
  const { db } = await import('/static/js/app.js');
  return (await db.from('transactions').select('id, note')).data.filter(t => t.note === note).length;
}"""


def open_list(pg):
    pg.goto(BASE + "/imports", wait_until="networkidle")
    pg.wait_for_selector("#list .rv-item[data-id]")
    pg.wait_for_timeout(300)


def desktop(b):
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    watch(pg)
    open_list(pg)
    items = pg.locator("#list .rv-item[data-id]")
    check(items.count() == 6, f"6 items to review ({items.count()})")
    check(pg.locator(".seg-nav a[href='/imports']").inner_text().startswith("To review"), "tab is called To review")
    swiggy = pg.locator("#list .rv-item", has_text="Swiggy")
    check(swiggy.locator(".pill.merge").text_content().strip() == "3 emails merged", "Swiggy shows 3 emails merged")
    check(swiggy.locator(".sel-cat option:checked").inner_text().startswith("Food"), "suggested category preselected")
    check("Not synced yet" in pg.inner_text("#syncLine") or "Last synced" in pg.inner_text("#syncLine"),
          "sync line shown")
    shot(pg, "s9-desktop-list.png")

    # Accept -> leaves the list, transaction made; Undo -> back, transaction gone.
    pg.locator("#list .rv-item", has_text="Zomato").locator(".act-accept").click()
    pg.wait_for_timeout(700)
    check(pg.locator("#list .rv-item", has_text="Zomato").count() == 0, "accepted item leaves the list")
    check(pg.is_visible("#undoBar"), "undo bar appears")
    check(pg.evaluate(TX_COUNT, "Zomato order") == 1, "accept makes one transaction")
    pg.click("#undoBtn")
    pg.wait_for_timeout(900)
    check(pg.locator("#list .rv-item", has_text="Zomato").count() == 1, "undo brings the item back")
    check(pg.evaluate(TX_COUNT, "Zomato order") == 0, "undo removes the transaction")

    # Ignore -> gone, no transaction; Ctrl+Z restores it.
    pg.locator("#list .rv-item", has_text="Uber").locator(".act-ignore").click()
    pg.wait_for_timeout(700)
    check(pg.locator("#list .rv-item", has_text="Uber").count() == 0, "ignored item leaves the list")
    check(pg.evaluate(TX_COUNT, "Uber trip") == 0, "ignore makes no transaction")
    pg.keyboard.press("Control+z")
    pg.wait_for_timeout(900)
    check(pg.locator("#list .rv-item", has_text="Uber").count() == 1, "Ctrl+Z brings it back")

    # Fix sheet: pick a category, save and accept.
    pg.locator("#list .rv-item", has_text="Uber").locator(".act-fix").click()
    pg.wait_for_selector("#fixSheet[open]")
    chips = pg.locator("#fixCats input[type=radio]")
    check(chips.count() >= 3, f"fix sheet lists categories ({chips.count()})")
    pg.locator("#fixCats label", has_text="Transport").first.click()
    shot(pg, "s9-desktop-fix.png")
    pg.click("#fixSave")
    pg.wait_for_timeout(900)
    check(not pg.is_visible("#fixSheet"), "fix sheet closes")
    check(pg.locator("#list .rv-item", has_text="Uber").count() == 0, "fixed item accepted")
    check(pg.evaluate(TX_COUNT, "Uber trip") == 1, "fix makes one transaction")

    # Keyboard: J moves, A accepts.
    before = items.count()
    pg.locator("#list .rv-item[data-id]").first.focus()
    pg.keyboard.press("j")
    focused = pg.evaluate("document.activeElement?.closest('.rv-item')?.dataset.id")
    check(bool(focused), "J moves focus to an item")
    pg.keyboard.press("a")
    pg.wait_for_timeout(800)
    check(items.count() == before - 1, "A accepts the focused item")

    # Bulk select -> Accept selected.
    before = items.count()
    pg.locator("#list .rv-check").nth(0).check()
    pg.locator("#list .rv-check").nth(1).check()
    check(pg.is_visible("#acceptSel"), "Accept selected appears")
    pg.click("#acceptSel")
    pg.wait_for_timeout(1200)
    check(items.count() == before - 2, f"bulk accept takes two ({before} -> {items.count()})")

    # Needs-you strip on Home.
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_timeout(800)
    check(pg.is_visible("#needs"), "Needs-you row on desktop Home")
    check("to review" in pg.inner_text("#needs"), "Needs-you shows items to review")
    shot(pg, "s9-desktop-home.png")

    # Rules on Profile.
    pg.goto(BASE + "/profile", wait_until="networkidle")
    pg.wait_for_selector("#ruleList .rule-item")
    n = pg.locator("#ruleList .rule-item").count()
    pg.fill("#ruleText", "blinkit")
    pg.click("#ruleAdd")
    pg.wait_for_timeout(700)
    check(pg.locator("#ruleList .rule-item").count() == n + 1, "rule added")
    pg.fill("#ruleText", "BlinKit")
    pg.click("#ruleAdd")
    pg.wait_for_timeout(700)
    check(pg.locator("#ruleList .rule-item").count() == n + 1, "duplicate rule refused")
    pg.locator("#ruleList .rule-item", has_text="blinkit").locator(".rule-del").click()
    pg.wait_for_timeout(700)
    check(pg.locator("#ruleList .rule-item").count() == n, "rule deleted")
    pg.close()


def phone(b):
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    watch(pg)
    open_list(pg)
    over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check(over <= 0, f"no sideways scroll at 390 ({over}px)")
    small = pg.evaluate("""() => [...document.querySelectorAll('#list .rv-item[data-id] button')]
        .filter(b => b.offsetParent).map(b => b.getBoundingClientRect())
        .filter(r => r.height < 44 || r.width < 44).length""")
    check(small == 0, f"every button is a 44px tap target ({small} too small)")
    amt_on_screen = pg.evaluate("""() => [...document.querySelectorAll('#list .rv-amt')]
        .every(e => { const r = e.getBoundingClientRect(); return r.right <= innerWidth && r.left >= 0; })""")
    check(amt_on_screen, "amounts fit on screen")
    shot(pg, "s9-phone-list.png")

    # Swipe right accepts.
    item = pg.locator("#list .rv-item", has_text="Zomato")
    box = item.bounding_box()
    y = box["y"] + box["height"] / 2
    pg.evaluate("""([x0, x1, y, id]) => {
        const el = [...document.querySelectorAll('#list .rv-item')].find(e => e.textContent.includes(id));
        const ev = (type, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType: 'touch',
            pointerId: 7, isPrimary: true, clientX: x, clientY: y }));
        ev('pointerdown', x0);
        for (let x = x0; x <= x1; x += 20) ev('pointermove', x);
        ev('pointerup', x1);
    }""", [box["x"] + 20, box["x"] + 220, y, "Zomato"])
    pg.wait_for_timeout(900)
    check(pg.locator("#list .rv-item", has_text="Zomato").count() == 0, "swipe right accepts")
    check(pg.is_visible("#undoBar"), "undo bar after swipe")

    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_timeout(800)
    check(pg.is_visible("#needs"), "Needs-you chips on phone Home")
    nb, fb = pg.locator("#needs").bounding_box(), pg.locator(".figures").bounding_box()
    check(nb["y"] < fb["y"], "Needs-you sits above the figures on phone, not at the bottom of the page")
    over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check(over <= 0, f"Home fits at 390 ({over}px)")
    shot(pg, "s9-phone-home.png")
    ctx.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        desktop(b)
        phone(b)
        b.close()
    for e in errors:
        print("  [FAIL]", e)
    passed = sum(1 for r in results if r[0] == "PASS")
    print(f"{passed}/{len(results)} checks passed; {len(errors)} browser errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
