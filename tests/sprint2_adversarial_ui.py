"""Sprint 2 release gate (Sherlock Bugs): Bills page + dashboard card, demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python3 tests/sprint2_adversarial_ui.py

Written against docs/sprint-2-contract.md (Manager decisions 1-14). Demo data
resets on every page load, so scenarios seed the in-page store through an init
script that intercepts `window.__demoDb = db` in static/js/app.js; it also
counts RPC calls. Every scenario runs even if an earlier one fails.

Selectors live in SEL below; if the page's markup changes, fix them there.
Needs playwright + Chromium (CHROME_PATH, default /opt/pw-browsers/chromium).
SHOT_DIR=<dir> saves screenshots.
"""
import json
import os
import re
import sys
import traceback

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
SHOTS = os.environ.get("SHOT_DIR", "")

SEL = {
    "rows": "#billRows tr[data-id]",
    "mark_paid": ".mark-paid", "skip": ".skip", "edit": ".edit", "pause": ".pause",
    "delete": ".delete", "auto": ".auto-toggle", "auto_visual": ".switch-ui",
    "total": "#monthlyTotal",
    "form": "#billForm", "name": "#fName", "type": "#fType", "amount": "#fAmount",
    "account": "#fAccount", "category": "#fCategory", "freq": "#fFreq", "start": "#fStart",
    "auto_form": "#fAuto", "save": "#saveBtn", "past_warn": "#fPastWarn",
    "suggestions": "#suggestions li", "add_sugg": ".add-sugg", "dismiss": ".dismiss",
    "upcoming": "#upcoming li[data-id]", "toast": "#toast",
}
XSS = "<img src=x onerror=window.__pwned=1>"

results, errors = [], []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


# --- in-page store access ----------------------------------------------------------
SEED = """
(() => {
  let client;
  window.__rpcCalls = {};
  Object.defineProperty(window, "__demoDb", {
    configurable: true,
    get() { return client; },
    set(v) {
      client = v;
      const rpc = v.rpc.bind(v);
      v.rpc = (name, params) => { window.__rpcCalls[name] = (window.__rpcCalls[name] || 0) + 1; return rpc(name, params); };
      const today = new Date().toISOString().slice(0, 10);
      const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
      const S = window.__qa = {
        today, addDays,
        all(t) { const b = v.from(t); b._op = "select"; return b._run().data; },
        insert(t, rows) { const r = v.from(t).insert(rows)._run(); if (r.error) throw new Error(JSON.stringify(r.error)); },
        clear(t) { v.from(t).delete()._run(); },
        acc(name) { return S.all("accounts").find((a) => a.name === name).id; },
        cat(name, kind) { return S.all("categories").find((c) => c.name === name && c.kind === kind).id; },
        // A bill whose next due is `due` (YYYY-MM-DD), anchored `back` periods ago.
        bill(name, due, extra = {}) {
          const b = Object.assign({ name, type: "expense", amount: 100, account_id: S.acc("Cash"),
            category_id: S.cat("Bills", "expense"), frequency: "monthly", start_date: due, next_seq: 0,
            auto_post: false, active: true }, extra);
          S.insert("recurring", b);
          return S.all("recurring").find((r) => r.name === name).id;
        },
        txFor(id) { return S.all("transactions").filter((t) => t.recurring_id === id); },
        billByName(n) { return S.all("recurring").find((r) => r.name === n); },
      };
      try { (function (S) { %s })(S); } catch (e) { window.__seedError = String(e); }
    },
  });
})();
"""


def open_page(browser, path, seed_js="", width=1440, height=1000):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.add_init_script(SEED % seed_js)
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"{path}@{width} console.error: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"{path}@{width} pageerror: {e}"))
    pg.on("dialog", lambda d: d.accept() if d.type == "confirm" else
          (errors.append(f"{path}: unexpected {d.type} dialog: {d.message}"), d.dismiss()))
    pg.goto(BASE + path, wait_until="networkidle")
    pg.wait_for_timeout(700)
    err = pg.evaluate("() => window.__seedError || null")
    if err:
        errors.append(f"{path}: seed failed: {err}")
    return ctx, pg


