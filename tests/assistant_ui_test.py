"""Assistant (Ask) browser checks, demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/assistant_ui_test.py

At phone, tablet and desktop width, day and night:
  * the chat fits (no sideways scroll), has no page errors and 44px controls on phone,
  * a question runs the real tool loop: server asks for a tool, the browser runs it
    on the person's data, the answer is drawn,
  * an attached statement becomes a proposal, and only reaches To review on confirm;
    Discard adds nothing; importing the same file twice adds nothing new,
  * each data tool returns correct figures from the demo data,
  * text from Claude and from the file is escaped (no script runs),
  * a server without the assistant (503) falls back to basic on-device answers,
  * a server error is shown and the next question still works.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
HERE = os.path.dirname(__file__)
CSV = os.path.join(HERE, "fixtures", "import", "kotak-style.csv")
SIZES = {"phone": (390, 844), "tablet": (768, 1024), "desktop": (1440, 900)}

results = []


def check(cond, msg):
    results.append(bool(cond))
    if not cond:
        print("  [FAIL]", msg)


def open_ask(b, size="desktop", theme="light"):
    w, h = SIZES[size]
    ctx = b.new_context(viewport={"width": w, "height": h}, service_workers="block")
    ctx.add_init_script(f"try {{ localStorage.setItem('theme', '{theme}'); }} catch (e) {{}}")
    pg = ctx.new_page()
    pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(str(e)))
    pg.goto(BASE + "/ask", wait_until="networkidle")
    pg.wait_for_timeout(400)
    return ctx, pg


def pending(pg):
    return pg.evaluate("async () => (await window.__demoDb.from('pending_imports').select('id').eq('status', 'pending')).data.length")


def ask(pg, text):
    pg.fill("#q", text)
    pg.click("#send")
    pg.wait_for_function("() => !document.querySelector('.msg.waiting') && !document.getElementById('send').disabled", timeout=20000)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME) if os.path.exists(CHROME) else p.chromium.launch()

        # ---- layout: every size, both themes -------------------------------------
        for size in SIZES:
            for theme in ("light", "dark"):
                tag = f"{size} {theme}"
                ctx, pg = open_ask(b, size, theme)
                check(pg.locator(".chat-chip").count() >= 4, f"{tag}: starter questions shown")
                ask(pg, "What's outstanding right now?")
                answer = pg.inner_text(".msg.from-bot")
                check("₹" in answer and "overdue" in answer, f"{tag}: question answered from the snapshot ({answer[:60]!r})")
                check(pg.locator(".msg.from-me").count() == 1, f"{tag}: the question is shown")
                over = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                check(over <= 1, f"{tag}: no sideways scroll ({over}px)")
                check(not pg.errs, f"{tag}: no page errors ({pg.errs[:1]})")
                if size == "phone":
                    for sel in ("#send", "#attach"):
                        box = pg.locator(sel).bounding_box()
                        check(box["height"] >= 44 and box["width"] >= 44, f"{tag}: {sel} is a 44px target ({box['width']:.0f}x{box['height']:.0f})")
                    bar = pg.locator(".chat-row").bounding_box()
                    tab = pg.locator(".tabbar").bounding_box()
                    check(bar["y"] + bar["height"] <= tab["y"] + 1, f"{tag}: composer sits above the tab bar")
                ctx.close()

        # ---- file -> proposal -> review list ----------------------------------------
        ctx, pg = open_ask(b)
        before = pending(pg)
        pg.set_input_files("#file", CSV)
        check(pg.is_visible("#fileChip") and "kotak-style.csv" in pg.inner_text("#fileName"), "file: attached file is named")
        check(pg.input_value("#q") != "", "file: a default request is filled in")
        pg.click("#send")
        pg.wait_for_selector(".msg.proposal", timeout=20000)
        pg.wait_for_function("() => !document.querySelector('.msg.waiting')", timeout=20000)
        n = int(pg.inner_text(".msg.proposal .btn").split()[1])
        check(n == 12, f"file: proposal holds the 12 rows of the statement ({n})")
        mid = pending(pg)
        check(mid == before, "file: nothing reaches To review before confirming")
        check("review" in pg.locator(".msg.from-bot").last.inner_text().lower(), "file: the reply says review, not added")
        pg.click(".msg.proposal .btn")
        pg.wait_for_function("() => document.querySelector('.msg.proposal [role=status]').textContent.includes('waiting')", timeout=10000)
        after = pending(pg)
        check(after == before + 12, f"file: confirming adds 12 to To review ({before} -> {after})")
        check(pg.locator(".msg.proposal .btn").count() == 0, "file: buttons go away once added")
        # Same file again: de-duplicated.
        pg.evaluate("window.__assistant.ctx.onProposal({ rows: window.__assistant.ctx.mapped, label: 'again' })")
        pg.locator(".msg.proposal").last.locator(".btn").first.click()
        pg.wait_for_function("() => [...document.querySelectorAll('.msg.proposal [role=status]')].pop().textContent.includes('already imported')", timeout=10000)
        again = pending(pg)
        check(again == after, "file: the same rows a second time add nothing")
        # Discard.
        pg.evaluate("window.__assistant.ctx.onProposal({ rows: [{date:'2026-01-02', amount: 5, direction:'expense', note:'x', dedupe:'d1'}], label: 'drop me' })")
        pg.locator(".msg.proposal").last.locator(".btn.ghost").click()
        check("Nothing was added" in pg.locator(".msg.proposal").last.inner_text(), "file: Discard says nothing was added")
        check(pending(pg) == after,
              "file: Discard adds nothing")
        check(not pg.errs, f"file: no page errors ({pg.errs[:1]})")

        # ---- the data tools, against the demo data ------------------------------------
        tools = pg.evaluate("""async () => {
          const m = await import('/static/js/assistant.js');
          const c = window.__assistant.ctx, db = c.db, month = c.today.slice(0, 7);
          const snap = await m.runTool(c, 'get_snapshot', {});
          const bal = (await db.rpc('account_balances')).data;
          const sum = (await db.rpc('dashboard_summary', { p_month: month })).data[0];
          const food = await m.runTool(c, 'search_transactions', { category: 'food', from_date: month + '-01', to_date: c.today });
          const all = await m.runTool(c, 'search_transactions', { limit: 3 });
          const big = await m.runTool(c, 'search_transactions', { min_amount: 10000, type: 'expense' });
          const byCat = await m.runTool(c, 'summarize_spending', { from_date: month + '-01', to_date: c.today, group_by: 'category' });
          const byMonth = await m.runTool(c, 'summarize_spending', { from_date: '2000-01-01', to_date: c.today, group_by: 'month', type: 'income' });
          let unknown = null, badGroup = null, noRows = null;
          try { await m.runTool(c, 'delete_everything', {}); } catch (e) { unknown = e.message; }
          try { await m.runTool(c, 'summarize_spending', { from_date: 'x', to_date: 'y', group_by: 'colour' }); } catch (e) { badGroup = e.message; }
          let shown = null; const c2 = { ...c, onProposal: (p) => { shown = p; } };
          try { await m.runTool(c2, 'propose_transactions', { rows: [{ date: 'yesterday', amount: 5, direction: 'expense', description: 'x' }, { date: '2026-01-01', amount: -0, direction: 'expense', description: 'y' }] }); } catch (e) { noRows = e.message; }
          const ok = await m.runTool(c2, 'propose_transactions', { label: 'Typed', rows: [
            { date: '2026-11-01', amount: -450.129, direction: 'expense', description: 'Lunch', category: 'Food' },
            { date: '2026-11-01', amount: 450.13, direction: 'expense', description: 'Lunch' },
            { date: '2026-11-02', amount: 90000, direction: 'salary', description: 'Pay' }] });
          return { snap, bal, sum, food, all, big, byCat, byMonth, unknown, badGroup, noRows, ok, shown };
        }""")
        s = tools["snap"]
        check(len(s["accounts"]) == len(tools["bal"]) and abs(s["total_balance"] - sum(float(a["balance"]) for a in tools["bal"])) < 0.01,
              "tools: snapshot balances match the accounts")
        check(abs(s["this_month"]["spent"] - float(tools["sum"]["month_expense"])) < 0.01, "tools: snapshot spending matches Home")
        check(any(x["overdue"] for x in s["bills"]) and all("next_due" in x for x in s["bills"]), "tools: snapshot lists bills with overdue ones")
        check(len(s["budgets"]) >= 3 and all(abs(x["limit"] - x["spent"] - x["left"]) < 0.01 for x in s["budgets"]), "tools: budgets add up")
        check(s["net_worth"] and abs(s["net_worth"]["assets"] - s["net_worth"]["liabilities"] - s["net_worth"]["net"]) < 0.01, "tools: net worth adds up")
        check(s["waiting_for_review"] >= 6 and len(s["investments"]) >= 3 and len(s["goals"]) >= 1, "tools: review count, investments and goals present")
        f = tools["food"]
        check(f["matches"] >= 2 and all(r["category"] == "Food" for r in f["rows"]), "tools: category filter is exact and case-blind")
        check(abs(f["total_out"] - sum(r["amount"] for r in f["rows"])) < 0.01, "tools: search totals equal the rows")
        check(tools["all"]["shown"] == 3 and tools["all"]["matches"] > 3 and len(tools["all"]["rows"]) == 3, "tools: limit cuts rows but not the count")
        check(tools["big"]["matches"] >= 1 and all(r["amount"] >= 10000 and r["type"] == "expense" for r in tools["big"]["rows"]), "tools: amount and type filters")
        bc = tools["byCat"]
        check(abs(bc["total"] - sum(g["total"] for g in bc["groups"])) < 0.01 and bc["groups"] == sorted(bc["groups"], key=lambda g: -g["total"]),
              "tools: spending by category sums and sorts")
        check(abs(bc["total"] - s["this_month"]["spent"]) < 0.01, "tools: category total equals the month's spending")
        bm = tools["byMonth"]
        check(bm["type"] == "income" and [g["name"] for g in bm["groups"]] == sorted(g["name"] for g in bm["groups"]), "tools: by month is in date order")
        check("Unknown tool" in (tools["unknown"] or ""), "tools: an unknown tool is refused")
        check("group_by" in (tools["badGroup"] or ""), "tools: a bad group_by is refused")
        check("No valid rows" in (tools["noRows"] or ""), "tools: rows without a real date or amount are dropped")
        sh = tools["shown"]["rows"]
        check(tools["ok"]["proposed"] == 3 and sh[0]["amount"] == 450.13 and sh[2]["direction"] == "expense",
              "tools: amounts made positive and rounded, unknown direction becomes expense")
        check(sh[0]["dedupe"] != sh[1]["dedupe"], "tools: two identical rows in one batch stay distinct")
        ctx.close()

        # ---- hostile text is escaped -----------------------------------------------------
        ctx, pg = open_ask(b)
        evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'
        pg.route("**/api/assistant", lambda r: r.fulfill(status=200, content_type="application/json",
                 body=json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": evil + "\n\nsecond paragraph"}]})))
        ask(pg, evil)
        pg.evaluate(f"window.__assistant.ctx.onProposal({{ rows: [{{date:'2026-01-02', amount: 5, direction:'expense', note:{json.dumps(evil)}, dedupe:'e'}}], label: {json.dumps(evil)} }})")
        pg.wait_for_timeout(300)
        check(pg.evaluate("window.__pwned") is None, "escape: script in a question, a reply and a file row never runs")
        check(pg.locator(".chat-log img, .chat-log script").count() == 0, "escape: no injected elements")
        check(pg.locator(".msg.from-bot p").count() == 2, "escape: paragraphs still drawn")
        ctx.close()

        # ---- server without the assistant: basic answers -----------------------------------
        ctx, pg = open_ask(b)
        pg.route("**/api/assistant", lambda r: r.fulfill(status=503, content_type="application/json",
                 body=json.dumps({"error": "off", "unavailable": True})))
        ask(pg, "what is my balance")
        check("basic answer" in pg.inner_text(".msg.note"), "fallback: says it is a basic answer")
        check("₹" in pg.locator(".msg.from-bot").last.inner_text(), "fallback: the on-device engine answers")
        ask(pg, "how much did I spend on food this month")
        check(pg.locator(".msg.note").count() == 1 and "₹" in pg.locator(".msg.from-bot").last.inner_text(), "fallback: stays in basic mode without repeating the note")
        ctx.close()

        # ---- a failed turn is shown and does not poison the chat -----------------------------
        ctx, pg = open_ask(b)
        state = {"fail": True}

        def flaky(route):
            if state["fail"]:
                route.fulfill(status=502, content_type="application/json", body=json.dumps({"error": "The assistant couldn't answer just now. Please try again."}))
            else:
                route.continue_()
        pg.route("**/api/assistant", flaky)
        ask(pg, "what do I owe")
        check("couldn't answer" in pg.inner_text(".msg.note.err"), "error: the server's message is shown")
        check(pg.evaluate("window.__assistant.history.length") == 0, "error: the failed turn is dropped from the conversation")
        state["fail"] = False
        ask(pg, "what do I owe")
        check("₹" in pg.locator(".msg.from-bot").last.inner_text(), "error: the next question works")
        sent = pg.evaluate("window.__assistant.history.map(m => m.role).join(',')")
        check(sent == "user,assistant,user,assistant", f"error: conversation alternates correctly ({sent})")
        ctx.close()
        b.close()

    print(f"assistant UI: {sum(results)} passed, {len(results) - sum(results)} failed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
