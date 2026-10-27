-- Sprint 2 release gate: fixture for the double-posting race tests that
-- tests/sprint2_adversarial.sh drives with several concurrent psql sessions.
-- User A has five bills: four monthly from 31 Jan 2026 (so as of
-- 30 Jun 2026 six occurrences are due: 31 Jan, 28 Feb, 31 Mar, 30 Apr, 31 May,
-- 30 Jun), plus a weekly one (57 occurrences due by 30 Jun, under the 60 cap).
-- Only one bill has auto_post on at a time; the runner flips it per scenario.
\set ON_ERROR_STOP on
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'app_user') then create role app_user nologin; end if;
end $$;
grant authenticated to app_user;
grant usage on schema public, auth to authenticated;
grant select on auth.users to authenticated;

\i supabase/migrations/0001_schema.sql
\i supabase/migrations/0002_rls.sql
\i supabase/migrations/0003_functions.sql
\i supabase/migrations/0004_seed_trigger.sql
\i supabase/migrations/0005_imports.sql
\i supabase/migrations/0006_approve_import.sql
\i supabase/migrations/0007_recurring.sql
grant select, insert, update, delete on all tables in schema public to authenticated;

insert into auth.users (id, email) values
    ('11111111-1111-1111-1111-111111111111', 'a@x.com'),
    ('22222222-2222-2222-2222-222222222222', 'b@x.com');
set role app_user;
set request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due, auto_post)
select n, 'expense', 100, (select id from public.accounts where name = 'Cash'),
       (select id from public.categories where name = 'Bills'), f, d, d, n = 'race-post-post'
  from (values ('race-post-post', 'monthly', date '2026-01-31'),
               ('race-pay-then-post', 'monthly', date '2026-01-31'),
               ('race-post-then-pay', 'monthly', date '2026-01-31'),
               ('race-pay-pay', 'monthly', date '2026-01-31'),
               ('race-storm', 'weekly', date '2025-06-01')) v(n, f, d);
-- B has a bill too; B's concurrent calls must never touch A's rows.
set request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due, auto_post)
select 'race-b', 'expense', 100, (select id from public.accounts where name = 'Cash'),
       (select id from public.categories where name = 'Bills'), 'monthly', '2026-01-31', '2026-01-31', true;
reset role;
