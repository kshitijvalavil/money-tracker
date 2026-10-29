"""Imports page browser test (QA, Sprint 1) — runs against demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/imports_ui_test.py

Demo data lives in memory and resets on every page load, so each scenario
seeds the in-page store through an init script that intercepts the
`window.__demoDb = db` assignment in static/js/app.js (before any page code
reads the store).

Covers: hostile text in every imported field is escaped (imports page, and
the same text once it is a transaction on /transactions and the dashboard),
sidebar badge at 0 / 1 / 99 / 100+, approve / reject / approve all, double
click, no horizontal scroll at 390px. Needs playwright + Chromium
(CHROME_PATH, default /opt/pw-browsers/chromium).
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")

XSS_IMG = "<img src=x onerror=window.__pwned=1>"
XSS = {
    "note": XSS_IMG,
    "source": "<script>window.__pwned=2</script>_alert",
    "account_hint": '"><svg onload=window.__pwned=3>',
    "occurred_on": "2026-10-20<b>x</b>",
}

STRICT = os.environ.get("QA_STRICT") == "1"
results, errors, xfails = [], [], []


def known_bug(cond, bug, msg):
    """A check for an open bug: XFAIL while broken (does not fail the run
    unless QA_STRICT=1), XPASS once fixed."""
    if cond:
        print(f"  XPASS ({bug}): {msg} -- bug looks fixed, drop the tag")
    elif STRICT:
        check(False, f"{bug}: {msg}")
    else:
        xfails.append(f"{bug}: {msg}")


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


# JS run before page scripts: when app.js publishes the demo client, mutate
# its store with `mutate(store)` (a function body given as a string).
def seed_script(mutate_js):
    return """