def ev(pg, js, arg=None):
    return pg.evaluate(js, arg)


def row(pg, name):
    bid = ev(pg, "(n) => (window.__qa.billByName(n) || {}).id", name)
    return pg.locator(f'#billRows tr[data-id="{bid}"]'), bid


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def toast_text(pg):
    """Current toast text, or '' when no toast was ever shown (no 30s wait)."""
    return ev(pg, "() => (document.querySelector('#toast') || {}).textContent || ''")


def money_in(text):
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def scenario(fn):
    def run(b):
        try:
            fn(b)
        except Exception as e:
            check(False, f"{fn.__name__} crashed: {type(e).__name__}: {e}")
            traceback.print_exc()
    run.__name__ = fn.__name__
    return run


# --- scenarios -----------------------------------------------------------------------
@scenario
def layout(b):
    for path in ("/bills", "/"):
        for w in (1440, 1024, 768, 390):
            ctx, pg = open_page(b, path, width=w)
            sw = ev(pg, "() => document.documentElement.scrollWidth")
            check(sw <= w, f"no horizontal page scroll on {path} at {w}px (scrollWidth {sw})")
            if w in (1440, 390):
                shot(pg, f"s2{path.replace('/', '-') or '-dash'}-{w}.png")
            ctx.close()
    ctx, pg = open_page(b, "/bills")
    nav = ev(pg, "() => [...document.querySelectorAll('.nav a')].map(a => a.textContent.trim())")
    i_b = next((i for i, t in enumerate(nav) if t.endswith("Budgets")), -1)
    # Sprint 3 inserted Goals between Budgets and Bills in the sidebar.
    check(i_b >= 0 and i_b + 2 < len(nav) and "Goals" in nav[i_b + 1] and "Bills" in nav[i_b + 2],
          f"nav: Goals then Bills after Budgets ({nav})")
    check(ev(pg, "() => document.querySelector('.nav a[href=\"/bills\"]').classList.contains('active')"),
          "Bills nav item marked active on /bills")
    ctx.close()


@scenario
def sample_data_and_total(b):
    ctx, pg = open_page(b, "/bills")
    check(pg.locator(SEL["rows"]).count() == 4, f"4 sample bills listed ({pg.locator(SEL['rows']).count()})")
    want = ev(pg, """() => {
        const f = { weekly: 52 / 12, monthly: 1, quarterly: 1 / 3, yearly: 1 / 12 };
        return window.__qa.all('recurring').filter(r => r.active && r.type === 'expense')
            .reduce((n, r) => n + Number(r.amount) * f[r.frequency], 0); }""")
    got = money_in(pg.text_content(SEL["total"]))
    check(got is not None and abs(got - want) <= 1, f"monthly total ≈ ₹{want:.2f} (shown {pg.text_content(SEL['total'])!r})")
    # Paused and income bills do not count.
    r, _ = row(pg, "Netflix")
    r.locator(SEL["pause"]).click()
    pg.wait_for_timeout(700)
    got2 = money_in(pg.text_content(SEL["total"]))
    check(got2 is not None and abs(got2 - (want - 649)) <= 1, f"pausing Netflix drops the total by 649 ({got2})")
    ctx.close()


@scenario
def due_wording(b):
    seed = """S.clear('recurring');
      S.bill('Due in 3', S.addDays(S.today, 3)); S.bill('Overdue 2', S.addDays(S.today, -2));
      S.bill('Due in 1', S.addDays(S.today, 1)); S.bill('Overdue 1', S.addDays(S.today, -1));
      S.bill('Due today', S.today);"""
    ctx, pg = open_page(b, "/bills", seed)
    txt = lambda n: (row(pg, n)[0].text_content() or "").lower()
    check("in 3 days" in txt("Due in 3"), f"'in 3 days' ({txt('Due in 3')[:120]!r})")
    check("overdue 2 days" in txt("Overdue 2"), f"'overdue 2 days' ({txt('Overdue 2')[:120]!r})")
    check("1 days" not in txt("Due in 1") and "1 days" not in txt("Overdue 1"), "singular: never '1 days'")
    check("today" in txt("Due today") and "overdue" not in txt("Due today"), f"due today says today, not overdue ({txt('Due today')[:120]!r})")
    order = ev(pg, "() => [...document.querySelectorAll('#billRows tr[data-id]')].map(tr => tr.dataset.id)")
    by_due = ev(pg, "() => window.__qa.all('recurring').slice().sort((a, b) => a.next_due < b.next_due ? -1 : 1).map(r => r.id)")
    check(order == by_due, "bills ordered by next due")
    ctx.close()


