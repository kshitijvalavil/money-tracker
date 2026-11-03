-- Money Tracker — per-user alert settings (Sprint 13)
--
-- Sprint 13 adds an Alerts centre: which alert types are on and their
-- thresholds (budget overspend %, low-balance floor, bill lead days, …). That
-- choice is personal, so it rides alongside the Home layout in the same tiny
-- per-user table added in Sprint 12. One extra JSON column, e.g.
--   {"budget_overspend":true,"low_balance":true,"low_floor":5000, ...}
--
-- The alerts themselves are computed in the browser from the user's own rows,
-- so nothing new is sent to the server; only this small settings blob is saved.
-- The shape is validated in the browser and tolerated loosely here (unknown
-- keys ignored, missing keys fall back to defaults), so a later release can add
-- an alert type without another migration.
--
-- Add-only: the table and its RLS already exist (0016). This only adds a
-- nullable JSON column with a default and two backstop checks. Nothing is
-- dropped or rewritten, and existing rows keep working (the column defaults to
-- an empty object, which the browser reads as "use the defaults").

alter table public.user_prefs
    add column if not exists alert_prefs jsonb not null default '{}'::jsonb;

-- Backstops, not the real validation (the browser only ever writes a small,
-- well-formed object). NOT VALID so the add never has to scan existing rows.
alter table public.user_prefs
    add constraint user_prefs_alerts_is_object check (jsonb_typeof(alert_prefs) = 'object') not valid;
alter table public.user_prefs
    add constraint user_prefs_alerts_small check (pg_column_size(alert_prefs) <= 4096) not valid;

-- RLS and grants are unchanged: the existing per-user policies on user_prefs
-- already scope every read and write of this column to the signed-in user, and
-- anon has no access. No new grant is needed.
