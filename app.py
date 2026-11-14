"""Money Tracker — Flask app.

This server only serves pages. The pages talk to Supabase directly from the
browser (auth + data), protected by Row Level Security in the database, so this
server never holds your database password or sees your data.
"""
import datetime
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque

from flask import Flask, g, jsonify, redirect, render_template, request

from assistant import AssistantError, reply as assistant_reply
from billreader import BillReadError, parse_bill_text, read_bill
from statementreader import read_statement
from xlsreader import XlsReadError, read_xls


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

# Planned maintenance (for example a database change). Set MAINTENANCE_MODE=1 in
# Render and every page shows the friendly "we are updating" screen with a 503
# until it is switched off again. /healthz stays up so Render keeps the service.
MAINTENANCE = os.environ.get("MAINTENANCE_MODE", "").lower() in ("1", "true", "yes")
# Bump this date whenever templates/privacy.html changes in a way people should
# re-accept. Everyone is asked to accept the current version before using the app.
NOTICE_VERSION = "2026-11-14"
# Optional: where people can send privacy questions and complaints (set on Render).
PRIVACY_CONTACT = os.environ.get("PRIVACY_CONTACT_EMAIL", "").strip()
_MAINTENANCE_OPEN = ("/healthz", "/privacy", "/.well-known/", "/api/status", "/deploying", "/offline", "/sw.js", "/static/")

# Content-Security-Policy. The app loads only its own files; the one cross-origin
# talker is Supabase (REST + realtime websocket). Everything else outside is
# blocked. frame-ancestors 'none' also stops click-jacking.
_SUPABASE_HOST = (SUPABASE_URL or "").rstrip("/")
# Scripts: only our own files, plus inline scripts that carry this request's secret
# nonce (so an injected script, which cannot know it, will not run). No outside
# hosts at all: the OCR library is served from /static/vendor/tesseract.
# 'wasm-unsafe-eval' lets the browser compile the OCR engine's WebAssembly; it does
# not allow eval() of text. Styles still allow inline because the pages use style
# attributes; styles cannot run code.
_CONNECT = " ".join(x for x in ["'self'", _SUPABASE_HOST, "https://*.supabase.co",
                                "wss://*.supabase.co"] if x)


def _build_csp(nonce):
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' 'wasm-unsafe-eval'; "
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
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # microphone is self so the voice-note page can ask to listen (Sprint 16);
    # the browser still prompts for permission on each use.
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Tell browsers to stick to HTTPS for a year (Render serves the app over TLS).
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.before_request
def make_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(18)


@app.before_request
def maintenance_gate():
    if MAINTENANCE and not request.path.startswith(_MAINTENANCE_OPEN):
        resp = app.make_response((render_template("deploying.html"), 503))
        resp.headers["Retry-After"] = "30"
        resp.headers["Cache-Control"] = "no-store"
        return resp


@app.after_request
def set_security_headers(resp):
    resp.headers["Content-Security-Policy"] = _build_csp(getattr(g, "csp_nonce", ""))
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


# How many reverse proxies sit in front of the app and append to X-Forwarded-For.
# Each one adds the address of the peer it saw, so the real client is the entry
# that many places from the END. The left-most entry is whatever the client chose
# to send, so it must never be trusted. Over-counting only makes limits stricter.
_TRUSTED_PROXIES = max(1, int(os.environ.get("TRUSTED_PROXIES", "1")))


def _client_ip():
    parts = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
    if parts:
        return parts[max(0, len(parts) - _TRUSTED_PROXIES)]
    return request.remote_addr or "unknown"


# --- Who is calling? ---------------------------------------------------------
# The browser sends its Supabase access token. A header merely being present
# proves nothing, so ask Supabase Auth whether the token is real and unexpired.
# Good answers are cached for a short time so we do not call Supabase on every
# request. Any failure (bad token, Supabase unreachable) means "not signed in".
_AUTH_TTL = 60
_AUTH_CACHE_MAX = 1000
_AUTH_LOCK = threading.Lock()
_AUTH_CACHE = {}  # sha256(token) -> (user_id, expires_at)


