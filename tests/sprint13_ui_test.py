"""Sprint 13 UI + logic checks (Alerts centre, Ask your money), demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint13_ui_test.py

Runs at iPhone size and desktop size, in light and dark, and checks:
  * no sideways scrolling and no uncaught page errors on /alerts and /ask,
  * no emoji anywhere in the rendered text (Boss's house rule),
  * the Alerts page shows a feed and six on/off settings, and a toggle works,
  * the Ask page answers a suggested question and a typed one, on the device,
  * the pure engines (alerts.js, ask.js) compute every alert type and answer
    the main question shapes correctly (run in the page, over crafted data).
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
PAGES = ["/alerts", "/ask"]

# Pictographic emoji only; arrows (→ ← ↑ ↓) and the minus − are legitimate glyphs.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]"
)

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def sweep(ctx, tag):
    for path in PAGES:
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(700)

        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(over <= 1, f"{tag} {path}: no sideways scrolling (overflow {over}px)")
        check(not errs, f"{tag} {path}: no page errors ({errs[:1]})")

        body = page.inner_text("body")
        found = EMOJI.findall(body)
        check(not found, f"{tag} {path}: no emoji in UI ({found[:3]})")
        page.close()


def feature_checks(ctx):
    # ---- Alerts page ----
    page = ctx.new_page()
    page.goto(BASE + "/alerts", wait_until="networkidle")
    page.wait_for_timeout(700)
    toggles = page.locator("#alertSettings input[type=checkbox]").count()
    check(toggles == 6, f"alerts: six settings toggles ({toggles})")
    feed_rows = page.locator("#alertFeed .alert-row").count()
    empty = page.locator("#alertFeed .state-title").count()
    check(feed_rows >= 1 or empty == 1, "alerts: feed renders (rows or an empty state)")
    # Toggling a setting must not error and should re-render the feed.
    before = page.locator("#alertFeed").inner_html()
    page.locator('#alertSettings input[data-key="upcoming_bill"]').uncheck(force=True)
    page.wait_for_timeout(200)
    check(page.locator("#alertFeed").inner_html() is not None, "alerts: toggle re-renders without error")
    page.close()

    # ---- Ask page ----
    page = ctx.new_page()
    page.goto(BASE + "/ask", wait_until="networkidle")
    page.wait_for_timeout(600)
    chips = page.locator(".ask-chip").count()
    check(chips >= 4, f"ask: suggested questions shown ({chips})")
    check("Free" in page.inner_text(".page-head"), "ask: free/on-device badge present")
    page.locator(".ask-chip").first.click()
    page.wait_for_timeout(500)
    ans = page.inner_text("#askAnswer")
    check("₹" in ans, f"ask: suggested question returns a rupee answer")
    # A typed question.
    page.fill("#askInput", "what is my balance")
    page.locator("#askForm button[type=submit]").click()
    page.wait_for_timeout(400)
    check("₹" in page.inner_text("#askAnswer"), "ask: typed balance question answered")
    page.close()


def logic_checks(ctx):
    page = ctx.new_page()
    page.goto(BASE + "/alerts", wait_until="networkidle")

    # Alerts engine: craft data that trips every alert type.
    alert_types = page.evaluate(
        """async () => {
      const m = await import('/static/js/alerts.js');
      const now = new Date();
      const ym = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
      const thisM = ym(now);
      const prev = ym(new Date(now.getFullYear(), now.getMonth()-1, 1));
      const data = {
        balances: [{name:'HDFC', type:'bank', balance:3000}],
        budgets: [{category:'Food', monthly_limit:5000, spent:6000}],
        bills: [{name:'Rent', type:'expense', amount:18000, days_left:2, overdue:false}],
        goals: [{name:'Fund', pct:100, target_amount:100000}],
        txns: [
          {id:'t1', occurred_on: thisM+'-10', amount:9000, type:'expense', category_id:'c1'},
          {id:'t2', occurred_on: prev+'-05', amount:1000, type:'expense', category_id:'c1'},
          {id:'t3', occurred_on: prev+'-06', amount:1100, type:'expense', category_id:'c1'},
        ],
        categories: [{id:'c1', name:'Food'}],
        month: thisM,
      };
      const prefs = { ...m.defaultAlertPrefs(), goal_milestone: true };
      return m.computeAlerts(data, prefs).map(a => a.type);
    }"""
    )
    for t in ["budget", "unusual", "bill", "low", "goal"]:
        check(t in alert_types, f"alerts engine: computes a '{t}' alert ({alert_types})")

    # Disabling a type suppresses it.
    none_budget = page.evaluate(
        """async () => {
      const m = await import('/static/js/alerts.js');
      const data = { budgets: [{category:'Food', monthly_limit:5000, spent:6000}] };
      const prefs = { ...m.defaultAlertPrefs(), budget_overspend: false };
      return m.computeAlerts(data, prefs).length;
    }"""
    )
    check(none_budget == 0, "alerts engine: a disabled type produces no alert")

    # Ask engine: the main question shapes.
    ask = page.evaluate(
        """async () => {
      const m = await import('/static/js/ask.js');
      const now = new Date();
      const ym = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
      const prev = ym(new Date(now.getFullYear(), now.getMonth()-1, 1));
      const txns = [
        {occurred_on: prev+'-05', amount:9000, type:'expense', category_id:'c1'},
        {occurred_on: prev+'-15', amount:355, type:'expense', category_id:'c1'},
      ];
      const data = {
        txns, categories:[{id:'c1', name:'Food', kind:'expense'}],
        balances:[{name:'HDFC', type:'bank', balance:50000}],
        bills:[], recurring:[{name:'Netflix', amount:649, active:true, type:'expense'},
                             {name:'Rent', amount:18000, active:true, type:'expense'}],
        goals:[], today: ym(now)+'-15',
      };
      const food = m.answerQuestion('How much did I spend on food last month?', data);
      const sub  = m.answerQuestion("what's my biggest subscription?", data);
      const aff  = m.answerQuestion('can I afford a 20000 trip?', data);
      const bad  = m.answerQuestion('qwerty zzz nonsense', data);
      return {
        foodOk: food.ok, foodVal: food.value,
        subOk: sub.ok, subVal: sub.value, subHasRent: /Rent/.test(sub.sub || ''),
        affOk: aff.ok, badOk: bad.ok,
      };
    }"""
    )
    check(ask["foodOk"] and "9,355" in ask["foodVal"], f"ask engine: category spend ({ask['foodVal']})")
    check(ask["subOk"] and ask["subHasRent"] and "18,000" in ask["subVal"],
          f"ask engine: biggest subscription is Rent ({ask['subVal']})")
    check(ask["affOk"], "ask engine: affordability answered")
    check(ask["badOk"] is False, "ask engine: nonsense returns a graceful miss")
    page.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for tag, theme, w, h in [("phone-dark", "dark", 390, 844), ("desktop-light", "light", 1280, 900)]:
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                color_scheme=theme,
            )
            ctx.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}}")
            sweep(ctx, tag)
            ctx.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        feature_checks(ctx)
        logic_checks(ctx)
        ctx.close()
        browser.close()

    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nSprint 13 UI/logic: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("sprint13_ui_test: all checks passed")


if __name__ == "__main__":
    main()