@scenario
def row_actions(b):
    seed = """S.clear('recurring'); S.bill('Phone', S.addDays(S.today, -3), { amount: 299 });
      S.bill('Paused gym', S.addDays(S.today, -21), { frequency: 'weekly', auto_post: true, active: false });
      S.bill('Doomed', S.addDays(S.today, 5));"""
    ctx, pg = open_page(b, "/bills", seed)
    r, bid = row(pg, "Phone")
    due0 = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id).next_due", bid)
    btn = r.locator(SEL["mark_paid"])
    btn.click(); btn.click(force=True); btn.click(force=True)
    pg.wait_for_timeout(1200)
    tx = ev(pg, "(id) => window.__qa.txFor(id)", bid)
    check(len(tx) == 1, f"Mark paid (triple click) posts exactly one transaction ({len(tx)})")
    check(tx and tx[0].get("source") == "recurring" and tx[0].get("occurred_on") == ev(pg, "() => window.__qa.today"),
          "paid transaction: source 'recurring', dated today (decision 6)")
    due1 = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id).next_due", bid)
    check(due1 > due0, f"next due advances after Mark paid ({due0} -> {due1})")
    check("₹299" in (toast_text(pg) or "").replace(",", "") or "Phone" in (toast_text(pg) or ""),
          f"toast confirms the payment ({toast_text(pg)!r})")

    r, bid = row(pg, "Phone")
    r.locator(SEL["skip"]).click()
    pg.wait_for_timeout(1000)
    check(len(ev(pg, "(id) => window.__qa.txFor(id)", bid)) == 1, "Skip posts nothing")
    due2 = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id).next_due", bid)
    check(due2 > due1, f"Skip advances next due ({due1} -> {due2})")

    # Auto-post toggle.
    r, bid = row(pg, "Phone")
    r.locator(SEL["auto_visual"]).first.click()      # what a mouse user clicks (input is visually hidden)
    pg.wait_for_timeout(700)
    check(ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id).auto_post", bid) is True,
          "auto-post toggle saves")

    # Decision 14: resume skips the dates missed while paused.
    r, bid = row(pg, "Paused gym")
    check("paused" in (r.get_attribute("class") or "") or "paused" in (r.text_content() or "").lower(), "paused bill shown as paused")
    r.locator(SEL["pause"]).click()            # Resume
    pg.wait_for_timeout(800)
    g = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id)", bid)
    check(g["active"] and g["next_due"] >= ev(pg, "() => window.__qa.today"),
          f"Resume moves next due to today or later (decision 14) ({g['next_due']})")
    posted = ev(pg, """async (id) => { await window.__demoDb.rpc('post_due_recurring', {});
                         return window.__qa.txFor(id).length; }""", bid)
    check(posted <= 1, f"resume does not back-post the paused weeks ({posted} posted)")

    # Delete keeps history.
    r, bid = row(pg, "Doomed")
    ev(pg, "async (id) => window.__demoDb.rpc('mark_recurring_paid', { p_id: id })", bid)
    r.locator(SEL["delete"]).click()
    pg.wait_for_timeout(900)
    check(pg.locator(f'#billRows tr[data-id="{bid}"]').count() == 0, "Delete removes the row (after confirm)")
    check(ev(pg, "() => window.__qa.all('transactions').some(t => t.note === 'Doomed')"),
          "Delete keeps the bill's past transactions")
    shot(pg, "s2-bills-actions.png")
    ctx.close()


