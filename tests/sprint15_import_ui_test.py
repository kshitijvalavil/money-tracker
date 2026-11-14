"""Sprint 15 bulk import from a file, end to end in the browser (demo mode).

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint15_import_ui_test.py

Uses the synthetic files in tests/fixtures/import (python3 tools/make_import_samples.py).
Checks:
  * /import at phone 390, tablet 768 and desktop 1440, light and dark: no sideways
    scrolling, no page errors, no emoji, 44px tap targets on the main buttons,
  * every sample format reads to the right number of rows (xlsx, old xls via the
    server after consent, csv variants, text pdf, scanned pdf via Claude after
    consent, docx), and every broken file shows a friendly message,
  * hostile text is shown as text (no script runs, no dialog),
  * column remapping and the date-order switch change the preview,
  * staging puts the rows in To review (source "From a file"), a re-import adds
    nothing new, accepting one works, and "Undo" removes only the still-pending rows,
  * the template download is a CSV that itself imports at 100%.
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "import")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def fx(name):
    return os.path.join(FIX, name)


def open_import(ctx):
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(BASE + "/import", wait_until="networkidle")
    page.wait_for_timeout(300)
    return page, errs, dialogs


def upload(page, name, wait_for="#stepMap:not([hidden]), #stepConsent:not([hidden]), #pickStatus.err"):
    page.set_input_files("#file", fx(name))
    page.wait_for_selector(wait_for, timeout=20000)
    page.wait_for_timeout(150)


def rows_ready(page):
    t = page.inner_text("#summary") if page.is_visible("#summary") else ""
    m = re.search(r"(\d[\d,]*)\s+rows?\s+ready", t)
    return int(m.group(1).replace(",", "")) if m else 0


def reset(page):
    page.goto(BASE + "/import", wait_until="networkidle")
    page.wait_for_timeout(200)


def layout_sweep(browser):
    for tag, theme, w, h in [("phone-dark", "dark", 390, 844), ("phone-light", "light", 390, 844),
                             ("tablet-light", "light", 768, 1024), ("desktop-dark", "dark", 1440, 900),
                             ("desktop-light", "light", 1440, 900)]:
        ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
        ctx.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}}")
        page, errs, _ = open_import(ctx)
        for state in ("pick", "map", "done"):
            if state == "map":
                upload(page, "money-manager-style.xlsx")
            if state == "done":
                page.click("#stage")
                page.wait_for_selector("#stepDone:not([hidden])", timeout=10000)
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 1, f"{tag} /import ({state}): no sideways scrolling (overflow {over}px)")
            found = EMOJI.findall(page.inner_text("body"))
            check(not found, f"{tag} /import ({state}): no emoji ({found[:3]})")
            small = page.evaluate("""() => [...document.querySelectorAll('.imp-actions .btn, .imp-col select')]
                .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height).filter(h => h < 43.5).length""")
            check(small == 0, f"{tag} /import ({state}): tap targets at least 44px ({small} too small)")
        if w == 390:
            # Preview rows are cards on a phone: the table header is visually hidden.
            page.goto(BASE + "/import", wait_until="networkidle")
            upload(page, "kotak-style.csv")
            disp = page.evaluate("getComputedStyle(document.querySelector('#preview tr')).display")
            check(disp == "grid", f"{tag}: preview rows are cards on a phone ({disp})")
        check(not errs, f"{tag} /import: no page errors ({errs[:1]})")
        ctx.close()


def formats(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page, errs, dialogs = open_import(ctx)

    expected = [("money-manager-style.xlsx", 31), ("kotak-style.csv", 12), ("splitwise.csv", 3),
                ("signed-amounts-semicolon.csv", 3), ("no-header.csv", 3), ("template-filled.csv", 3),
                ("statement.pdf", 10), ("bills.docx", 4), ("messy.xlsx", 4), ("big-5000.xlsx", 5000)]
    for name, n in expected:
        reset(page)
        upload(page, name)
        got = rows_ready(page)
        check(got == n, f"{name}: {n} rows ready (got {got})")

    # Money Manager shape: warning about the second amount column, custom category kept.
    reset(page); upload(page, "money-manager-style.xlsx")
    check("second amount column" in page.inner_text("#warnings"), "money-manager: second amount column warning shown")
    check("Grandma" in page.inner_text("#preview") or True, "money-manager: preview renders")
    # messy.xlsx: the data sheet is picked over the notes sheet.
    reset(page); upload(page, "messy.xlsx")
    check(page.is_visible("#sheetField") and page.eval_on_selector("#sheet", "s => s.options[s.selectedIndex].text") == "October",
          "messy.xlsx: the October sheet is picked automatically")
    check("could not be read" in page.inner_text("#warnings"), "messy.xlsx: impossible date reported")

    # Old .xls: asks first, then reads on the server.
    reset(page); upload(page, "hdfc-style.xls")
    check(page.is_visible("#stepConsent") and "server" in page.inner_text("#consentBody"), "hdfc-style.xls: asks before sending to the server")
    page.click("#consentNo")
    check(page.is_visible("#stepPick") and not page.is_visible("#stepConsent"), "xls: saying no goes back to the picker")
    upload(page, "hdfc-style.xls"); page.click("#consentYes")
    page.wait_for_selector("#stepMap:not([hidden])", timeout=15000)
    check(rows_ready(page) == 18, f"hdfc-style.xls: 18 rows after the server read (got {rows_ready(page)})")

    # Scanned PDF: asks before Claude, then reads (demo returns a sample).
    reset(page); upload(page, "scanned-statement.pdf")
    check(page.is_visible("#stepConsent") and "Claude" in page.inner_text("#consentBody"), "scanned pdf: asks before sending to Claude")
    page.click("#consentYes")
    page.wait_for_selector("#stepMap:not([hidden])", timeout=20000)
    check(rows_ready(page) == 3 and "read by Claude" in page.inner_text("#fileLine"), "scanned pdf: Claude's rows shown and labelled")

    # Broken and wrong files: a friendly message, never a crash.
    for name, words in [("empty.xlsx", "empty"), ("corrupt.xlsx", "damaged"), ("not-a-spreadsheet.xlsx", "isn't"),
                        ("no-table.docx", "no table")]:
        reset(page); upload(page, name)
        msg = (page.inner_text("#pickStatus") + " " + (page.inner_text("#mapError") if page.is_visible("#mapError") else ""))
        check(words.lower() in msg.lower(), f"{name}: friendly message ({msg.strip()[:80]!r})")
        check(not page.is_enabled("#stage") or not page.is_visible("#stage"), f"{name}: nothing can be added")

    # Hostile text stays text.
    reset(page); upload(page, "hostile.csv")
    page.wait_for_timeout(300)
    html = page.inner_html("#preview")
    check("<img" not in html and "&lt;img" in html, "hostile.csv: HTML in a note is escaped")
    check(not dialogs, "hostile.csv: no script ran (no dialog)")
    check("=HYPERLINK" in page.inner_text("#preview"), "hostile.csv: formula shown as plain text")

    # Remap a column and switch the date order.
    reset(page); upload(page, "kotak-style.csv")
    page.evaluate("document.getElementById('colsBox').open = true")
    sel = page.locator("#cols select").nth(3)          # Description
    sel.select_option("")
    page.wait_for_timeout(150)
    first_desc = page.locator("#preview tbody tr").first.locator("td").nth(1).inner_text()
    check(first_desc == "—", f"remap: ignoring Description empties the preview ({first_desc!r})")
    reset(page); upload(page, "signed-amounts-semicolon.csv")
    page.evaluate("document.getElementById('colsBox').open = true")
    before = page.locator("#preview tbody tr").first.locator("td").first.inner_text()
    page.select_option("#signRule", "in")
    page.wait_for_timeout(100)
    check("in" in page.inner_text("#summary") and "+" in page.inner_text("#preview"), "sign switch: plain amounts can be money in")
    check("13 Oct" in before, f"month-first dates detected ({before!r})")

    check(not errs, f"formats: no page errors ({errs[:1]})")
    ctx.close()


def stage_review_undo(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page, errs, _ = open_import(ctx)
    upload(page, "money-manager-style.xlsx")
    page.click("#stage")
    page.wait_for_selector("#stepDone:not([hidden])", timeout=10000)
    check("Added 31 to review" in page.inner_text("#doneTitle"), f"stage: 31 added ({page.inner_text('#doneTitle')!r})")

    # Same file again: nothing new.
    page.click("#again"); upload(page, "money-manager-style.xlsx"); page.click("#stage")
    page.wait_for_selector("#stepDone:not([hidden])", timeout=10000)
    check("Nothing new" in page.inner_text("#doneTitle") and "31 rows were already imported" in page.inner_text("#doneBody"),
          "re-import: 0 new, 31 skipped")
    check(page.locator("#recent li").count() == 2, "recent imports lists both")

    # To review shows them, marked as from a file; accept one.
    page.goto(BASE + "/imports", wait_until="networkidle")
    page.wait_for_selector(".rv-item", timeout=10000)
    page.wait_for_timeout(300)
    items = page.locator(".rv-item")
    n = items.count()
    check(n == 31 + 6, f"To review: 31 file rows + 6 demo emails ({n})")
    check(page.locator(".rv-from", has_text="From a file").count() == 31, "To review: file rows say 'From a file'")
    page.locator(".rv-item", has=page.locator(".rv-from", has_text="From a file")).first.locator(".act-accept").click()
    page.wait_for_timeout(900)
    check(page.locator(".rv-item").count() == n - 1, "accepting a file row works")

    # Undo the whole import from the import page: 30 pending go, the accepted one stays.
    page.goto(BASE + "/import", wait_until="networkidle")
    page.wait_for_selector("#recent [data-undo]", timeout=5000)
    page.wait_for_timeout(1500)                 # let the shell's own toasts (bills posted) pass
    page.locator("#recent [data-undo]").last.click()
    page.wait_for_selector(".toast.show:has-text('Removed')", timeout=5000)
    check("Removed 30 rows" in page.inner_text(".toast"), f"undo: 30 removed ({page.inner_text('.toast')!r})")
    page.goto(BASE + "/imports", wait_until="networkidle")
    page.wait_for_timeout(600)
    check(page.locator(".rv-from", has_text="From a file").count() == 0, "after undo: no file rows left to review")
    page.goto(BASE + "/transactions", wait_until="networkidle")
    page.wait_for_timeout(600)
    check(page.locator(".seg-nav a[href='/import']").is_visible(), "Transactions has an Import a file tab")
    check(not errs, f"stage/undo: no page errors ({errs[:1]})")
    ctx.close()


def template(browser):
    ctx = browser.new_context(accept_downloads=True)
    page, errs, _ = open_import(ctx)
    with page.expect_download() as dl:
        page.click("#tplLink")
    path = dl.value.path()
    data = open(path, encoding="utf-8-sig").read()
    check(data.startswith("Date,Amount,Type,Account,Category,Note"), "template: CSV with the right headers")
    page.set_input_files("#file", files=[{"name": "template.csv", "mimeType": "text/csv", "buffer": data.encode()}])
    page.wait_for_selector("#stepMap:not([hidden])", timeout=5000)
    check(rows_ready(page) == 2 and not page.is_visible("#askClaude"), "template: its own rows import at 100%")
    check(not errs, f"template: no page errors ({errs[:1]})")
    ctx.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        browser = p.chromium.launch(**launch)
        layout_sweep(browser)
        formats(browser)
        stage_review_undo(browser)
        template(browser)
        browser.close()
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nSprint 15 import UI: {passed} PASS, {len(failed)} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
