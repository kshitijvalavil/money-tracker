-- Money Tracker — per-user alert settings test (Sprint 13, migration 0017).
--
-- Real migrations 0001-0017 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * the alert_prefs column exists on user_prefs with a '{}' object default,
--   * the object/size check constraints reject a non-object and an oversized blob,
--   * grants are unchanged (authenticated read/write, anon none),
--   * RLS still scopes alert_prefs per user: B cannot see or overwrite A's prefs,
--   * saving alert_prefs leaves an existing home_layout untouched (same row).

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'

create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'app_user') then create role app_user nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
grant usage on schema public, auth to app_user, authenticated;
grant select on auth.users to app_user;
grant execute on function auth.uid() to public;

\i migrations/0001_schema.sql
\i migrations/0002_rls.sql
\i migrations/0003_functions.sql
\i migrations/0004_seed_trigger.sql
\i migrations/0005_imports.sql
\i migrations/0006_approve_import.sql
\i migrations/0007_recurring.sql
\i migrations/0008_suggest_recurring.sql
\i migrations/0009_goals_labels.sql
\i migrations/0010_investments.sql
\i migrations/0011_import_channel.sql
\i migrations/0012_security_hardening.sql
\i migrations/0013_review_inbox.sql
\i migrations/0014_flex_budget.sql
\i migrations/0015_net_worth.sql
\i migrations/0016_home_layout.sql
\i migrations/0017_alert_prefs.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- Column exists with the right type and a '{}' default.
do $$
declare v_default text; v_type text;
begin
    select data_type into v_type from information_schema.columns
      where table_schema = 'public' and table_name = 'user_prefs' and column_name = 'alert_prefs';
    assert v_type = 'jsonb', 'alert_prefs is not jsonb: ' || coalesce(v_type, 'MISSING');
    select column_default into v_default from information_schema.columns
      where table_schema = 'public' and table_name = 'user_prefs' and column_name = 'alert_prefs';
    assert v_default like '%{}%', 'alert_prefs default is not an empty object: ' || coalesce(v_default, 'NULL');
end $$;

-- Grants unchanged: signed-in read/write, anon none.
do $$ begin
    assert has_table_privilege('authenticated', 'public.user_prefs', 'select'), 'authenticated cannot select user_prefs';
    assert has_table_privilege('authenticated', 'public.user_prefs', 'update'), 'authenticated cannot update user_prefs';
    assert not has_table_privilege('anon', 'public.user_prefs', 'select'), 'anon can select user_prefs';
    assert not has_table_privilege('anon', 'public.user_prefs', 'insert'), 'anon can insert user_prefs';
end $$;

-- ---------------------------------------------------------------- user A ----
set role app_user;
set request.jwt.claim.sub = :'A';

-- A row with a home layout but no alert prefs: alert_prefs defaults to '{}'.
insert into public.user_prefs (home_layout)
values ('[{"id":"forecast","show":true}]'::jsonb);
do $$
declare v jsonb;
begin
    select alert_prefs into v from public.user_prefs;
    assert v = '{}'::jsonb, 'alert_prefs did not default to {}: ' || v::text;
end $$;

-- Save alert settings; the home layout must be untouched (what the app's
-- upsert of just {user_id, alert_prefs} does).
update public.user_prefs
   set alert_prefs = '{"budget_overspend":true,"low_balance":false,"low_floor":10000}'::jsonb,
       updated_at = now();
do $$
declare v jsonb; layout jsonb;
begin
    select alert_prefs, home_layout into v, layout from public.user_prefs;
    assert (v ->> 'low_floor') = '10000', 'low_floor not saved';
    assert (v -> 'low_balance')::text = 'false', 'low_balance not saved';
    assert jsonb_array_length(layout) = 1 and (layout -> 0 ->> 'id') = 'forecast',
        'saving alert_prefs changed the home layout';
end $$;

-- Check constraints: a non-object and an oversized payload are rejected.
do $$ begin
    begin
        update public.user_prefs set alert_prefs = '[1,2,3]'::jsonb;
        raise exception 'non-object alert_prefs accepted';
    exception when check_violation then null; end;
end $$;
do $$
declare big jsonb;
begin
    select jsonb_object_agg('key_' || g, repeat('x', 20)) into big from generate_series(1, 500) g;
    begin
        update public.user_prefs set alert_prefs = big;
        raise exception 'oversized alert_prefs accepted';
    exception when check_violation then null; end;
end $$;

-- ---------------------------------------------------------------- user B ----
set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.user_prefs) = 0, 'B sees A prefs';
    insert into public.user_prefs (alert_prefs) values ('{"weekly_digest":true}'::jsonb);
    assert (select count(*) from public.user_prefs) = 1, 'B capture leaked A rows';
    assert (select user_id from public.user_prefs) = '22222222-2222-2222-2222-222222222222',
        'B row has wrong owner';
end $$;

-- A still sees only its own, unchanged settings.
set request.jwt.claim.sub = :'A';
do $$
declare v jsonb;
begin
    assert (select count(*) from public.user_prefs) = 1, 'A should still see only its own row';
    select alert_prefs into v from public.user_prefs;
    assert (v ->> 'low_floor') = '10000', 'A alert_prefs changed unexpectedly';
end $$;

reset role;
select 'sprint13_prefs_test: all assertions passed' as result;
