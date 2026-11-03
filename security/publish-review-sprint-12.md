# Publish-safety / security review — Sprint 12

Sprint 12 adds a cash-flow **Forecast**, a money-**Flow (Sankey)** view and
**trend sparklines** on Insights, and a **custom Home** where each card can be
shown, hidden and reordered, saved per account. This records the standing
security checklist re-run for the sprint (see memory:
security-checklist-standing-rule).

## What changed

- **Database (migration 0016, add-only):** one tiny table `user_prefs`
  (`user_id` primary key, `home_layout jsonb`, `updated_at`) holding each user's
  Home arrangement. No existing object is touched; no function, trigger or RPC
  is added.
- **Server (app.py):** one new route, `/forecast`, that only
  `render_template`s a page. No new request body is read, no new input is
  parsed, and no new external call is made.
- **Client:** a new Forecast page, a Flow + trends section on Insights, a
  rebuilt custom Home, and two helper modules (`forecast.js`, `charts.js`). All
  computation runs in the browser over data it already reads under RLS; the only
  new write is the user's own layout to `user_prefs`.

## Checklist

1. **RLS on the new table.** `user_prefs` has RLS enabled with
   `auth.uid() = user_id` policies for select/insert/update/delete. A second
   user can neither read nor overwrite another's row — asserted in
   `tests/sprint12_prefs_test.sql` (two-user isolation).
2. **Grants.** All rights on `user_prefs` are revoked from `anon` and granted
   only to `authenticated` (asserted in the SQL test). The anon key cannot read
   or write it.
3. **Input validation / payload bound.** `home_layout` is constrained to a JSON
   array and to `pg_column_size <= 8192` bytes, so a runaway or malformed
   payload is rejected at the database (both asserted). The browser only ever
   writes a small, well-formed array of `{id, show}` objects; an unknown card id
   is ignored and a missing card falls back to its default slot, so the feature
   degrades safely and needs no future migration to add a card.
4. **No new financial data reaches the server.** The Flask server still never
   sees the user's transactions or balances. Forecast, Sankey and sparklines are
   all computed client-side from data already read under RLS. No change to the
   CSP, security headers, rate limits, or the paid vision endpoint.
5. **No secrets, no new third-party origins.** The new charts are hand-drawn
   inline SVG (no charting library, no CDN). No new script, font, image or
   connect origin is introduced, so the CSP is unchanged.
6. **Output escaping.** Every user-supplied string rendered by the new code
   (account, category and bill names, chart and driver labels) goes through
   `esc()` or `textContent`; bucket labels come from a fixed map. The Sankey and
   sparkline SVGs are built from numeric geometry plus escaped labels only.
7. **No deletes, no destructive SQL.** Migration 0016 is strictly add-only
   (`create table if not exists`, new policies, grants). Nothing is dropped,
   altered or back-filled, so it carries no data-loss risk and runs within the
   thread's add-only authority.
8. **Demo mode parity.** The demo client gains an in-memory `user_prefs` store
   so the feature works with no login and nothing is persisted; it introduces no
   real credential or data path.

## Result

No new attack surface on the server. The one new table follows the same
RLS / authenticated-only pattern as every other table, is bounded against
oversized payloads, and is covered by SQL and UI tests (SQL isolation + 66
browser checks across light/dark and phone/desktop, plus the customize/reorder
persistence flow). Cleared to ship under the standing S9–S13 deploy approval.
