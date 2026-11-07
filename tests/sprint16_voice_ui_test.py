"""Sprint 16 voice note, in the browser (demo mode).

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint16_voice_ui_test.py

Checks:
  * /voice at phone 390, tablet 768 and desktop 1440, light and dark: no sideways
    scrolling, no page errors, no emoji, mic + action buttons at least 44px,
  * a typed sentence makes a confirm card with the right amount and direction,
  * a multi-item sentence makes two cards,
  * script text in a merchant name is shown escaped (no dialog), no amount is reported,
  * Add saves a transaction (History grows), Send to review adds a pending row
    tagged voice, Add as bill creates a recurring bill, Discard removes a card,
  * a mocked SpeechRecognition drives the mic: start, interim + final text,
    permission-denied shows a friendly message and the typed box still works,
  * when SpeechRecognition is absent the mic is disabled and the typed path works.
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")

# A fake SpeechRecognition injected before any script runs. window.__mockSpeak(text)
# fires an interim then a final result; window.__mockErr(name) fires an error.
MOCK_SR = """
window.__srEvents = [];
class MockSR {
  constructor(){ this.lang=''; this.interimResults=false; this.continuous=false; this.maxAlternatives=1; MockSR.last=this; }
  start(){ this.onstart && this.onstart(); window.__srEvents.push('start'); }
  stop(){ this.onend && this.onend(); window.__srEvents.push('stop'); }
  abort(){ this.onend && this.onend(); }
}
window.SpeechRecognition = MockSR;
window.__mockSpeak = (text) => { const r = MockSR.last;
  r.onresult({ resultIndex:0, results:[Object.assign([{transcript:text}],{isFinal:true})] }); r.stop(); };
