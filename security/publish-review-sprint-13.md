# Publish-safety / security review — Sprint 13

Alerts centre, free on-device "Ask your money", friendly empty states, Indian
number-format polish. Reviewed against the standing per-sprint checklist.

## What changed (attack surface)

- **No new server endpoints.** `/alerts` and `/ask` are template routes only
  (`render_template`), like every other page. No new `/api/*`, no new request
  body is parsed, no new query parameter is read.
- **No new server input.** Alerts and answers are computed **in the browser**
  from rows the user already owns (`account_balances`, `budget_status`,
  `upcoming_recurring`, `goal_progress`, `transactions`, `categories`,
  `recurring`). Nothing the user types in the Ask box is sent anywhere — it is
  matched locally in `ask.js`. This is also why the feature is free (no AI/API).
- **One add-only migration, `0017_alert_prefs.sql`.** Adds a single `jsonb`
  column `alert_prefs` (default `'{}'`) to the existing `user_prefs` table. No
  table, column, policy, grant or function is dropped or rewritten.

## Checklist

- **API keys / secrets:** none added. No key, token or URL in client code,
  templates or git history for this change (`ask.js`/`alerts.js` are pure
  compute). Secret scan clean.
- **Env vars:** none added.
- **Admin routes:** none added.
- **Authentication:** both pages call `requireAuth()` via the shared shell, as
  every signed-in page does; signed-out users are redirected to `/login`.
- **Per-user isolation (RLS):** `alert_prefs` lives on `user_prefs`, whose
  existing per-user policies (`auth.uid() = user_id` for select/insert/update/
  delete) already scope every read and write of the new column. Verified by
  `tests/sprint13_prefs_test.sql`: user B cannot see or overwrite user A's
  settings, and saving `alert_prefs` leaves A's `home_layout` untouched.
- **Grants:** unchanged — `authenticated` keeps select/insert/update/delete on
  `user_prefs`, `anon` has none (asserted in the test).
- **Form / input sanitisation:** the Ask box value is never echoed as HTML; the
  page renders it through `esc()`. All alert and answer text rendered into the
  DOM goes through `esc()`. The settings write is a small, well-formed JSON
  object built in code, not from free text.
- **XSS:** no `innerHTML` is fed unescaped user content; question text, category
  names, bill names and account names are all `esc()`-wrapped before insertion.
- **Rate limiting:** not applicable — no server endpoint is added; the paid
  vision endpoint's limiter is unchanged.
- **CORS / origins / CSP:** unchanged. No new CDN, library, font or image
  origin. Charts are hand-drawn inline SVG (same approach as Sprint 12), and all
  scripts are ES modules served from our own origin. The existing CSP and the 7
  security headers are untouched (`tests/security_test.py` still passes).
- **Input bounds:** `alert_prefs` is guarded by two NOT-VALID check constraints
  — it must be a JSON object and at most 4096 bytes — so a runaway payload is
  rejected (asserted in the test). The browser only ever writes a small, fixed
  set of boolean/number keys.
- **Debug off:** no change to `app.py` debug configuration.
- **Dependencies:** none added (no new Python or JS packages, no new vendored
  file).
- **Exposed files:** none. The two new static JS files are app code; the two new
  templates are ordinary pages.
- **Passwords:** unchanged.

## Verdict

Low risk. The sprint adds client-side computation over data the user already
has, two template routes, and one add-only, RLS-covered settings column. No new
server input, endpoint, origin, secret or dependency. Full automated suite green
(parsers, scanner, DB incl. migration 0017, and the browser suites incl.
`sprint13_ui_test.py`).
