"""Money Tracker — Flask app.

This server only serves pages. The pages talk to Supabase directly from the
browser (auth + data), protected by Row Level Security in the database, so this
server never holds your database password or sees your data.
"""
import os

from flask import Flask, render_template


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


@app.route("/reports")
def reports():
    return render_template("reports.html", page="reports")


@app.route("/imports")
def imports():
    return render_template("imports.html", page="imports")


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