def _fetch_supabase_user(token):
    """Ask Supabase Auth who owns this token. Returns the user id or None."""
    if not (SUPABASE_URL and SUPABASE_ANON_KEY):
        return None
    req = urllib.request.Request(
        SUPABASE_URL.rstrip("/") + "/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return None
            uid = json.loads(resp.read().decode("utf-8")).get("id")
            return uid if isinstance(uid, str) and uid else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _signed_in_user():
    """The signed-in user's id for this request, or None. Demo mode is open."""
    if DEMO_MODE:
        return "demo"
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 4096:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    with _AUTH_LOCK:
        hit = _AUTH_CACHE.get(key)
        if hit and hit[1] > now:
            return hit[0]
    uid = _fetch_supabase_user(token)
    if uid:
        with _AUTH_LOCK:
            if len(_AUTH_CACHE) >= _AUTH_CACHE_MAX:
                _AUTH_CACHE.clear()
            _AUTH_CACHE[key] = (uid, now + _AUTH_TTL)
    return uid


def _too_many(window):
    resp = jsonify({"error": "Too many requests just now. Please wait a moment and try again."})
    resp.headers["Retry-After"] = str(window)
    return resp, 429


def _rate_limited(key, max_hits=None, window=None):
    max_hits = _RL_MAX if max_hits is None else max_hits
    window = _RL_WINDOW if window is None else window
    now = time.monotonic()
    with _RL_LOCK:
        hits = _RL_HITS[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= max_hits:
            return True
        hits.append(now)
        return False


@app.context_processor
def inject_config():
    return {
        "csp_nonce": getattr(g, "csp_nonce", ""),
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_ANON_KEY,
        "DEMO_MODE": DEMO_MODE,
        "NOTICE_VERSION": NOTICE_VERSION,
        "PRIVACY_CONTACT": PRIVACY_CONTACT,
        "config_ready": DEMO_MODE or bool(SUPABASE_URL and SUPABASE_ANON_KEY),
    }


# Every page is a template; the browser does the data work. (path, template,
# page key, section). The section picks the highlighted menu entry and the tab
# strip in templates/_shell.html.
PAGES = [
    ("/", "dashboard.html", "dashboard", "home"),
    ("/login", "login.html", "login", ""),
    ("/consent", "consent.html", "consent", ""),
    ("/privacy", "privacy.html", "privacy", ""),
    ("/transactions", "transactions.html", "transactions", "transactions"),
    ("/bills", "bills.html", "bills", "transactions"),
    ("/calendar", "calendar.html", "calendar", "transactions"),
    ("/imports", "imports.html", "imports", "transactions"),
    ("/import", "import.html", "import", "transactions"),
    ("/voice", "voice.html", "voice", "transactions"),
    ("/budgets", "budgets.html", "budgets", "plan"),
    ("/goals", "goals.html", "goals", "plan"),
    ("/forecast", "forecast.html", "forecast", "plan"),
    ("/net-worth", "networth.html", "networth", "wealth"),
    ("/accounts", "accounts.html", "accounts", "wealth"),
    ("/investments", "investments.html", "investments", "wealth"),
    ("/insights", "insights.html", "insights", "insights"),
    ("/ask", "ask.html", "ask", "ask"),
    ("/alerts", "alerts.html", "alerts", ""),
    ("/profile", "profile.html", "profile", ""),
]


def _page_view(template, page, section):
    def view():
        return render_template(template, page=page, section=section)
    return view


for _path, _template, _page, _section in PAGES:
    app.add_url_rule(_path, endpoint="page_" + _page, view_func=_page_view(_template, _page, _section))

# Pages that were merged into others. Old links, bookmarks and installed apps
# still resolve.
MOVED = {"/reports": "/insights", "/wealth": "/investments"}


@app.route("/reports")
@app.route("/wealth")
def moved():
    return redirect(MOVED[request.path], code=301)


@app.route("/.well-known/security.txt")
def security_txt():
    # Where to report a security problem (RFC 9116). The GitHub link is private
    # vulnerability reporting for this repository.
    expires = (datetime.date.today() + datetime.timedelta(days=180)).isoformat() + "T00:00:00.000Z"
    lines = []
    if PRIVACY_CONTACT:
        lines.append("Contact: mailto:" + PRIVACY_CONTACT)
    lines += [
        "Contact: https://github.com/kshitijvalavil/money-tracker/security/advisories/new",
        "Expires: " + expires,
        "Preferred-Languages: en",
        "Canonical: " + request.url_root.rstrip("/") + "/.well-known/security.txt",
        "Policy: " + request.url_root.rstrip("/") + "/privacy",
    ]
    return app.response_class("\n".join(lines) + "\n", mimetype="text/plain")


@app.route("/deploying")
def deploying():
    # The friendly "we are updating" page. Precached by the service worker so it
    # can be shown even while the server itself is restarting during a deploy.
    return render_template("deploying.html")


@app.route("/api/status")
def api_status():
    # The updating page asks this every few seconds: ready yet?
    resp = jsonify({"ok": True, "deploying": MAINTENANCE})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/offline")
def offline():
    # Precached by the service worker and shown when a navigation fails offline.
    return render_template("offline.html")


def _asset_version():
    """Short hash of everything under static/, worked out once at start-up.
    It changes whenever a deploy changes any CSS, JS or icon file."""
    digest = hashlib.sha256()
    root = app.static_folder
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            digest.update(os.path.relpath(path, root).encode())
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()


@app.route("/sw.js")
def service_worker():
    # Served from the root so the worker can control the whole app ("/"),
    # which a file under /static/ could not. The cache name is stamped with
    # ASSET_VERSION, so every deploy that changes a static file ships a new
    # worker: browsers install it, drop the old cache and fetch the new CSS
    # and JS instead of serving the previous release's copies.
    with open(os.path.join(app.static_folder, "sw.js"), encoding="utf-8") as f:
        body = f.read().replace('"money-tracker-v1"', f'"money-tracker-{ASSET_VERSION}"')
    resp = app.response_class(body, mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/read-bill", methods=["POST"])
def api_read_bill():
    # Reads a bill photo into form fields. The image is used for this one call
    # and never stored. Demo mode returns a fixed sample and needs no key.
    data = request.get_json(silent=True) or {}
    # Guard the paid vision call behind a verified session. Demo mode is open so
    # the demo works with no login.
    uid = _signed_in_user()
    if not uid:
        return jsonify({"error": "Please sign in and try again."}), 401
    # Count per person (a spoofed IP cannot dodge this) and per address.
    if _rate_limited("user:" + uid) or _rate_limited(_client_ip()):
        return _too_many(_RL_WINDOW)
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
    # text here; we extract the fields with no API call and no cost. Needs a
    # verified session and is rate limited per person and per address.
    uid = _signed_in_user()
    if not uid:
        return jsonify({"error": "Please sign in and try again."}), 401
    if _rate_limited("parse:user:" + uid) or _rate_limited("parse:" + _client_ip()):
        return _too_many(_RL_WINDOW)
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "No text was read from the photo. Try a clearer, straight-on shot."}), 400
    # The form only needs a little text; ignore anything huge.
    return jsonify(parse_bill_text(text[:20000]))


@app.route("/api/read-xls", methods=["POST"])
def api_read_xls():
    # Old binary Excel (.xls) only: parsed in memory with xlrd and thrown away.
    # The browser reads every other format itself. Verified sign-in (like the
    # bill endpoints, Sprint 14), counted per person and per address.
    uid = _signed_in_user()
    if not uid:
        return jsonify({"error": "Please sign in and try again."}), 401
    if _rate_limited("xls:user:" + uid, max_hits=30) or _rate_limited("xls:" + _client_ip(), max_hits=30):
        return _too_many(_RL_WINDOW)
    try:
        return jsonify(read_xls(request.get_data(cache=False)))
    except XlsReadError as e:
        return jsonify({"error": e.message}), e.status


@app.route("/api/read-statement", methods=["POST"])
def api_read_statement():
    # Claude fallback for a scanned or messy statement, only after the person
    # agrees in the browser. Nothing is stored. Demo mode returns a fixed sample.
    uid = _signed_in_user()
    if not uid:
        return jsonify({"error": "Please sign in and try again."}), 401
    if _rate_limited("stmt:user:" + uid, max_hits=6) or _rate_limited("stmt:" + _client_ip(), max_hits=6):
        return _too_many(_RL_WINDOW)
    try:
        out = read_statement(
            request.get_json(silent=True),
            demo=DEMO_MODE,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model=os.environ.get("STATEMENT_MODEL"),
        )
    except BillReadError as e:
        return jsonify({"error": e.message}), e.status
    return jsonify(out)


@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    # One turn of the assistant chat. The browser holds the conversation and runs
    # the data tools itself; this only forwards to Claude. Verified sign-in, and
    # counted per person and per address (each question is a few turns).
    uid = _signed_in_user()
    if not uid:
        return jsonify({"error": "Please sign in and try again."}), 401
    if (_rate_limited("ask:user:" + uid, max_hits=40) or _rate_limited("ask:" + _client_ip(), max_hits=40)
            or _rate_limited("ask:day:" + uid, max_hits=400, window=86400)):
        return _too_many(_RL_WINDOW)
    data = request.get_json(silent=True) or {}
    try:
        out = assistant_reply(
            data.get("messages"),
            today=data.get("today") or "",
            demo=DEMO_MODE,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model=os.environ.get("ASSISTANT_MODEL"),
            effort=os.environ.get("ASSISTANT_EFFORT"),
        )
    except AssistantError as e:
        return jsonify({"error": e.message, "unavailable": e.status == 503}), e.status
    return jsonify(out)


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    # Debug is OFF by default; turn it on for local work with FLASK_DEBUG=1.
    # Production runs under gunicorn (see Procfile / render.yaml), not this block.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug, port=int(os.environ.get("PORT", 5000)))
