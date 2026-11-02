"""On-demand email check for the web app's "Check email now" button.

Runs on the server, for the signed-in owner only:

    1. who_is(jwt)           -> the Supabase user behind the browser's token
    2. gmail_access_token()  -> a fresh read-only Gmail token from the owner's
                                refresh token (Google OAuth, testing mode)
    3. run_scan(...)         -> fetch recent bank / Google Pay / receipt emails,
                                parse them, and insert new rows into
                                pending_imports AS THAT USER (their JWT), so RLS
                                keeps the rows in their own account.

Nothing is approved automatically: rows land as "pending" for the user to
review. Keys come only from the environment (Render settings), never the code:

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_OWNER_EMAIL

Every network call takes an `opener` so tests can run fully offline.
"""
import json
import os
import urllib.parse
import urllib.request

from bot import gmail_source
from bot.scanner import fetch_existing_ids, push_to_supabase, scan

TOKEN_URL = "https://oauth2.googleapis.com/token"
ENV_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_OWNER_EMAIL")


class ScanError(Exception):
    """A failure with a message that is safe to show the user."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.message, self.status = message, status


def configured(env=os.environ):
    return all((env.get(k) or "").strip() for k in ENV_KEYS)


def is_owner(email, env=os.environ):
    owner = (env.get("GMAIL_OWNER_EMAIL") or "").strip().lower()
    return bool(owner) and (email or "").strip().lower() == owner


def who_is(jwt, supabase_url, anon_key, opener=urllib.request.urlopen):
    """The Supabase user for an access token, or None if it isn't valid."""
    if not jwt or not supabase_url or not anon_key:
        return None
    req = urllib.request.Request(supabase_url.rstrip("/") + "/auth/v1/user",
                                 headers={"apikey": anon_key, "Authorization": "Bearer " + jwt})
    try:
        with opener(req, timeout=15) as resp:
            user = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def gmail_access_token(env=os.environ, opener=urllib.request.urlopen):
    """Exchange the stored refresh token for a short-lived gmail.readonly token."""
    body = urllib.parse.urlencode({
        "client_id": env.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": env.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": env.get("GMAIL_REFRESH_TOKEN", ""),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener(req, timeout=15) as resp:
            token = json.loads(resp.read().decode("utf-8")).get("access_token")
    except Exception:
        token = None
    if not token:
        raise ScanError("Couldn't sign in to Gmail. The Gmail connection may have expired; "
                        "it needs to be set up again.", 502)
    return token


def run_scan(jwt, supabase_url, anon_key, gmail_token, query=None, max_messages=200,
             opener=urllib.request.urlopen):
    """Fetch, parse and queue new transactions. Returns {checked, added}."""
    try:
        existing = fetch_existing_ids(supabase_url, jwt, anon_key, opener=opener)
        messages = list(gmail_source.fetch_messages(
            token=gmail_token, query=query or gmail_source.default_query(),
            max_messages=max_messages, opener=opener))
    except ScanError:
        raise
    except Exception:
        raise ScanError("Couldn't read your email just now. Please try again in a minute.", 502)
    rows = scan(messages, existing)
    try:
        push_to_supabase(rows, supabase_url, jwt, anon_key, opener=opener)
    except Exception:
        raise ScanError("Found new transactions but couldn't save them. Please try again.", 502)
    return {"checked": len(messages), "added": len(rows)}
