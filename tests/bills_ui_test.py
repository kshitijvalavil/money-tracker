"""Bills page + dashboard Upcoming bills browser test (Sprint 2) — demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/bills_ui_test.py

Demo data lives in memory and resets on every page load. Like
imports_ui_test.py, scenarios reach the in-page store through an init script
that intercepts the `window.__demoDb = db` assignment in static/js/app.js.

Covers: the shell auto-posts due bills once (idempotent), list order and due
labels, monthly total, Mark paid (triple click, keyboard), Skip, add, edit
without and with a schedule change (reschedule_recurring, no seq collision),
the past-payments warning, pause / resume (no backlog), delete, category filter
by type, form validation, suggestions (confidence cut-off, tidy names, Add as
bill, Dismiss), hostile bill names, the dashboard card, and no horizontal
scroll at 1440 and 390 px in day and night themes. Needs playwright + Chromium
(CHROME_PATH, default /opt/pw-browsers/chromium). SHOT_DIR saves screenshots.
"""
import datetime as dt
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")
TODAY = dt.datetime.now(dt.timezone.utc).date()   # demo dates are UTC
XSS_IMG = "<img src=x onerror=window.__pwned=1>"

results, errors = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def iso(d):
    return d.isoformat()


# JS run before page scripts: when app.js publishes the demo client, expose
# helpers on window.__qaStore and run `mutate(S)` (a function body string).
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
        db: v,
        all(table) { const b = v.from(table); b._op = "select"; return b._run().data; },
        insert(table, rows) { const b = v.from(table).insert(rows); return b._run(); },
        clear(table) { const b = v.from(table).delete(); b._run(); },
        bill(name) { return this.all("recurring").find((r) => r.name === name); },
        txFor(id) { return this.all("transactions").filter((t) => t.recurring_id === id); },
      };
      (function (S) { %s })(S);
    },
  });
})();
""" % mutate_js


def page_with(browser, mutate_js="", width=1280, theme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    ctx.add_init_script(seed_script(mutate_js))
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("dialog", lambda d: (errors.append(f"unexpected dialog: {d.message}"), d.dismiss())
          if d.type != "confirm" else d.accept())
    return ctx, pg


def ev(pg, js):
    return pg.evaluate(js)


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def open_bills(pg):
    pg.goto(BASE + "/bills", wait_until="networkidle")
    pg.wait_for_selector("#billRows tr[data-id]")
    pg.wait_for_timeout(300)


def row(pg, name):
    return pg.locator("#billRows tr[data-id]", has=pg.locator(".bill-name", has_text=name))


def bill(pg, name):
    return ev(pg, f"() => window.__qaStore.bill({json.dumps(name)})")


def tx_count(pg, bill_id):
    return ev(pg, f"() => window.__qaStore.txFor({json.dumps(bill_id)}).length")


def toast(pg):
    return pg.text_content("#toast") or ""


def settle(pg, ms=900):
    pg.wait_for_timeout(ms)


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)

        # --- Shell auto-posts due bills once ------------------------------------
        ctx, pg = page_with(b)
        open_bills(pg)
        gym = bill(pg, "Gym membership")
        posted = ev(pg, f"() => window.__qaStore.txFor({json.dumps(gym['id'])})")
        check(len(posted) == 1, f"shell posted the auto-post gym bill due today once ({len(posted)})")
        check(all(t["source"] == "recurring" and t["occurred_on"] == iso(TODAY) for t in posted),
              "posted bill has source 'recurring' and occurred_on = its due date")
        check(gym["next_due"] == iso(TODAY + dt.timedelta(days=7)), f"gym advanced a week ({gym['next_due']})")
        again = ev(pg, "async () => (await window.__qaStore.db.rpc('post_due_recurring')).data")
        check(again == 0, f"post_due_recurring again posts nothing ({again})")
        check("Posted 1 bill" in toast(pg), f"toast says Posted 1 bill ({toast(pg)!r})")

        # --- List, order, labels, total -------------------------------------------
        names = pg.locator("#billRows tr[data-id] .bill-name").all_inner_texts()
        check(names[0].startswith("Netflix"), f"overdue Netflix listed first ({names})")
        check(len(names) == 4, f"4 sample bills ({len(names)})")
        check("Overdue 2 days" in row(pg, "Netflix").inner_text(), "Netflix shows 'Overdue 2 days'")
        bills = ev(pg, "() => window.__qaStore.all('recurring')")
        per = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}
        total = sum(float(x["amount"]) * per[x["frequency"]] for x in bills if x["active"] and x["type"] == "expense")
        text = pg.inner_text("#monthlyTotal")
        shown = text.split("≈")[-1].split("/")[0].replace("₹", "").replace(",", "").strip()
        check(text.startswith("Bills ≈") and abs(float(shown) - total) < 0.01,
              f"monthly total ≈ {total:.2f} ({text!r})")
        lbl = row(pg, "Netflix").locator(".mark-paid").get_attribute("aria-label")
        check(lbl and "Netflix" in lbl and "overdue" in lbl, f"Mark paid has a descriptive label ({lbl!r})")
        sw = row(pg, "Netflix").locator(".auto-toggle")
        check(sw.get_attribute("role") == "switch" and "Netflix" in (sw.get_attribute("aria-label") or ""),
              "auto-post switch has role=switch and a name")

        # --- Suggestions ---------------------------------------------------------
        sugg = pg.locator("#suggestions li.suggest .bill-name").all_inner_texts()
        check(sorted(sugg) == ["Airtel Recharge", "Spotify Premium"],
              f"two suggestions, tidy names, weak Chai Point hidden ({sugg})")
        pg.locator("#suggestions li.suggest", has_text="Spotify").locator(".add-sugg").click()
        check(pg.input_value("#fName") == "Spotify Premium" and pg.input_value("#fAmount") == "119"
              and pg.input_value("#fFreq") == "monthly", "Add as bill pre-fills name, amount, frequency")
        check(pg.evaluate("() => document.activeElement.id") == "fName", "focus moves to the form")
        pg.locator("#suggestions li.suggest", has_text="Airtel").locator(".dismiss").click()
        check(pg.locator("#suggestions li.suggest", has_text="Airtel").count() == 0, "Dismiss removes it")
        pg.reload(wait_until="networkidle")
        pg.wait_for_selector("#billRows tr[data-id]")
        settle(pg, 400)
        check(pg.locator("#suggestions li.suggest", has_text="Airtel").count() == 0,
              "dismissal lasts for the session")

        # --- Mark paid (triple click) ------------------------------------------------
        net = bill(pg, "Netflix")
        before = tx_count(pg, net["id"])
        btn = row(pg, "Netflix").locator(".mark-paid")
        btn.click(); btn.click(force=True); btn.click(force=True)
        settle(pg, 1200)
        net2 = bill(pg, "Netflix")
        check(tx_count(pg, net["id"]) == before + 1, "triple-clicking Mark paid posts exactly one payment")
        check(net2["next_seq"] == net["next_seq"] + 1 and net2["next_due"] > net["next_due"],
              f"Netflix advanced ({net['next_due']} -> {net2['next_due']})")
        check(toast(pg).startswith("Paid Netflix"), f"toast confirms ({toast(pg)!r})")
        check(pg.evaluate("() => document.activeElement.classList.contains('mark-paid')"),
              "focus returns to the row's Mark paid")

        # --- Keyboard: Enter on Mark paid ------------------------------------------
        rent = bill(pg, "Flat rent")
        row(pg, "Flat rent").locator(".mark-paid").focus()
        pg.keyboard.press("Enter")
        settle(pg)
        check(tx_count(pg, rent["id"]) == 1, "Enter on Mark paid posts the payment")

        # --- Skip ------------------------------------------------------------------
        ins = bill(pg, "Home insurance")
        row(pg, "Home insurance").locator(".skip").click()
        settle(pg)
        ins2 = bill(pg, "Home insurance")
        check(tx_count(pg, ins["id"]) == 0 and ins2["next_seq"] == ins["next_seq"] + 1,
              "Skip advances without posting")
        check(toast(pg).startswith("Skipped Home insurance"), f"skip toast ({toast(pg)!r})")

        # --- Validation ----------------------------------------------------------
        pg.click("#addBillBtn")
        n_bills = len(ev(pg, "() => window.__qaStore.all('recurring')"))
        pg.click("#saveBtn")
        settle(pg, 300)
        check(pg.get_attribute("#fName", "aria-invalid") == "true" and pg.is_visible("#fNameErr"),
              "empty name flagged inline")
        check(len(ev(pg, "() => window.__qaStore.all('recurring')")) == n_bills, "nothing saved when invalid")

        # --- Category filter by type ---------------------------------------------
        pg.select_option("#fType", "income")
        opts = pg.locator("#fCategory option").all_inner_texts()
        cats = ev(pg, "() => window.__qaStore.all('categories')")
        income = {c["name"] for c in cats if c["kind"] == "income"}
        check(opts and set(opts) <= income, f"income type lists only income categories ({opts})")
        pg.select_option("#fType", "expense")

        # --- Add (future date) ----------------------------------------------------
        due = iso(TODAY + dt.timedelta(days=5))
        pg.fill("#fName", "Water bill")
        pg.fill("#fAmount", "300")
        pg.select_option("#fFreq", "monthly")
        pg.fill("#fStart", due)
        check(pg.inner_text("#fPastWarn") == "", "no past-payments warning for a future date")
        pg.click("#saveBtn")
        settle(pg)
        w = bill(pg, "Water bill")
        check(w and w["next_due"] == due and w["next_seq"] == 0 and w["start_date"] == due,
              f"new bill saved; trigger set next_due ({w and w['next_due']})")
        check(row(pg, "Water bill").count() == 1, "new bill appears in the list")

        # --- Add (past date + auto-post): warning + backlog posted now ------------
        start = TODAY - dt.timedelta(days=63)            # weekly: 10 dates up to today
        pg.click("#addBillBtn")
        pg.fill("#fName", "Newspaper")
        pg.fill("#fAmount", "40")
        pg.select_option("#fFreq", "weekly")
        pg.fill("#fStart", iso(start))
        pg.check("#fAuto")
        warn = pg.inner_text("#fPastWarn")
        check("This will add 10 past payments now." in warn, f"past-payments warning ({warn!r})")
        pg.click("#saveBtn")
        settle(pg, 1200)
        nb = bill(pg, "Newspaper")
        check(nb and tx_count(pg, nb["id"]) == 10, f"10 past payments posted on save ({nb and tx_count(pg, nb['id'])})")
        check(nb and nb["next_due"] == iso(start + dt.timedelta(days=70)), "next due is the first future date")

        # --- Edit without a schedule change -----------------------------------------
        row(pg, "Water bill").locator(".edit").click()
        check(pg.inner_text("#fStartLabel") == "Next due date" and pg.input_value("#fStart") == due,
              "edit shows the next due date")
        pg.fill("#fAmount", "350")
        pg.click("#saveBtn")
        settle(pg)
        w2 = bill(pg, "Water bill")
        check(float(w2["amount"]) == 350 and w2["start_date"] == w["start_date"] and w2["next_seq"] == 0,
              "amount edit keeps the schedule")
        check(pg.evaluate("() => document.activeElement.classList.contains('edit')"), "focus back on Edit")

        # --- Edit with a schedule change: reschedule_recurring --------------------
        row(pg, "Water bill").locator(".mark-paid").click()       # posts seq 0
        settle(pg)
        wid = w2["id"]
        new_due = iso(TODAY + dt.timedelta(days=10))
        row(pg, "Water bill").locator(".edit").click()
        pg.fill("#fStart", new_due)
        pg.click("#saveBtn")
        settle(pg)
        w3 = bill(pg, "Water bill")
        seqs = sorted(t["recurring_seq"] for t in ev(pg, f"() => window.__qaStore.txFor({json.dumps(wid)})"))
        check(w3["start_date"] == new_due and w3["next_seq"] == 0 and w3["next_due"] == new_due,
              f"rescheduled from the picked date ({w3['start_date']}, seq {w3['next_seq']})")
        check(seqs == [-1], f"old payment renumbered to a negative seq ({seqs})")
        row(pg, "Water bill").locator(".mark-paid").click()
        settle(pg)
        check(tx_count(pg, wid) == 2, "Mark paid after reschedule posts a new payment (no seq collision)")

        # --- Pause / resume ---------------------------------------------------------
        row(pg, "Netflix").locator(".pause").click()
        settle(pg)
        r = row(pg, "Netflix")
        check("Paused" in r.inner_text() and r.locator(".mark-paid").count() == 1,
              "paused bill shows Paused and can still be marked paid")
        up = ev(pg, "async () => (await window.__qaStore.db.rpc('upcoming_recurring', {p_days: 400})).data.map(x => x.name)")
        check("Netflix" not in up, "paused bill left out of upcoming")
        # Pretend it stayed paused for 3 months with auto-post on: move its
        # schedule back so three dates are overdue.
        ev(pg, """() => { const r = window.__qaStore.bill('Netflix');
            window.__qaStore.db.from('recurring').update({ auto_post: true, next_seq: Math.max(0, r.next_seq - 3) })
              .eq('id', r.id)._run(); }""")
        n_before = tx_count(pg, net["id"])
        got = ev(pg, "async () => (await window.__qaStore.db.rpc('post_due_recurring')).data")
        check(got == 0 and tx_count(pg, net["id"]) == n_before, "auto-post skips a paused bill")
        row(pg, "Netflix").locator(".pause").click()
        settle(pg)
        n3 = bill(pg, "Netflix")
        check(n3["active"] and n3["next_due"] >= iso(TODAY), f"resume skips missed dates ({n3['next_due']})")
        ev(pg, "async () => window.__qaStore.db.rpc('post_due_recurring')")
        check(tx_count(pg, net["id"]) == n_before, "resume leaves no backlog to auto-post")
        check(toast(pg).startswith("Resumed Netflix"), f"resume toast ({toast(pg)!r})")

        # --- Auto-post switch -------------------------------------------------------
        row(pg, "Flat rent").locator(".auto-toggle").check(force=True)
        settle(pg)
        check(bill(pg, "Flat rent")["auto_post"] is True, "auto-post switch saves")

        # --- Delete ------------------------------------------------------------------
        nid = nb["id"]
        row(pg, "Newspaper").locator(".delete").click()
        settle(pg)
        check(row(pg, "Newspaper").count() == 0 and bill(pg, "Newspaper") is None, "Delete removes the bill")
        orphan = ev(pg, f"() => window.__qaStore.all('transactions').filter(t => t.note === 'Newspaper')")
        check(len(orphan) == 10 and all(t["recurring_id"] is None for t in orphan),
              "its payments stay, unlinked (on delete set null)")
        check(nid not in json.dumps(ev(pg, "() => window.__qaStore.all('recurring')")), "bill gone from store")
        ctx.close()

        # --- UI hides suggestions below 0.5 confidence (decision 2) ------------------------
        stub = """const rpc = S.db.rpc.bind(S.db);
            S.db.rpc = async (n, a) => n !== 'suggest_recurring' ? rpc(n, a) : ({ error: null, data: [
              { name: 'NETFLIX.COM 9921', type: 'expense', amount: 649, frequency: 'monthly', account_id: null,
                category_id: null, last_seen: '2026-10-01', next_guess: '2026-11-01', occurrences: 4, confidence: 0.5 },
              { name: 'Random shop', type: 'expense', amount: 80, frequency: 'weekly', account_id: null,
                category_id: null, last_seen: '2026-10-01', next_guess: '2026-10-08', occurrences: 3, confidence: 0.49 } ] });"""
        ctx, pg = page_with(b, stub)
        open_bills(pg)
        sugg = pg.locator("#suggestions li.suggest .bill-name").all_inner_texts()
        check(sugg == ["Netflix.com"], f"0.49 hidden, 0.5 shown, name tidied ({sugg})")
        ctx.close()

        # --- Rescheduling an auto-post bill to a past date is refused (decision 15) -------
        ctx, pg = page_with(b)
        open_bills(pg)
        gym = bill(pg, "Gym membership")
        row(pg, "Gym membership").locator(".edit").click()
        pg.fill("#fStart", iso(TODAY - dt.timedelta(days=14)))
        pg.click("#saveBtn")
        settle(pg, 500)
        check(pg.get_attribute("#fStart", "aria-invalid") == "true" and "today or a later date" in pg.inner_text("#fStartErr"),
              "past date for an auto-post bill flagged inline")
        check(bill(pg, "Gym membership")["start_date"] == gym["start_date"], "bill not rescheduled")
        pg.uncheck("#fAuto")
        pg.click("#saveBtn")
        settle(pg)
        g2 = bill(pg, "Gym membership")
        check(g2["start_date"] == iso(TODAY - dt.timedelta(days=14)) and not g2["auto_post"]
              and g2["next_due"] < iso(TODAY), "with auto-post off, a past date is allowed and shows as overdue")
        ctx.close()

        # --- Hostile bill name ----------------------------------------------------------
        hostile = f"""const a = S.all('accounts')[0], c = S.all('categories').find(x => x.kind === 'expense');
            S.insert('recurring', {{ name: {json.dumps(XSS_IMG)}, type: 'expense', amount: 9,
              account_id: a.id, category_id: c.id, frequency: 'monthly', start_date: '{iso(TODAY + dt.timedelta(days=1))}' }});"""
        for path in ("/bills", "/"):
            ctx, pg = page_with(b, hostile)
            pg.goto(BASE + path, wait_until="networkidle")
            settle(pg, 600)
            check(pg.evaluate("() => window.__pwned === undefined"), f"hostile bill name runs nothing on {path}")
            check(pg.locator("main img[src=x]").count() == 0, f"hostile bill name not rendered as HTML on {path}")
            check(XSS_IMG in (pg.text_content("main") or ""), f"hostile bill name shown as text on {path}")
            ctx.close()

        # --- Dashboard card ------------------------------------------------------------------
        ctx, pg = page_with(b)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_selector("#upcoming li[data-id]")
        items = pg.locator("#upcoming li[data-id] .bill-name").all_inner_texts()
        check(items and items[0].startswith("Netflix") and any(i.startswith("Flat rent") for i in items)
              and not any(i.startswith("Home insurance") for i in items),
              f"Upcoming bills: overdue first, next 7 days only ({items})")
        net = bill(pg, "Netflix")
        pg.locator("#upcoming li[data-id]", has_text="Netflix").locator(".mark-paid").click()
        settle(pg)
        check(tx_count(pg, net["id"]) == 1, "Mark paid on the dashboard posts the payment")
        check(pg.locator("#upcoming li[data-id]", has_text="Netflix").count() == 0,
              "paid bill leaves the card")
        ctx.close()

        # Empty card.
        ctx, pg = page_with(b, "S.clear('recurring');")
        pg.goto(BASE + "/", wait_until="networkidle")
        settle(pg, 500)
        check("Nothing due in the next 7 days" in pg.inner_text("#upcoming"), "empty card copy")
        ctx.close()

        # --- Widths and themes ---------------------------------------------------------
        for theme in ("light", "dark"):
            for w in (1440, 390):
                for path, tag in (("/bills", "bills"), ("/", "dashboard")):
                    ctx, pg = page_with(b, width=w, theme=theme)
                    pg.goto(BASE + path, wait_until="networkidle")
                    settle(pg, 600)
                    sw = pg.evaluate("() => document.documentElement.scrollWidth")
                    check(sw <= w, f"no horizontal scroll on {path} at {w}px {theme} (scrollWidth {sw})")
                    if path == "/bills" and w == 390:
                        box = pg.locator("#billRows tr[data-id] .mark-paid").first.bounding_box()
                        check(box and box["height"] >= 44, f"Mark paid is a 44px target at 390px ({box})")
                    shot(pg, f"{tag}-{w}-{theme}.png")
                    ctx.close()

        b.close()

    print("\n=== BILLS UI TEST RESULTS ===")
    for status, msg in results:
        print(f"  [{status}] {msg}")
    if errors:
        print("\n=== BROWSER ERRORS ===")
        for e in errors:
            print("  " + e)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed; {len(errors)} browser errors")
    return 1 if (fails or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
