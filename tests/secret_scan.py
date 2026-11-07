"""Scan the code and its whole git history for leaked secrets (Sprint 14).

    python3 tests/secret_scan.py

Looks for private keys, cloud and API tokens, and Supabase 'service_role' keys
(the all-powerful one; the public 'anon' key is meant to be in browsers and is
allowed). Exit 1 if anything is found. Runs in CI.
"""
import base64
import json
import re
import subprocess
import sys

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Render deploy hook": re.compile(r"https://api\.render\.com/deploy/srv-[A-Za-z0-9]+\?key=[A-Za-z0-9_\-]+"),
    "Supabase secret key": re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{20,}"),
}
JWT = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.(eyJ[A-Za-z0-9_\-]{10,})\.[A-Za-z0-9_\-]{10,}")


def service_role(payload_b64):
    try:
        pad = "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + pad)).get("role") == "service_role"
    except Exception:
        return False


def scan_text(label, text, found):
    for name, rx in PATTERNS.items():
        if rx.search(text):
            found.add((label, name))
    for m in JWT.finditer(text):
        if service_role(m.group(1)):
            found.add((label, "Supabase service_role key"))


found = set()
files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
for f in files:
    if not f or f.startswith("static/vendor/") or f.endswith((".png", ".ico", ".gz", ".webp", ".jpg")):
        continue
    try:
        scan_text(f, open(f, encoding="utf-8", errors="ignore").read(), found)
    except OSError:
        pass

# Every line ever added or removed, on every branch (a secret deleted later is still leaked).
hist = subprocess.run(["git", "log", "--all", "-p", "--no-color", "--text", "--", ".", ":!static/vendor", ":!*.png", ":!*.gz"],
                      capture_output=True, text=True, errors="ignore").stdout
scan_text("git history", hist, found)

if found:
    print("POSSIBLE SECRETS FOUND:")
    for where, what in sorted(found):
        print(f"  {what}  in  {where}")
    sys.exit(1)
print(f"secret_scan: {len(files)} files and the full git history checked, no secrets found.")
