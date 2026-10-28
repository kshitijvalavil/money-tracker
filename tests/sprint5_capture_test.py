"""Snap-a-bill capture UI test (Sprint 5) — demo mode.

    DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
    BASE_URL=http://127.0.0.1:8099 python tests/sprint5_capture_test.py

Covers: the capture card and dropzone are on the Bills page, the camera input
opens the camera on a phone (accept image + capture=environment), choosing a
photo calls /api/read-bill and fills the Add-a-bill form from the result, and a
non-image file is rejected with a clear message.
Needs playwright + Chromium (CHROME_PATH, default /opt/pw-browsers/chromium).
"""
import base64
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8099")
CHROME = os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium")

# A valid 1x1 PNG, so the browser can decode and downscale it with no extra deps.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

results = []


def check(cond, msg):
    results.append(("PASS" if cond else "FAIL", msg))
    if not cond:
        print("  FAIL:", msg)


def run(pw):
    browser = pw.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    page = ctx.new_page()
    page.on("pageerror", lambda e: check(False, f"page error: {e}"))
    page.goto(f"{BASE}/bills", wait_until="networkidle")
    page.wait_for_timeout(300)

    # ---- The capture UI is present ----------------------------------------
    check(page.locator("#captureCard").is_visible(), "the Snap-a-bill card is shown")
    check(page.locator("#dropzone").is_visible(), "the dropzone is shown")
    check(page.locator("#pickBtn").is_visible(), "a Choose file button is shown")
    check(page.locator("#cameraBtn").is_visible(), "a Take photo button is shown")
    cap = page.get_attribute("#cameraInput", "capture")
    check(cap == "environment", f"the camera input opens the rear camera (capture={cap})")
    accept = page.get_attribute("#cameraInput", "accept") or ""
    check("image" in accept, "the camera input accepts images")

    # ---- Choosing a photo fills the form from the read --------------------
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(PNG_1X1)
        img_path = fh.name
    page.set_input_files("#fileInput", img_path)
    # Demo mode returns a fixed sample: Acme Power & Light, 84.50, monthly, no date.
    page.wait_for_function("document.getElementById('fName').value.length > 0", timeout=8000)
    check(page.input_value("#fName") == "Acme Power & Light", "the bill name is filled from the photo")
    check(page.input_value("#fAmount") in ("84.5", "84.50"), "the amount is filled from the photo")
    check(page.input_value("#fType") == "expense", "the type is set to expense")
    check(page.input_value("#fFreq") == "monthly", "the frequency guess is applied")
    status = (page.text_content("#captureStatus") or "").lower()
    check("due date" in status, "it asks for the missing due date (none in the sample)")
    check(page.locator("#captureStatus").is_visible(), "a status line is shown after reading")

    # ---- A non-image file is rejected clearly -----------------------------
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as fh:
        fh.write("not an image")
        txt_path = fh.name
    page.set_input_files("#fileInput", txt_path)
    page.wait_for_timeout(300)
    status2 = (page.text_content("#captureStatus") or "").lower()
    check("not an image" in status2 or "jpeg" in status2, "a non-image file is rejected with a message")

    os.unlink(img_path)
    os.unlink(txt_path)
    ctx.close()
    browser.close()


def main():
    with sync_playwright() as pw:
        run(pw)
    passed = sum(1 for s, _ in results if s == "PASS")
    failed = [m for s, m in results if s == "FAIL"]
    print(f"\nsprint5_capture_test: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILURES:")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("All Sprint 5 capture checks passed.")


if __name__ == "__main__":
    main()
