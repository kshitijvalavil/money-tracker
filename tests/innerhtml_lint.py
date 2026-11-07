"""Lint: user-typed text must never reach the page as raw HTML (Sprint 14).

    python3 tests/innerhtml_lint.py

Pages build HTML with template strings. Anything a person can type (account and
category names, notes, merchants, labels, goal names, emails ...) must go through
esc() first, otherwise a name like <img onerror=...> becomes script. This checks
every HTML-looking template string in the templates and static/js for an
interpolation that names such a field and is not wrapped in esc().

If a hit is a false alarm, say why next to it with the marker  // lint-ok: <reason>
on the same line. Run in CI.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Field names that hold text a person can type or that arrives from an email.
USER_FIELDS = r"(?:name|note|notes|title|merchant|description|desc|email|label|text|memo|payee|source|match_text|account_hint|goal|category|account|bank|vpa|message|detail)"
FIELD = re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\??\.)+" + USER_FIELDS + r"(?![\w$])|(?<![\w$.])" + USER_FIELDS + r"(?![\w$(])")
SAFE_WRAP = re.compile(r"^\s*(?:esc|escapeHtml|encodeURIComponent|CSS\.escape|String\(\s*Number|Number|Math\.[a-z]+|money\w*|signedMoney|inr|pct|shortDate|dueWord|fmtDay|tidyName)\s*\(")
TPL = re.compile(r"`((?:[^`\\]|\\.)*)`", re.S)
EXPR = re.compile(r"\$\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}")

# Reviewed by hand: not user text. (file, expression) -> why it is safe.
ALLOWED = {
    ("templates/dashboard.html", "b.label"): "BUCKETS is a constant list in this file",
    ("templates/dashboard.html", "note"): "built from numbers and the constant b.desc",
    ("templates/dashboard.html", "label"): "label comes from dueLabel(), a function of a number",
    ("templates/budgets.html", "b.label"): "BUCKETS is a constant list in this file",
    ("templates/alerts.html", """neu ? ' <span class="alert-dot" title="New"></span>' : \"\" """.strip()): "a constant HTML snippet chosen by a boolean",
    ("templates/forecast.html", """note ? " · " + esc(note) : \"\" """.strip()): "note is escaped inside the expression",
}

hits = []
files = [f for f in glob.glob(os.path.join(ROOT, "templates", "*.html")) + glob.glob(os.path.join(ROOT, "static", "js", "*.js"))]
for f in files:
    src = open(f, encoding="utf-8").read()
    rel = os.path.relpath(f, ROOT)
    # Names that were escaped where they were created, e.g.  const name = esc(b.name);
    escaped_names = set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*esc\(", src))
    for tm in TPL.finditer(src):
        body = tm.group(1)
        if "<" not in body:            # not HTML
            continue
        for em in EXPR.finditer(body):
            expr = em.group(1).strip()
            if SAFE_WRAP.match(expr):
                continue
            # Take out anything already escaped inside a bigger expression, then look for raw fields.
            stripped = re.sub(r"esc\((?:[^()]|\([^()]*\))*\)", "", expr)
            if not FIELD.search(stripped):
                continue
            if re.fullmatch(r"[A-Za-z_$][\w$]*", expr) and expr in escaped_names:
                continue
            if (rel, expr) in ALLOWED:
                continue
            line_no = src.count("\n", 0, tm.start(1) + em.start()) + 1
            line = src.splitlines()[line_no - 1]
            if "lint-ok" in line:
                continue
            hits.append((os.path.relpath(f, ROOT), line_no, expr))

if hits:
    print("Unescaped user text in HTML strings (wrap in esc(...)):")
    for f, n, e in hits:
        print(f"  {f}:{n}: ${{{e}}}")
    sys.exit(1)
print(f"innerhtml_lint: {len(files)} files checked, no unescaped user text in HTML strings.")