@scenario
def add_form(b):
    ctx, pg = open_page(b, "/bills")
    n0 = pg.locator(SEL["rows"]).count()
    # Category list follows the type.
    pg.select_option(SEL["type"], "income")
    pg.wait_for_timeout(200)
    kinds = ev(pg, """() => { const cats = window.__qa.all('categories');
        return [...document.querySelectorAll('#fCategory option')].filter(o => o.value)
               .map(o => (cats.find(c => c.id === o.value) || {}).kind); }""")
    check(kinds and all(k == "income" for k in kinds), f"income type -> only income categories ({kinds})")
    pg.select_option(SEL["type"], "expense")
    pg.wait_for_timeout(200)
    kinds = ev(pg, """() => { const cats = window.__qa.all('categories');
        return [...document.querySelectorAll('#fCategory option')].filter(o => o.value)
               .map(o => (cats.find(c => c.id === o.value) || {}).kind); }""")
    check(kinds and all(k == "expense" for k in kinds), f"expense type -> only expense categories ({kinds})")
    check(pg.input_value(SEL["start"]) == ev(pg, "() => window.__qa.today"), "start date defaults to today (decision 9)")

    # Validation: blank name, zero amount.
    pg.fill(SEL["name"], "   ")
    pg.fill(SEL["amount"], "0")
    pg.click(SEL["save"])
    pg.wait_for_timeout(500)
    check(pg.locator(SEL["rows"]).count() == n0, "blank name / zero amount adds nothing")
    check(pg.locator("#fNameErr:not([hidden])").count() == 1 and pg.locator("#fAmountErr:not([hidden])").count() == 1,
          "blank name and zero amount show field errors")

    # Decision 9: past start + auto-post warns with the right count, then posts them.
    past = ev(pg, "() => window.__qa.addDays(window.__qa.today, -21)")
    pg.fill(SEL["name"], f"Weekly EMI {XSS}")
    pg.fill(SEL["amount"], "1000")
    pg.select_option(SEL["freq"], "weekly")
    pg.fill(SEL["start"], past)
    pg.check(SEL["auto_form"])
    pg.wait_for_timeout(300)
    warn = pg.text_content(SEL["past_warn"]) or ""
    check("This will add 4 past payments now" in warn, f"past-date warning counts 4 payments (-21, -14, -7, today): {warn!r}")
    pg.click(SEL["save"])
    pg.wait_for_timeout(1500)
    check(pg.locator(SEL["rows"]).count() == n0 + 1, "valid bill is added")
    bid = ev(pg, "() => (window.__qa.all('recurring').find(r => r.name.startsWith('Weekly EMI')) || {}).id")
    check(bid and len(ev(pg, "(id) => window.__qa.txFor(id)", bid)) == 4, "the 4 past payments are posted on save")
    check(ev(pg, "() => window.__pwned === undefined") and pg.locator("main img[src=x]").count() == 0,
          "hostile bill name is escaped in the list and toast")

    # Edit amount only keeps the schedule; changing frequency reschedules (decision 12).
    r = pg.locator(f'#billRows tr[data-id="{bid}"]')
    before = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id)", bid)
    r.locator(SEL["edit"]).click()
    pg.wait_for_timeout(300)
    pg.fill(SEL["amount"], "1100")
    pg.click(SEL["save"])
    pg.wait_for_timeout(1000)
    after = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id)", bid)
    check(float(after["amount"]) == 1100 and after["start_date"] == before["start_date"]
          and after["next_seq"] == before["next_seq"], "editing the amount keeps start date and next_seq")
    r = pg.locator(f'#billRows tr[data-id="{bid}"]')
    r.locator(SEL["edit"]).click()
    pg.wait_for_timeout(300)
    pg.select_option(SEL["freq"], "monthly")
    pg.click(SEL["save"])
    pg.wait_for_timeout(1000)
    after = ev(pg, "(id) => window.__qa.all('recurring').find(r => r.id === id)", bid)
    seqs = sorted(t["recurring_seq"] for t in ev(pg, "(id) => window.__qa.txFor(id)", bid))
    check(after["frequency"] == "monthly" and after["next_seq"] == 0 and all(s < 0 for s in seqs) and len(seqs) == 4,
          f"changing frequency reschedules: next_seq 0, history kept with negative seqs ({after['next_seq']}, {seqs})")
    check(ev(pg, "() => (window.__rpcCalls.reschedule_recurring || 0) >= 1"), "frequency change uses reschedule_recurring")
    ctx.close()