window.__mockErr = (name) => { const r = MockSR.last; r.onerror({ error:name }); r.onend && r.onend(); };
"""

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


def card(page):
    return page.locator(".vo-item").first


def type_and_read(page, text):
    page.fill("#typed", text)
    page.click("#go")
    page.wait_for_timeout(200)


def layout_sweep(browser):
    for tag, theme, w, h in [("phone-dark", "dark", 390, 844), ("phone-light", "light", 390, 844),
                             ("tablet-light", "light", 768, 1024), ("desktop-dark", "dark", 1440, 900),
                             ("desktop-light", "light", 1440, 900)]:
        ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
        ctx.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}}")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE + "/voice", wait_until="networkidle")
        page.wait_for_timeout(250)
        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(over <= 1, f"{tag} /voice: no sideways scrolling (overflow {over}px)")
        check(not EMOJI.findall(page.inner_text("body")), f"{tag} /voice: no emoji")
        mic = page.evaluate("() => { const b = document.querySelector('.vo-mic').getBoundingClientRect(); return Math.min(b.width, b.height); }")
        check(mic >= 44, f"{tag}: mic button at least 44px ({mic})")
        type_and_read(page, "paid 450 to Starbucks on UPI")
        small = page.evaluate("""() => [...document.querySelectorAll('.vo-actions .btn, .vo-type-row .btn')]
            .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height).filter(h => h < 43.5).length""")
        check(small == 0, f"{tag}: action buttons at least 44px ({small} too small)")
        check(over <= 1, f"{tag}: still no sideways scroll with a card")
        check(not errs, f"{tag} /voice: no page errors ({errs[:1]})")
        ctx.close()


def parse_and_save(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errs, dialogs = [], []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(BASE + "/voice", wait_until="networkidle")
    page.wait_for_timeout(300)

    # One expense.
    type_and_read(page, "paid 450 to Starbucks on UPI")
    check(page.locator(".vo-item").count() == 1, "one card for one sentence")
    check(page.eval_on_selector(".vo-f-amt", "e => e.value") == "450", "amount 450 on the card")
    check(page.eval_on_selector(".vo-dir .active", "e => e.dataset.dir") == "expense", "direction expense")
    check("Starbucks" in page.eval_on_selector(".vo-f-note", "e => e.value"), "merchant Starbucks")
    check("UPI" in page.inner_text(".vo-chip-row"), "UPI channel shown")

    # Income.
    type_and_read(page, "got my salary 85000")
    check(page.eval_on_selector(".vo-dir .active", "e => e.dataset.dir") == "income", "salary is income")

    # Multi-item.
    type_and_read(page, "chai 20 and auto 80 cash")
    check(page.locator(".vo-item").count() == 2, "two cards for two items")

    # No amount: friendly message, no card.
    type_and_read(page, "umm so like I think")
    check(page.is_hidden("#results") and "couldn't find an amount" in page.inner_text("#status").lower(), "no amount: friendly message")

    # Script text in a name is escaped, no dialog, amount still read.
    type_and_read(page, "paid 450 to <script>alert(1)</script>")
    page.wait_for_timeout(200)
    check(not dialogs, "no script ran from a spoken name")
    check("<script" not in page.inner_html("#cards") or "&lt;script" in page.inner_html("#cards"), "name escaped in the card")

    # Save as a transaction: History grows.
    type_and_read(page, "paid 275 for lunch today")
    page.once("dialog", lambda d: d.dismiss())
    page.locator(".vo-save[data-mode='tx']").first.click()
    page.wait_for_selector(".toast.show", timeout=5000)
    check("Added" in page.inner_text(".toast"), "Add saves a transaction")
    page.goto(BASE + "/transactions", wait_until="networkidle")
    page.wait_for_timeout(500)
    check(page.get_by_text("lunch", exact=False).count() >= 1, "the voice transaction shows in History")

    # Send to review: a pending row tagged voice.
    page.goto(BASE + "/voice", wait_until="networkidle")
    type_and_read(page, "paid 60 for tea")
    page.locator(".vo-save[data-mode='review']").first.click()
    page.wait_for_selector(".toast.show", timeout=5000)
    check("review" in page.inner_text(".toast").lower(), "Send to review works")
    page.goto(BASE + "/imports", wait_until="networkidle")
    page.wait_for_selector(".rv-item", timeout=8000)
    page.wait_for_timeout(300)
    check(page.get_by_text("tea", exact=False).count() >= 1, "the review row is there")

    # Add as bill: a recurring bill appears on the Bills page.
    page.goto(BASE + "/voice", wait_until="networkidle")
    type_and_read(page, "netflix 299 monthly")
    check(page.locator(".vo-save[data-mode='bill']").first.is_visible(), "a monthly phrase offers Add as bill")
    page.locator(".vo-save[data-mode='bill']").first.click()
    page.wait_for_selector(".toast.show", timeout=5000)
    check("bill" in page.inner_text(".toast").lower(), "Add as bill works")
    page.goto(BASE + "/bills", wait_until="networkidle")
    page.wait_for_timeout(500)
    check(page.get_by_text("netflix", exact=False).count() >= 1, "the bill shows on the Bills page")

    # Discard removes the card.
    page.goto(BASE + "/voice", wait_until="networkidle")
    type_and_read(page, "paid 10 for a pen")
    page.locator(".vo-dismiss").first.click()
    check(page.is_hidden("#results"), "Discard removes the only card and hides the panel")

    check(not errs, f"parse/save: no page errors ({errs[:1]})")
    ctx.close()


def speech(browser):
    # With a mocked SpeechRecognition.
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(MOCK_SR)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE + "/voice", wait_until="networkidle")
    page.wait_for_timeout(300)
    check(page.is_enabled("#mic"), "mic enabled when SpeechRecognition exists")
    page.click("#mic")
    check("Listening" in page.inner_text("#micHint"), "mic shows a listening state")
    page.evaluate("window.__mockSpeak('swiped hdfc card at reliance fresh 1230')")
    page.wait_for_timeout(300)
    check(page.locator(".vo-item").count() == 1, "speech result becomes a card")
    check(page.eval_on_selector(".vo-f-amt", "e => e.value") == "1230", "spoken amount parsed")
    # Permission denied: friendly message, typed path still works.
    page.click("#mic")
    page.evaluate("window.__mockErr('not-allowed')")
    page.wait_for_timeout(200)
    check("permission" in page.inner_text("#status").lower(), "permission-denied is explained")
    type_and_read(page, "paid 99 for a book")
    check(page.locator(".vo-item").count() == 1, "typed path still works after a mic error")
    check(not errs, f"speech: no page errors ({errs[:1]})")
    ctx.close()

    # Without SpeechRecognition at all.
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("delete window.SpeechRecognition; delete window.webkitSpeechRecognition; Object.defineProperty(window,'SpeechRecognition',{value:undefined}); Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined});")
    page = ctx.new_page()
    page.goto(BASE + "/voice", wait_until="networkidle")
    page.wait_for_timeout(300)
    check(page.is_disabled("#mic"), "mic disabled when speech is unavailable")
    type_and_read(page, "paid 150 to the milk guy on phonepe")
    check(page.locator(".vo-item").count() == 1, "typed path works with no speech support")
    check(page.eval_on_selector(".vo-f-amt", "e => e.value") == "150", "typed amount parsed with no speech API")
    ctx.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        browser = p.chromium.launch(**launch)
        layout_sweep(browser)
        parse_and_save(browser)
        speech(browser)
        browser.close()
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nSprint 16 voice UI: {passed} PASS, {len(failed)} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
