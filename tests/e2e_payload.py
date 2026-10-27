"""Print the exact JSON body the scanner would POST to PostgREST for the
.eml files in a directory (default tests/fixtures), after checking the
request's PostgREST assumptions (endpoint, on_conflict columns, Prefer).

Used by tests/run.sh to feed tests/e2e_import_test.sql, so the end-to-end
database test runs on the scanner's real output, not hand-written rows.

Run: python3 tests/e2e_payload.py [dir]
"""
import json
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot.scanner import ROW_KEYS, build_push_request, load_dir, scan


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tests", "fixtures")
    rows = scan(load_dir(directory))
    if not rows:
        sys.exit("e2e_payload: scanner found no rows in " + directory)
    req = build_push_request(rows, "https://example.supabase.co", "user-jwt", "anon-key")
    url = urllib.parse.urlsplit(req.full_url)
    query = urllib.parse.parse_qs(url.query)
    problems = []
    if url.path != "/rest/v1/pending_imports":
        problems.append("endpoint is " + url.path)
    if query.get("on_conflict") != ["user_id,source_message_id"]:
        problems.append("on_conflict is %r" % query.get("on_conflict"))
    if "resolution=ignore-duplicates" not in (req.get_header("Prefer") or ""):
        problems.append("Prefer lacks resolution=ignore-duplicates")
    if req.get_header("Authorization") != "Bearer user-jwt":
        problems.append("not sent as the user (Authorization header)")
    body = json.loads(req.data)
    for row in body:
        if "user_id" in row or "status" in row or "id" in row:
            problems.append("payload sets a db-owned column: %r" % sorted(row))
        if tuple(row) != ROW_KEYS:
            problems.append("row keys differ from ROW_KEYS: %r" % list(row))
    if problems:
        sys.exit("e2e_payload: " + "; ".join(problems))
    print(json.dumps(body, ensure_ascii=False))


if __name__ == "__main__":
    main()
