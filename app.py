"""Money Tracker — Flask app.

This server only serves pages. The pages talk to Supabase directly from the
browser (auth + data), protected by Row Level Security in the database, so this
server never holds your database password or sees your data.
"""
import os

from flask import Flask, jsonify, render_template, request

from billreader import BillReadError, read_bill


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
    return render_template("reports.html", page="reports")


@app.route("/imports")
def imports():
    return render_template("imports.html", page="imports")


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


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
