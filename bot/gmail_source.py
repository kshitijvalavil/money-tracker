"""Fetch emails from Gmail via the Gmail REST API (read-only), stdlib only.

Auth: a ready-made OAuth 2.0 access token with the
`https://www.googleapis.com/auth/gmail.readonly` scope, passed in env
GMAIL_TOKEN. Obtaining/refreshing that token is out of scope here (use the
Google OAuth playground or your own consent flow); this module never asks for
more than read access and never modifies the mailbox.

Output: dicts shaped {id, subject, sender, body, date} — exactly what
bot.scanner.scan() takes.

The network part (fetch_messages) is a thin loop; the parsing part
(parse_api_message) is pure and unit-tested against a canned API response.
"""
import base64
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_QUERY = "from:(alerts.sbi.bank.in) newer_than:30d"


# --- pure parsing -----------------------------------------------------------

def _b64url(data):
    if not data:
        return ""
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _html_to_text(s):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def _find_body(part, mime):
    """Depth-first search for the first part of the given mimeType with data."""
    if part.get("mimeType", "").lower() == mime and part.get("body", {}).get("data"):
        return _b64url(part["body"]["data"])
    for sub in part.get("parts", []) or []:
        found = _find_body(sub, mime)
        if found:
            return found
    return None


def parse_api_message(msg):
    """Turn a Gmail API `users.messages.get?format=full` response into
    {id, subject, sender, body, date}. Prefers text/plain, falls back to
    text/html with tags stripped. `date` is the ISO date of internalDate (UTC)."""
    payload = msg.get("payload", {}) or {}
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("headers", []) or []}
    body = _find_body(payload, "text/plain")
    if body is None:
        raw_html = _find_body(payload, "text/html")
        body = _html_to_text(raw_html) if raw_html else msg.get("snippet", "")
    body = re.sub(r"[ \t\r\f\v]+", " ", body).strip()

    date = None
    if msg.get("internalDate"):
        ts = int(msg["internalDate"]) / 1000
        date = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
    return {
        "id": msg.get("id"),
        "subject": headers.get("subject", ""),
        "sender": headers.get("from", ""),
        "body": body,
        "date": date or headers.get("date"),
    }


# --- network ----------------------------------------------------------------

def _get_json(url, token, opener):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
                                               "Accept": "application/json"})
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_messages(token=None, query=DEFAULT_QUERY, max_messages=200,
                   opener=urllib.request.urlopen):
    """Yield parsed messages matching a Gmail search query (newest first)."""
    token = token or os.environ.get("GMAIL_TOKEN")
    if not token:
        raise SystemExit("GMAIL_TOKEN is not set (needs a gmail.readonly OAuth access token)")
    page_token, seen = None, 0
    while seen < max_messages:
        params = {"q": query, "maxResults": min(100, max_messages - seen)}
        if page_token:
            params["pageToken"] = page_token
        listing = _get_json(API + "/messages?" + urllib.parse.urlencode(params), token, opener)
        for ref in listing.get("messages", []) or []:
            if seen >= max_messages:
                return
            mid = urllib.parse.quote(ref["id"], safe="")
            yield parse_api_message(_get_json(API + "/messages/" + mid + "?format=full",
                                              token, opener))
            seen += 1
        page_token = listing.get("nextPageToken")
        if not page_token:
            break
