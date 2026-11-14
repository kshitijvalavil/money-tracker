"""Sprint 14: the tightened Content-Security-Policy really works (browser, demo mode).

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint14_csp_test.py

  * every page of the app loads with ZERO CSP violations (no blocked inline script,
    no blocked outside host), phone and desktop,
  * the self-hosted OCR engine (WebAssembly) starts and reads real text from an image
    under that policy, using only files from our own origin,
  * an injected inline script (no nonce) is blocked, an injected outside script is blocked.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")
PAGES = ["/", "/login", "/transactions", "/accounts", "/budgets", "/bills", "/calendar", "/goals", "/investments",
         "/net-worth", "/forecast", "/insights", "/alerts", "/ask", "/imports", "/profile",
         "/privacy", "/offline", "/deploying"]
results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  [FAIL]", msg)


WATCH = """() => { window.__v = []; document.addEventListener('securitypolicyviolation', (e) =>
  window.__v.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline'))); }"""


def sweep(ctx, tag):
    for path in PAGES:
        page = ctx.new_page()
        msgs = []
        page.on("console", lambda m: msgs.append(m.text) if "Content Security Policy" in m.text or "Refused to" in m.text else None)
        page.add_init_script("window.__v = []; document.addEventListener('securitypolicyviolation', (e) => window.__v.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline')));")
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(500)
        v = page.evaluate("window.__v")
        check(not v and not msgs, f"{tag} {path}: no CSP violations ({(v or msgs)[:2]})")
        page.close()


def ocr_and_injection(ctx):
    page = ctx.new_page()
    page.add_init_script("window.__v = []; document.addEventListener('securitypolicyviolation', (e) => window.__v.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline')));")
    page.goto(BASE + "/bills", wait_until="networkidle")
    hosts = []
    page.on("request", lambda r: hosts.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") and not r.url.startswith("blob:") else None)

    # 1) Real OCR under the policy: draw words on a canvas and read them back.
    page.add_script_tag(url="/static/vendor/tesseract/tesseract.min.js")
    page.wait_for_timeout(300)
    text = page.evaluate("""async () => {
      const c = document.createElement('canvas'); c.width = 900; c.height = 240;
      const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, 900, 240);
      g.fillStyle = '#000'; g.font = 'bold 64px Arial';
      g.fillText('ELECTRICITY BILL', 30, 90); g.fillText('Amount Rs 1234', 30, 190);
      const base = location.origin + '/static/vendor/tesseract';
      const w = await Tesseract.createWorker('eng', 1, { workerPath: base + '/worker.min.js', corePath: base, langPath: base + '/lang' });
      try { const r = await w.recognize(c.toDataURL('image/png')); return r.data.text; }
      finally { await w.terminate(); }
    }""")
    t = (text or "").upper()
    check("ELECTRICITY" in t and "BILL" in t, f"OCR engine runs under the policy and reads the words (got {text!r})")
    check("1234" in t, "OCR reads the amount digits")
    check(not hosts, f"OCR used no outside hosts ({hosts[:3]})")
    v = page.evaluate("window.__v")
    check(not v, f"OCR caused no CSP violations ({v[:3]})")

    # 2) Injection attempts are blocked.
    page.evaluate("""() => { window.__pwned = false;
      const s = document.createElement('script'); s.textContent = 'window.__pwned = true'; document.body.appendChild(s);
      const e = document.createElement('script'); e.src = 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js'; document.body.appendChild(e); }""")
    page.wait_for_timeout(600)
    check(page.evaluate("window.__pwned") is False, "an injected inline script (no nonce) does not run")
    v = page.evaluate("window.__v")
    check(any("script-src" in x for x in v), f"the browser reports the blocked scripts ({v[:3]})")
    page.close()


def main():
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"]}
        if os.path.exists(CHROME):
            launch["executable_path"] = CHROME
        b = p.chromium.launch(**launch)
        c = b.new_context(**p.devices["iPhone 13"], service_workers="block"); sweep(c, "phone"); c.close()
        c = b.new_context(viewport={"width": 1440, "height": 900}, service_workers="block"); sweep(c, "desktop"); c.close()
        c = b.new_context(viewport={"width": 1000, "height": 800}, service_workers="block"); ocr_and_injection(c); c.close()
        b.close()
    passed = sum(1 for s, _ in results if s == "PASS")
    print(f"\nsprint14_csp_test: {passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)
    print("All Sprint 14 CSP checks passed.")


if __name__ == "__main__":
    main()
