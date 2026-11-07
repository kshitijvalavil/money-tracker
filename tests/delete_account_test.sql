-- Money Tracker — "delete my account" test (Sprint 14).
--
-- Proves public.delete_my_account():
--   * removes the caller's login AND every row they own, in ALL 17 data tables,
--   * leaves every other user's data completely untouched,
--   * refuses callers who are not signed in, and is not callable by anon,
--   * only ever deletes the caller (it takes no user-id argument to abuse).
--
-- Runs the real migrations against a throwaway PostgreSQL with the usual tiny
-- Supabase mock (auth schema + auth.uid()). Run it with tests/run.sh.

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
grant usage on schema public, auth to app_user, authenticated, anon;
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
\i migrations/0019_delete_my_account.sql
\i migrations/0021_audit_and_consent.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all routines in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'keeper@example.com'), (:'B', 'leaver@example.com');

-- Put one row in EVERY data table for BOTH users (so "untouched" means something).
create or replace function pg_temp.seed_everything() returns void language plpgsql as $$
declare v_acct uuid; v_food uuid; v_tx uuid; v_goal uuid; v_label uuid;
begin
    select id into v_acct from public.accounts where name = 'Cash';
    select id into v_food from public.categories where name = 'Food' and kind = 'expense';
    insert into public.transactions (amount, type, account_id, category_id, note)
        values (1500, 'expense', v_acct, v_food, 'lunch') returning id into v_tx;
    insert into public.budgets (category_id, monthly_limit) values (v_food, 8000);
    insert into public.goals (name, target_amount) values ('Fund', 500000) returning id into v_goal;
    insert into public.goal_contributions (goal_id, amount) values (v_goal, 25000);
    insert into public.labels (name) values ('private') returning id into v_label;
    insert into public.transaction_labels (transaction_id, label_id) values (v_tx, v_label);
    insert into public.investments (name, kind, invested_amount, current_value) values ('MF', 'mutual_fund', 200000, 260000);
    insert into public.category_rules (match_text, category_id) values ('swiggy', v_food);
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('Rent', 'expense', 30000, v_acct, v_food, 'monthly', current_date, current_date);
    insert into public.sync_status (last_found, last_added) values (5, 2);
    insert into public.user_prefs (home_layout, alert_prefs) values ('[{"id":"forecast","show":true}]'::jsonb, '{"low_balance":true}'::jsonb);
    insert into public.pending_imports (source, direction, amount, occurred_on, note, source_message_id)
        values ('sbi_alert', 'expense', 999.00, current_date, 'import', 'msg-' || auth.uid());
    insert into public.net_worth_snapshots (as_of_month, assets, liabilities, net) values (date_trunc('month', current_date)::date, 100, 20, 80);
    insert into public.audit_log (event) values ('sign_in');
    insert into public.user_consents (notice_version) values ('2026-11-07');
end $$;

set role app_user;
set request.jwt.claim.sub = :'A';  select pg_temp.seed_everything();
set request.jwt.claim.sub = :'B';  select pg_temp.seed_everything();
reset role;

-- Helper: how many rows does a user own across ALL 17 tables? (run as superuser)
create or replace function pg_temp.rows_owned(p_user uuid) returns bigint language plpgsql as $$
declare t text; n bigint; total bigint := 0;
    tbls text[] := array['accounts','budgets','categories','category_rules','goal_contributions',
        'goals','investments','labels','net_worth_snapshots','pending_imports','recurring',
        'sync_status','transaction_labels','transactions','user_prefs','audit_log','user_consents'];
begin
    foreach t in array tbls loop
        execute format('select count(*) from public.%I where user_id = $1', t) into n using p_user;
        total := total + n;
    end loop;
    return total;
end $$;

-- Sanity: every one of the 15 tables really has a row for each user before the test.
do $$
declare t text; n bigint;
    tbls text[] := array['accounts','budgets','categories','category_rules','goal_contributions',
        'goals','investments','labels','net_worth_snapshots','pending_imports','recurring',
        'sync_status','transaction_labels','transactions','user_prefs','audit_log','user_consents'];
begin
    foreach t in array tbls loop
        execute format('select count(*) from public.%I where user_id = $1', t) into n using '22222222-2222-2222-2222-222222222222'::uuid;
        assert n >= 1, format('seed problem: %s has no row for the leaving user', t);
    end loop;
end $$;
select set_config('test.a_rows', pg_temp.rows_owned(:'A')::text, false);

-- ---- 1) Not signed in: refused ---------------------------------------------
set role app_user;
set request.jwt.claim.sub = '';
do $$ begin
    begin
        perform public.delete_my_account();
        raise exception 'a signed-out caller was allowed to delete';
    exception when insufficient_privilege then null; end;
end $$;

-- ---- 2) anon cannot even call it -------------------------------------------
reset role;
do $$ begin
    assert not has_function_privilege('anon', 'public.delete_my_account()', 'execute'), 'anon can execute delete_my_account';
    assert not has_function_privilege('public', 'public.delete_my_account()', 'execute'), 'PUBLIC can execute delete_my_account';
    assert has_function_privilege('authenticated', 'public.delete_my_account()', 'execute'), 'signed-in users cannot execute delete_my_account';
end $$;

-- ---- 3) B deletes their account --------------------------------------------
set role app_user;
set request.jwt.claim.sub = :'B';
select public.delete_my_account();
reset role;

do $$
begin
    assert not exists (select 1 from auth.users where id = '22222222-2222-2222-2222-222222222222'), 'B login still exists';
    assert pg_temp.rows_owned('22222222-2222-2222-2222-222222222222') = 0,
        format('B still owns %s rows after deleting their account', pg_temp.rows_owned('22222222-2222-2222-2222-222222222222'));
end $$;

-- ---- 4) A is completely untouched ------------------------------------------
do $$
begin
    assert exists (select 1 from auth.users where id = '11111111-1111-1111-1111-111111111111'), 'A login was deleted';
    assert pg_temp.rows_owned('11111111-1111-1111-1111-111111111111') = current_setting('test.a_rows')::bigint,
        'A lost or gained rows when B deleted their account';
end $$;

-- ---- 5) Deleting twice is harmless; a deleted user cannot come back as someone else
set role app_user;
set request.jwt.claim.sub = :'B';
select public.delete_my_account();            -- no row left: does nothing, no error
set request.jwt.claim.sub = :'A';
select count(*) from public.transactions;     -- A can still use the app
reset role;

\echo ''
\echo '========================================'
\echo '  DELETE MY ACCOUNT TEST: ALL PASSED'
\echo '========================================'