@scenario
def suggestions(b):
    ctx, pg = open_page(b, "/bills")
    items = pg.locator(SEL["suggestions"])
    n = items.count()
    texts = [t.lower() for t in items.all_text_contents()]
    check(1 <= n <= 2, f"demo shows 1-2 suggestions ({n}: {texts})")
    check(not any("chai" in t for t in texts), "low-confidence 'Chai Point' is hidden (decision 2)")
    if n:
        first = items.first
        first.locator(SEL["add_sugg"]).click()
        pg.wait_for_timeout(400)
        name = pg.input_value(SEL["name"])
        check(name and not re.search(r"[\d#]", name) and name[0].isupper(),
              f"Add as bill pre-fills a tidy name (decision 3): {name!r}")
        check(float(pg.input_value(SEL["amount"]) or 0) > 0, "Add as bill pre-fills the amount")
        check(pg.input_value(SEL["freq"]) in ("weekly", "monthly", "quarterly", "yearly"), "Add as bill pre-fills frequency")
        focused = ev(pg, "() => document.activeElement && document.activeElement.closest('form') ? true : false")
        check(focused, "focus moves into the form after Add as bill")
    if n:
        before = pg.locator(SEL["suggestions"]).count()
        pg.locator(SEL["suggestions"]).first.locator(SEL["dismiss"]).click()
        pg.wait_for_timeout(400)
        check(pg.locator(SEL["suggestions"]).count() == before - 1, "Dismiss removes the suggestion")
    ctx.close()

    # Hostile note in a detectable pattern is escaped in the suggestions list.
    seed = f"""const c = S.acc('Cash'), cat = S.cat('Other', 'expense');
      for (let k = 1; k <= 4; k++) S.insert('transactions', {{ occurred_on: S.addDays(S.today, -30 * k),
        amount: 77, type: 'expense', account_id: c, to_account_id: null, category_id: cat,
        note: {json.dumps(XSS + ' club')} }});"""
    ctx, pg = open_page(b, "/bills", seed)
    check(ev(pg, "() => window.__pwned === undefined") and pg.locator("main img[src=x]").count() == 0,
          "hostile transaction note is escaped in suggestions")
    ctx.close()


@scenario
def dashboard_card(b):
    seed = """S.clear('recurring');
      S.bill('Card overdue', S.addDays(S.today, -4)); S.bill('Card in 6', S.addDays(S.today, 6));
      S.bill('Card in 9', S.addDays(S.today, 9)); S.bill('Card paused', S.addDays(S.today, 1), { active: false });
      S.bill(""" + json.dumps(XSS) + """, S.addDays(S.today, 2));"""
    ctx, pg = open_page(b, "/", seed)
    names = [t for t in pg.locator(SEL["upcoming"]).all_text_contents()]
    joined = " ".join(names)
    check("Card overdue" in joined and "Card in 6" in joined, f"card lists overdue and next-7-day bills ({len(names)})")
    check("Card in 9" not in joined and "Card paused" not in joined, "card hides bills due after 7 days and paused bills")
    check(ev(pg, "() => window.__pwned === undefined") and pg.locator("main img[src=x]").count() == 0,
          "hostile bill name escaped on the dashboard")
    bid = ev(pg, "() => window.__qa.billByName('Card overdue').id")
    pg.locator(f'#upcoming li[data-id="{bid}"] {SEL["mark_paid"]}').click()
    pg.wait_for_timeout(1200)
    check(len(ev(pg, "(id) => window.__qa.txFor(id)", bid)) == 1, "Mark paid from the dashboard posts one transaction")
    check(ev(pg, "() => (window.__rpcCalls.post_due_recurring || 0)") == 1, "dashboard: post_due_recurring called once per load")
    shot(pg, "s2-dashboard.png")
    ctx.close()