(() => {
  let client;
  Object.defineProperty(window, "__demoDb", {
    configurable: true,
    get() { return client; },
    set(v) {
      client = v;
      const S = window.__qaStore = {
        all(table) { const b = v.from(table); b._op = "select"; return b._run().data; },
        insert(table, rows) { const b = v.from(table).insert(rows); b._run(); },
        clear(table) { const b = v.from(table).delete(); b._run(); },
      };
      (function (S) { %s })(S);
    },
  });
})();
""" % mutate_js


def pending_row(i, **over):
    row = {"id": f"qa-{i}", "source": "sbi_alert", "direction": "expense", "amount": 10 + i,
           "occurred_on": "2026-10-20", "account_hint": None, "note": f"QA row {i}",
           "source_message_id": f"qa-msg-{i}", "status": "pending", "created_at": "2026-10-20"}
    row.update(over)
    return row


def page_with(browser, mutate_js, width=1100):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.add_init_script(seed_script(mutate_js))
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("dialog", lambda d: (errors.append(f"unexpected dialog: {d.message}"), d.dismiss())
          if d.type != "confirm" else d.accept())
    return ctx, pg


def badge(pg):
    return pg.evaluate("""() => { const b = document.getElementById('importsBadge');
        return { hidden: b.hidden, text: b.textContent }; }""")


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)

        # --- XSS: hostile text in every rendered import field -----------------
        hostile = json.dumps([pending_row(1, **XSS), pending_row(2, id='x" onmouseover="window.__pwned=4')])
        ctx, pg = page_with(b, f"S.clear('pending_imports'); S.insert('pending_imports', {hostile});")
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(500)
        rows = pg.locator("#rows")
        check(pg.locator("#rows tr[data-id]").count() == 2, "hostile imports render as rows")
        check(pg.evaluate("() => window.__pwned === undefined"), "no injected script ran on /imports")
        # Any img/script/b, or any svg that is NOT one of our own icon-button
        # glyphs, means hostile markup rendered. (The reject button carries a
        # legitimate inline <svg> inside .icon-btn — that one is allowed.)
        injected = pg.evaluate("""() => {
            const r = document.getElementById('rows');
            const bad = r.querySelectorAll('img, script, b').length;
            const svgs = [...r.querySelectorAll('svg')].filter(s => !s.closest('.icon-btn')).length;
            return bad + svgs;
        }""")
        check(injected == 0, "no injected elements in the imports table")
        txt = rows.text_content()   # raw text (the .pill CSS capitalizes inner_text)
        check(XSS_IMG in txt, "hostile note shown as literal text")
        check("<svg onload" in txt, "hostile account hint shown as literal text")
        check(pg.locator('#rows tr[onmouseover]').count() == 0, "hostile id cannot break out of data-id")
        pg.locator("#rows tr[data-id] .reject").first.hover()
        check(pg.evaluate("() => window.__pwned === undefined"), "still nothing ran after hover")
        shot(pg, "imports-xss.png")
        ctx.close()

        # Same hostile note once it is a real transaction (what approve creates).
        tx_js = f"""const a = S.all('accounts')[0], c = S.all('categories').find(x => x.kind === 'expense');
            S.insert('transactions', {{ id: 'qa-tx', occurred_on: new Date().toISOString().slice(0,10),
              amount: 5, type: 'expense', account_id: a.id, to_account_id: null, category_id: c.id,
              note: {json.dumps(XSS_IMG)}, source: {json.dumps(XSS['source'])}, source_message_id: 'm' }});"""
        for path in ("/transactions", "/"):
            ctx, pg = page_with(b, tx_js)
            pg.goto(BASE + path, wait_until="networkidle")
            pg.wait_for_timeout(500)
            check(pg.evaluate("() => window.__pwned === undefined"), f"imported note does not run script on {path}")
            check(pg.locator("main img[src=x]").count() == 0, f"imported note not rendered as HTML on {path}")
            ctx.close()

        # --- Badge counts ------------------------------------------------------
        for n, want in ((0, None), (1, "1"), (99, "99"), (100, "99+"), (150, "99+")):
            rows_js = json.dumps([pending_row(i) for i in range(n)])
            ctx, pg = page_with(b, f"S.clear('pending_imports'); S.insert('pending_imports', {rows_js});")
            pg.goto(BASE + "/", wait_until="networkidle")
            pg.wait_for_timeout(400)
            got = badge(pg)
            if want is None:
                check(got["hidden"], f"badge hidden with 0 pending (got {got})")
            else:
                check(not got["hidden"] and got["text"] == want, f"badge shows {want} for {n} pending (got {got})")
            label = pg.get_attribute('[data-nav="imports"]', "aria-label")
            check((label is None) if n == 0 else (label and str(n) in label),
                  f"imports link aria-label for {n} pending ({label!r})")
            ctx.close()

        # Badge ignores approved/rejected rows.
        mixed = json.dumps([pending_row(1), pending_row(2, status="approved"), pending_row(3, status="rejected")])
        ctx, pg = page_with(b, f"S.clear('pending_imports'); S.insert('pending_imports', {mixed});")
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(400)
        check(badge(pg)["text"] == "1", f"badge counts only pending ({badge(pg)})")
        ctx.close()

        # --- Approve / reject / double click / approve all --------------------
        ctx, pg = page_with(b, "")                       # stock demo: 5 pending
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(500)
        n0 = pg.locator("#rows tr[data-id]").count()
        tx0 = pg.evaluate("() => window.__qaStore.all('transactions').length")
        check(badge(pg)["text"] == str(n0), f"badge matches the {n0} rows on the page")
        btn = pg.locator("#rows tr[data-id] .approve").first
        btn.click(); btn.click(force=True); btn.click(force=True)        # impatient user
        pg.wait_for_timeout(900)
        check(pg.evaluate("() => window.__qaStore.all('transactions').length") == tx0 + 1,
              "triple-clicking approve creates exactly one transaction")
        check(pg.locator("#rows tr[data-id]").count() == n0 - 1, "approved row leaves the list")
        check(badge(pg)["text"] == str(n0 - 1), f"badge drops after approve ({badge(pg)})")
        pg.locator("#rows tr[data-id] .reject").first.click()
        pg.wait_for_timeout(900)
        check(pg.evaluate("() => window.__qaStore.all('transactions').length") == tx0 + 1,
              "reject creates no transaction")
        check(badge(pg)["text"] == str(n0 - 2), f"badge drops after reject ({badge(pg)})")
        pg.click("#approveAll")
        pg.wait_for_timeout(350 * (n0 + 2) + 600)
        check(pg.evaluate("() => window.__qaStore.all('transactions').length") == tx0 + n0 - 1,
              "approve all adds every remaining row")
        check("caught up" in pg.inner_text("#rows"), "empty state after approve all")
        check(badge(pg)["hidden"], "badge hidden after everything is handled")
        ctx.close()

        # --- 390px phone width --------------------------------------------------
        ctx, pg = page_with(b, f"S.insert('pending_imports', {json.dumps([pending_row(9, note='A very long merchant note ' * 4)])});", width=390)
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(500)
        sw = pg.evaluate("() => document.documentElement.scrollWidth")
        check(sw <= 390, f"no horizontal page scroll at 390px (scrollWidth {sw})")
        btn = pg.locator("#rows tr[data-id] .approve").first
        btn.scroll_into_view_if_needed()
        box = btn.bounding_box()
        check(box is not None and box["width"] >= 24 and box["height"] >= 24,
              f"approve button usable at 390px ({box})")
        shot(pg, "imports-390.png")
        ctx.close()

        # --- No horizontal page scroll at phone / tablet / laptop widths -------
        # SB-4 regression: .content needs min-width: 0, or wide tables stretch
        # the whole page instead of scrolling inside .table-wrap.
        for path in ("/imports", "/transactions", "/accounts"):
            for w in (390, 768, 900, 1024):
                ctx, pg = page_with(b, "", width=w)
                pg.goto(BASE + path, wait_until="networkidle")
                pg.wait_for_timeout(400)
                sw = pg.evaluate("() => document.documentElement.scrollWidth")
                check(sw <= w, f"no horizontal page scroll on {path} at {w}px (scrollWidth {sw})")
                ctx.close()

        # --- SB-6 regression: at 390px each import shows what you're approving --
        ctx, pg = page_with(b, "", width=390)
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(400)
        vis = pg.evaluate("""() => { const tr = document.querySelector('#rows tr[data-id]');
            const inView = (sel) => { const r = tr.querySelector(sel).getBoundingClientRect();
                return r.width > 0 && r.left >= 0 && r.right <= innerWidth; };
            return { date: inView('td:first-child'), note: inView('td:nth-child(3)'),
                     account: inView('.sel-account'), category: inView('.sel-category'),
                     approve: inView('.approve') }; }""")
        check(all(vis.values()), f"date, note, account, category and approve all on screen at 390px ({vis})")
        ctx.close()

        # --- SB-5 regression: no category of the import's kind ----------------
        # The real approve_import refuses a null category (P0002), so the page
        # must not send one, must say why, and the demo must refuse it too.
        ctx, pg = page_with(b, "S.clear('categories');")
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(400)
        tx0 = pg.evaluate("() => window.__qaStore.all('transactions').length")
        n0 = pg.locator("#rows tr[data-id]").count()
        pg.locator("#rows tr[data-id] .approve").first.click()
        pg.wait_for_timeout(800)
        check(pg.evaluate("() => window.__qaStore.all('transactions').length") == tx0,
              "approve with no category of that kind adds nothing")
        check("category first" in (pg.text_content("#toast") or ""), "toast explains a category is needed")
        check(pg.locator("#rows .cell-hint").count() == n0, "every blocked row shows a category hint")
        check(pg.locator("#approveAll").is_hidden(), "approve all hidden when nothing is approvable")
        # The demo RPC itself refuses a null category, like the database.
        code = pg.evaluate("""async () => { const imp = window.__qaStore.all('pending_imports')[0];
            const acc = window.__qaStore.all('accounts')[0];
            const r = await window.__demoDb.rpc('approve_import',
                { p_import_id: imp.id, p_account_id: acc.id, p_category_id: null });
            return r.error && r.error.code; }""")
        check(code == "P0002", f"demo approve_import with null category -> P0002 (got {code})")
        ctx.close()

        # Mixed: only expense categories -> income rows blocked, approve all skips them.
        ctx, pg = page_with(b, "S.all('categories').filter(c => c.kind === 'income').forEach(c => { const q = window.__demoDb.from('categories').delete().eq('id', c.id); q._run(); });")
        pg.goto(BASE + "/imports", wait_until="networkidle")
        pg.wait_for_timeout(400)
        blocked = pg.locator("#rows tr[data-blocked]").count()
        total = pg.locator("#rows tr[data-id]").count()
        check(blocked >= 1 and blocked < total, f"only income rows blocked ({blocked} of {total})")
        pg.click("#approveAll")
        pg.wait_for_timeout(350 * total + 800)
        check(pg.locator("#rows tr[data-id]").count() == blocked, "approve all approves the rest, keeps blocked rows")
        check(pg.evaluate("() => window.__qaStore.all('transactions').every(t => t.type === 'transfer' || t.category_id)"),
              "no uncategorised transaction was created")
        ctx.close()

        b.close()

    print("\n=== IMPORTS UI TEST RESULTS ===")
    for status, msg in results:
        print(f"  [{status}] {msg}")
    if errors:
        print("\n=== BROWSER ERRORS ===")
        for e in errors:
            print("  " + e)
    for x in xfails:
        print("  XFAIL " + x)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed; {len(errors)} browser errors"
          + (f"; {len(xfails)} known bug(s) open" if xfails else ""))
    return 1 if (fails or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
