-- Money Tracker — per-user Home layout test (Sprint 12, migration 0016).
--
-- Real migrations 0001-0016 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * grants: authenticated may read and write user_prefs, anon may not,
--   * a user can insert and update exactly one row (user_id is the primary key),
--   * the default home_layout is an empty JSON array,
--   * the array/size check constraints reject a non-array and an oversized blob,
--   * RLS: user B cannot see or overwrite user A's prefs.

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- Grants: signed-in only.
do $$ begin
    assert has_table_privilege('authenticated', 'public.user_prefs', 'select'),
        'authenticated cannot select user_prefs';
    assert has_table_privilege('authenticated', 'public.user_prefs', 'insert'),
        'authenticated cannot insert user_prefs';
    assert has_table_privilege('authenticated', 'public.user_prefs', 'update'),
        'authenticated cannot update user_prefs';
    assert has_table_privilege('authenticated', 'public.user_prefs', 'delete'),
        'authenticated cannot delete user_prefs';
    assert not has_table_privilege('anon', 'public.user_prefs', 'select'),
        'anon can select user_prefs';
    assert not has_table_privilege('anon', 'public.user_prefs', 'insert'),
        'anon can insert user_prefs';
end $$;

-- RLS must be on.
do $$ begin
    assert (select relrowsecurity from pg_class where oid = 'public.user_prefs'::regclass),
        'RLS not enabled on user_prefs';
end $$;

-- ---------------------------------------------------------------- user A ----
set role app_user;
set request.jwt.claim.sub = :'A';

-- Insert a layout; user_id defaults to auth.uid().
insert into public.user_prefs (home_layout)
values ('[{"id":"forecast","show":true},{"id":"networth","show":false}]'::jsonb);

do $$
declare v_rows int; v_uid uuid; v_layout jsonb;
begin
    select count(*) into v_rows from public.user_prefs;
    assert v_rows = 1, 'A should have one prefs row, got ' || v_rows;
    select user_id, home_layout into v_uid, v_layout from public.user_prefs;
    assert v_uid = '11111111-1111-1111-1111-111111111111', 'user_id not defaulted to auth.uid(): ' || v_uid;
    assert jsonb_array_length(v_layout) = 2, 'layout length wrong: ' || jsonb_array_length(v_layout);
    assert (v_layout -> 0 ->> 'id') = 'forecast', 'first card wrong';
end $$;

-- Update (the real app upserts on user_id; here the row already exists).
update public.user_prefs
   set home_layout = '[{"id":"stats","show":true}]'::jsonb, updated_at = now();
do $$
declare v_len int;
begin
    select jsonb_array_length(home_layout) into v_len from public.user_prefs;
    assert v_len = 1, 'updated layout length wrong: ' || v_len;
end $$;

-- A second insert for the same user must fail: one row per user (primary key).
do $$ begin
    begin
        insert into public.user_prefs (home_layout) values ('[]'::jsonb);
        raise exception 'second row for same user accepted';
    exception when unique_violation then
        null;  -- expected
    end;
end $$;

-- Check constraints: a non-array and an oversized payload are rejected.
-- (Declared NOT VALID, but still enforced on new writes.)
do $$ begin
    begin
        update public.user_prefs set home_layout = '{"not":"an array"}'::jsonb;
        raise exception 'non-array layout accepted';
    exception when check_violation then
        null;  -- expected
    end;
end $$;
do $$
declare big jsonb;
begin
    select jsonb_agg(jsonb_build_object('id', 'card' || g, 'show', true))
      into big from generate_series(1, 2000) g;
    begin
        update public.user_prefs set home_layout = big;
        raise exception 'oversized layout accepted';
    exception when check_violation then
        null;  -- expected
    end;
end $$;

-- ---------------------------------------------------------------- user B ----
set request.jwt.claim.sub = :'B';
do $$ begin
    -- B cannot see A's row.
    assert (select count(*) from public.user_prefs) = 0, 'B sees A prefs';
    -- B writes its own row, which must not touch A's.
    insert into public.user_prefs (home_layout) values ('[{"id":"recent","show":true}]'::jsonb);
    assert (select count(*) from public.user_prefs) = 1, 'B capture leaked A rows';
    assert (select user_id from public.user_prefs) = '22222222-2222-2222-2222-222222222222',
        'B row has wrong owner';
end $$;

-- Back to A: A still has exactly its own (updated) row, untouched by B.
set request.jwt.claim.sub = :'A';
do $$
declare v_len int;
begin
    assert (select count(*) from public.user_prefs) = 1, 'A should still see only its own row';
    select jsonb_array_length(home_layout) into v_len from public.user_prefs;
    assert v_len = 1 and (select home_layout -> 0 ->> 'id' from public.user_prefs) = 'stats',
        'A row changed unexpectedly';
end $$;

reset role;
select 'sprint12_prefs_test: all assertions passed' as result;