@scenario
def shell_post_due(b):
    seed = """S.clear('recurring');
      S.bill('Auto weekly', S.addDays(S.today, -14), { frequency: 'weekly', auto_post: true });"""
    for path in ("/", "/bills", "/transactions", "/accounts", "/budgets", "/reports", "/imports"):
        ctx, pg = open_page(b, path, seed)
        pg.wait_for_timeout(300)
        calls = ev(pg, "() => window.__rpcCalls.post_due_recurring || 0")
        check(calls == 1, f"{path}: post_due_recurring called exactly once on load ({calls})")
        if path == "/":
            t = toast_text(pg) or ""
            check("Posted 3 bills" in t, f"toast 'Posted 3 bills' (14 and 7 days ago, today): {t!r}")
            bid = ev(pg, "() => window.__qa.billByName('Auto weekly').id")
            check(len(ev(pg, "(id) => window.__qa.txFor(id)", bid)) == 3, "3 occurrences posted, each once")
        ctx.close()
    ctx, pg = open_page(b, "/", "S.clear('recurring');")
    check("Posted" not in (toast_text(pg) or ""), "no 'Posted' toast when nothing was due")
    ctx.close()


@scenario
def keyboard_and_names(b):
    ctx, pg = open_page(b, "/bills")
    bad = ev(pg, """() => {
        const out = [];
        document.querySelectorAll('#billRows tr[data-id]').forEach(tr => {
          const nm = window.__qa.all('recurring').find(r => r.id === tr.dataset.id).name;
          tr.querySelectorAll('button, input, select').forEach(el => {
            const label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].textContent) || el.textContent;
            if (!label || !label.includes(nm)) out.push(nm + ': ' + el.className + ' -> ' + label);
          });
        });
        return out; }""")
    check(not bad, f"every row control names its bill for screen readers ({bad[:4]})")
    r = pg.locator(SEL["rows"]).first
    bid = r.get_attribute("data-id")
    r.locator(SEL["mark_paid"]).focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(1200)
    check(len(ev(pg, "(id) => window.__qa.txFor(id)", bid)) == 1, "Mark paid works from the keyboard (Enter)")
    active = ev(pg, "() => document.activeElement && document.activeElement !== document.body")
    check(active, "focus stays somewhere sensible after Mark paid (not lost to <body>)")
    ctx.close()


@scenario
def phone(b):
    ctx, pg = open_page(b, "/bills", width=390, height=900)
    vis = ev(pg, """() => [...document.querySelectorAll('#billRows tr[data-id]')].map(tr => {
        const ok = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.left >= 0 && r.right <= innerWidth; };
        const amt = [...tr.querySelectorAll('td')].find(td => /₹/.test(td.textContent));
        return { name: tr.textContent.trim().slice(0, 20), cells: ok(tr.querySelector('td')), amount: ok(amt),
                 pay: ok(tr.querySelector('.mark-paid')), skip: ok(tr.querySelector('.skip')) }; })""")
    bad = [v for v in vis if not (v["cells"] and v["amount"] and v["pay"] and v["skip"])]
    check(vis and not bad, f"390px: name, amount, Mark paid and Skip on screen for every bill ({bad[:2]})")
    small = ev(pg, """() => [...document.querySelectorAll('#billRows .mark-paid, #billRows .skip')]
        .map(e => e.getBoundingClientRect()).filter(r => r.height < 32 || r.width < 32).length""")
    check(small == 0, f"390px: Mark paid / Skip are at least 32px touch targets ({small} too small)")
    ctx.close()
    ctx, pg = open_page(b, "/", width=390, height=900)
    ok = ev(pg, """() => [...document.querySelectorAll('#upcoming li[data-id] .mark-paid')]
        .every(e => { const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; })""")
    check(ok, "390px: dashboard card Mark paid buttons on screen")
    ctx.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        for sc in (layout, sample_data_and_total, due_wording, row_actions, add_form, suggestions,
                   dashboard_card, shell_post_due, keyboard_and_names, phone):
            print(f"-- {sc.__name__}")
            sc(b)
        b.close()

    print("\n=== SPRINT 2 UI GATE ===")
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
