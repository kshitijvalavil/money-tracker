"""Email import scanner: emails -> rows for public.pending_imports.

Pure core:
    scan(messages, existing_ids) -> list of rows (no user_id; the database
    fills it from the caller's JWT via `default auth.uid()`).

Upload:
    push_to_supabase(rows, url, user_jwt) POSTs the rows to PostgREST as the
    signed-in user, so RLS keeps every row in that user's own space. Never
    use the service_role key here.

CLI:
    python -m bot.scanner --from-dir tests/fixtures         # dry run, prints rows
    python -m bot.scanner --gmail [--query ...] [--push]    # needs GMAIL_TOKEN
Push needs SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_USER_JWT (the user's
access token from a signed-in session) in the environment.
"""
import argparse
import email
import email.policy
import email.utils
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

from bot.gmail_source import _html_to_text
from bot.parsers import parse_email

# Largest value numeric(14, 2) can hold; bigger amounts would make PostgREST
# reject the whole bulk insert, so such rows are dropped.
MAX_AMOUNT = 999999999999.99

# Exactly the pending_imports columns we send (PostgREST bulk insert needs
# every object to carry the same keys).
ROW_KEYS = ("source", "direction", "amount", "occurred_on", "account_hint",
            "note", "source_message_id")


def _iso_date(value):
    """ISO date from 'YYYY-MM-DD...', an RFC 2822 Date header, or a date."""
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return (value.date() if isinstance(value, datetime) else value).isoformat()
    value = str(value).strip()
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def to_row(parsed):
    """A parser dict -> a pending_imports row, or None if it can't be stored."""
    try:
        amount = round(float(parsed.get("amount") or 0), 2)
    except (TypeError, ValueError):
        return None
    if amount <= 0 or amount > MAX_AMOUNT or parsed.get("direction") not in ("income", "expense"):
        return None
    occurred_on = _iso_date(parsed.get("occurred_on"))
    if not occurred_on or not parsed.get("source_message_id"):
        return None
    return {
        "source": parsed.get("source") or "email",
        "direction": parsed["direction"],
        "amount": amount,
        "occurred_on": occurred_on,
        "account_hint": parsed.get("account_hint"),
        "note": parsed.get("note") or "",
        "source_message_id": str(parsed["source_message_id"]),
    }


def scan(messages, existing_ids=()):
    """Parse emails into pending_imports rows.

    messages: iterable of {id, subject, sender, body, date}.
    existing_ids: message ids already imported (skipped).
    Drops non-transactions, rows missing required fields, repeats within the
    batch, and anything in existing_ids. Order of first appearance is kept.
    """
    seen = set(existing_ids or ())
    rows = []
    for msg in messages:
        mid = msg.get("id")
        if not mid or mid in seen:
            continue
        parsed = parse_email(msg.get("subject", ""), msg.get("sender", ""),
                             msg.get("body", ""), message_id=mid,
                             email_date=_iso_date(msg.get("date")))
        row = to_row(parsed) if parsed else None
        if row:
            seen.add(mid)
            rows.append(row)
    return rows


# --- Supabase (PostgREST) ----------------------------------------------------

def build_push_request(rows, url, user_jwt, anon_key=None):
    """The urllib Request that inserts rows as the signed-in user."""
    anon_key = anon_key or os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not user_jwt or not anon_key:
        raise ValueError("need Supabase url, anon key and the user's access token")
    endpoint = (url.rstrip("/") + "/rest/v1/pending_imports?on_conflict="
                + urllib.parse.quote("user_id,source_message_id", safe=","))
    body = json.dumps([{k: r.get(k) for k in ROW_KEYS} for r in rows]).encode("utf-8")
    return urllib.request.Request(endpoint, data=body, method="POST", headers={
        "apikey": anon_key,
        "Authorization": "Bearer " + user_jwt,
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    })


def push_to_supabase(rows, url, user_jwt, anon_key=None, opener=urllib.request.urlopen):
    """POST rows to {url}/rest/v1/pending_imports. Returns the HTTP status
    (None when there was nothing to send). Duplicates are ignored server-side."""
    rows = list(rows)
    if not rows:
        return None
    req = build_push_request(rows, url, user_jwt, anon_key)
    with opener(req, timeout=30) as resp:
        return resp.status


def fetch_existing_ids(url, user_jwt, anon_key=None, opener=urllib.request.urlopen):
    """Message ids this user already has in pending_imports or transactions."""
    anon_key = anon_key or os.environ.get("SUPABASE_ANON_KEY", "")
    ids = set()
    for table in ("pending_imports", "transactions"):
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/" + table
            + "?select=source_message_id&source_message_id=not.is.null",
            headers={"apikey": anon_key, "Authorization": "Bearer " + user_jwt})
        with opener(req, timeout=30) as resp:
            ids.update(r["source_message_id"] for r in json.loads(resp.read().decode("utf-8")))
    return ids


# --- local .eml files ---------------------------------------------------------

def load_eml(path):
    """Read one .eml file into {id, subject, sender, body, date}."""
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            # Unknown/bogus charset: decode the raw bytes as utf-8 instead.
            raw = part.get_payload(decode=True) or b""
            body = raw.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            body = _html_to_text(body)
    return {
        "id": (msg.get("Message-ID") or os.path.basename(path)).strip("<> "),
        "subject": str(msg.get("Subject", "")),
        "sender": str(msg.get("From", "")),
        "body": body,
        "date": str(msg.get("Date", "")),
    }


def load_dir(directory):
    return [load_eml(os.path.join(directory, n))
            for n in sorted(os.listdir(directory)) if n.endswith(".eml")]


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bot.scanner", description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-dir", help="scan .eml files in this directory")
    src.add_argument("--gmail", action="store_true", help="scan Gmail (env GMAIL_TOKEN)")
    ap.add_argument("--query", default=None, help="Gmail search query")
    ap.add_argument("--max", type=int, default=200, help="max Gmail messages")
    ap.add_argument("--push", action="store_true",
                    help="upload to Supabase (env SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_USER_JWT)")
    args = ap.parse_args(argv)

    if args.from_dir:
        messages = load_dir(args.from_dir)
    else:
        from bot import gmail_source
        messages = gmail_source.fetch_messages(
            query=args.query or os.environ.get("GMAIL_QUERY") or gmail_source.DEFAULT_QUERY,
            max_messages=args.max)

    url, jwt = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_USER_JWT", "")
    existing = fetch_existing_ids(url, jwt) if args.push else set()
    rows = scan(messages, existing)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    print(f"{len(rows)} transaction(s) found", file=sys.stderr)

    if args.push:
        status = push_to_supabase(rows, url, jwt)
        print(f"pushed {len(rows)} row(s) to pending_imports (HTTP {status})", file=sys.stderr)
    else:
        print("dry run: nothing uploaded (use --push)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
