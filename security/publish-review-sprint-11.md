# Publish-safety / security review — Sprint 11

Sprint 11 adds the big-picture views: net worth over time, a Wealth page, a
bills calendar, and a goals forecast. This records the standing security
checklist re-run for the sprint (see memory: security-checklist-standing-rule).

## What changed

- **Database (migration 0015, add-only):** a `net_worth_snapshots` table and
  four functions — `net_worth_breakdown()`, `net_worth_series(p_months)`,
  `capture_net_worth_snapshot()`, `goal_forecast()`.
- **Server (app.py):** three new routes that only `render_template` a page
  (`/net-worth`, `/wealth`, `/calendar`). No new request body is read, no new
  input is parsed, and no new external call is made.
- **Client:** three templates plus a goals upgrade; all data access is the
  browser talking to Supabase under RLS, exactly as the rest of the app.

## Checklist

1. **RLS on new tables.** `net_worth_snapshots` has RLS enabled with
   `user_id = auth.uid()` policies for select/insert/update/delete and a
   `unique (user_id, as_of_month)`. A second user sees nothing (asserted in
   `tests/net_worth_test.sql`).
2. **Function privilege.** Every new function is `SECURITY INVOKER` with
   `set search_path = ''` and fully schema-qualified names, so it runs as the
   caller and cannot be hijacked via the search path.
3. **Grants.** `execute` on each function and all rights on the table are
   revoked from `public` and `anon` and granted only to `authenticated`
   (asserted in the SQL test). An anonymous key cannot call them.
4. **Input validation.** `net_worth_series` rejects `p_months` outside 1..60
   (SQLSTATE 22023); `capture_net_worth_snapshot` raises 42501 when there is no
   signed-in user. Money stays `numeric(14,2)`.
5. **No data to the server.** The Flask server still never sees the user's
   financial data; the new pages read it client-side under RLS. No change to
   the CSP, headers, rate limits, or the paid vision endpoint.
6. **No secrets, no new third-party origins.** Charts use the already-vendored
   Chart.js; no new script, font, or connect origin, so the CSP is unchanged.
7. **Output escaping.** All user-supplied strings (account, holding and goal
   names) are rendered through `esc()` / `textContent`; chart labels come from
   a fixed map.

## Result

No new attack surface on the server; new SQL follows the same invoker /
search-path / authenticated-only pattern as Sprint 10 and is covered by SQL and
UI tests. Cleared to ship under the standing S9–S13 deploy approval.
