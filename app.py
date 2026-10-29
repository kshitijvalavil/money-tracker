"""Money Tracker — Flask app.

This server only serves pages. The pages talk to Supabase directly from the
browser (auth + data), protected by Row Level Security in the database, so this
server never holds your database password or sees your data.
"""
import os
import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, redirect, render_template, request

from billreader import BillReadError, parse_bill_text, read_bill


def _load_dotenv(path=".env"):
    """Minimal .env loader for local development (no external dependency)."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

app = Flask(__name__)

# A read-bill upload is a downscaled photo; cap the request so a huge body
# can't tie up the server. The browser shrinks photos well below this.
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

# Public Supabase settings, injected into every page. The anon key is designed
# to be shipped to browsers; it is safe to expose. Never put the service_role
# key or the database password here.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# Demo mode: try the UI with fake in-memory data and no login. Turn it on with
# DEMO_MODE=1. Never enable it for real use — it stores nothing.
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")

# Content-Security-Policy. The app loads only its own files; the one cross-origin
# talker is Supabase (REST + realtime websocket). Inline <script>/<style> are used
# for the pre-paint theme and config bootstrap, so they are allowed; everything
# external is blocked. frame-ancestors 'none' also stops click-jacking.
_SUPABASE_HOST = (SUPABASE_URL or "").rstrip("/")
# Open-source OCR (Tesseract.js) for the free Snap-a-bill path loads its script,
# worker and language data from these CDNs in the browser.
_OCR_CDNS = "https://cdn.jsdelivr.net https://unpkg.com https://tessdata.projectnaptha.com"
_CONNECT = " ".join(x for x in ["'self'", _SUPABASE_HOST, "https://*.supabase.co",
                                "wss://*.supabase.co", _OCR_CDNS] if x)
_CSP = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' blob: {_OCR_CDNS}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    f"connect-src {_CONNECT}; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Tell browsers to stick to HTTPS for a year (Render serves the app over TLS).
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.after_request
def set_security_headers(resp):
    for name, value in _SECURITY_HEADERS.items():
        resp.headers.setdefault(name, value)
    return resp


# Lightweight in-memory rate limit for the paid vision endpoint, so a single
# client cannot run up the bill (or hammer the server). Per-process, which is
# enough as a first guard; keyed by the caller's IP.
_RL_LOCK = threading.Lock()
_RL_HITS = defaultdict(deque)
_RL_MAX = int(os.environ.get("READ_BILL_RATE_MAX", "20"))   # requests...
_RL_WINDOW = int(os.environ.get("READ_BILL_RATE_WINDOW", "60"))  # ...per this many seconds


def _client_ip():
    # Render puts the real client first in X-Forwarded-For; fall back to the peer.
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")


def _rate_limited(key):
    now = time.monotonic()
    with _RL_LOCK:
        hits = _RL_HITS[key]
        while hits and hits[0] <= now - _RL_WINDOW:
            hits.popleft()
        if len(hits) >= _RL_MAX:
            return True
        hits.append(now)
        return False


@app.context_processor
def inject_config():
    return {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_ANON_KEY,
        "DEMO_MODE": DEMO_MODE,
        "config_ready": DEMO_MODE or bool(SUPABASE_URL and SUPABASE_ANON_KEY),
    }


@app.route("/")
def dashboard():
    return render_template("dashboard.html", page="dashboard")


@app.route("/login")
def login():
    return render_template("login.html", page="login")


@app.route("/transactions")
def transactions():
    return render_template("transactions.html", page="transactions")


@app.route("/accounts")
def accounts():
    return render_template("accounts.html", page="accounts")


@app.route("/budgets")
def budgets():
    return render_template("budgets.html", page="budgets")


@app.route("/bills")
def bills():
    return render_template("bills.html", page="bills")


@app.route("/goals")
def goals():
    return render_template("goals.html", page="goals")


@app.route("/insights")
def insights():
    return render_template("insights.html", page="insights")


@app.route("/reports")
def reports():
    # Reports merged into Insights (Sprint 6 IA). Keep the path as a redirect
    # so old links, bookmarks and the PWA cache still resolve.
    return redirect("/insights", code=301)


@app.route("/imports")
def imports():
    return render_template("imports.html", page="imports")


@app.route("/profile")
def profile():
    return render_template("profile.html", page="profile")


@app.route("/offline")
def offline():
    # Precached by the service worker and shown when a navigation fails offline.
    return render_template("offline.html")


@app.route("/sw.js")
def service_worker():
    # Served from the root so the worker can control the whole app ("/"),
    # which a file under /static/ could not.
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/read-bill", methods=["POST"])
def api_read_bill():
    # Reads a bill photo into form fields. The image is used for this one call
    # and never stored. Demo mode returns a fixed sample and needs no key.
    data = request.get_json(silent=True) or {}
    # Guard the paid vision call behind a signed-in session (the browser sends
    # its Supabase access token); demo mode is open so the demo works with no login.
    if not DEMO_MODE and not request.headers.get("Authorization"):
        return jsonify({"error": "Please sign in and try again."}), 401
    if _rate_limited(_client_ip()):
        resp = jsonify({"error": "Too many bill reads just now. Please wait a moment and try again."})
        resp.headers["Retry-After"] = str(_RL_WINDOW)
        return resp, 429
    try:
        fields = read_bill(
            data.get("image") or "",
            data.get("mime") or "",
            demo=DEMO_MODE,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model=os.environ.get("BILL_MODEL"),
        )
    except BillReadError as e:
        return jsonify({"error": e.message}), e.status
    return jsonify(fields)


@app.route("/api/parse-bill-text", methods=["POST"])
def api_parse_bill_text():
    # The FREE path: the browser does open-source OCR on the photo and sends the
    # text here; we extract the fields with no API call and no cost. Light rate
    # limit to stop abuse; no key needed because nothing paid happens here.
    if _rate_limited("parse:" + _client_ip()):
        resp = jsonify({"error": "Too many reads just now. Please wait a moment and try again."})
        resp.headers["Retry-After"] = str(_RL_WINDOW)
        return resp, 429
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "No text was read from the photo. Try a clearer, straight-on shot."}), 400
    # The form only needs a little text; ignore anything huge.
    return jsonify(parse_bill_text(text[:20000]))


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    # Debug is OFF by default; turn it on for local work with FLASK_DEBUG=1.
    # Production runs under gunicorn (see Procfile / render.yaml), not this block.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug, port=int(os.environ.get("PORT", 5000)))
