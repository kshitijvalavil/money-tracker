-- Money Tracker — multi-user data-isolation test.
--
-- The whole point of adding testers is that a new user must NEVER see, change
-- or delete anything that belongs to another user. This test proves exactly
-- that, across EVERY table the app stores data in (transactions, budgets,
-- goals, net worth / investments, alert settings, labels, rules, imports, the
-- lot — everything the S1-S13 roadmap added).
--
-- It runs the real migrations 0001-0017 against a throwaway PostgreSQL with the
-- usual tiny Supabase mock (the `auth` schema + `auth.uid()`), seeds two users
-- A (the "owner") and B (a freshly added tester), fills A's account with one
-- row in every table, then — acting as B through Row Level Security — checks:
--   * B sees ZERO of A's rows in every table,
--   * B sees only B's own seeded starter data (12 categories + a Cash account),
--   * B cannot UPDATE or DELETE any of A's rows,
--   * B cannot INSERT a row attributed to A (write-side isolation),
--   * after B adds their own data, A still sees only A's.
--
-- Run it with tests/run.sh (which starts a throwaway Postgres).

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'

-- ---- Mock the parts of Supabase we rely on --------------------------------
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

-- ---- The real migrations ---------------------------------------------------
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
\i migrations/0021_audit_and_consent.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all routines in schema public to app_user;
grant authenticated to app_user;

-- ---- Two users; the trigger seeds each with categories + a Cash account -----
insert into auth.users (id, email) values (:'A', 'owner@example.com'), (:'B', 'tester@example.com');

do $$
declare n int;
begin
    select count(*) into n from public.categories where user_id = '11111111-1111-1111-1111-111111111111';
    assert n = 12, format('owner should start with 12 seeded categories, got %s', n);
    select count(*) into n from public.categories where user_id = '22222222-2222-2222-2222-222222222222';
    assert n = 12, format('new tester should start with 12 seeded categories, got %s', n);
end $$;

-- ---- As the owner (A): put one real row in EVERY data table ----------------
set role app_user;
set request.jwt.claim.sub = :'A';
do $$
declare
    v_acct uuid; v_food uuid; v_tx uuid; v_goal uuid; v_label uuid;
begin
    select id into v_acct from public.accounts where name = 'Cash';
    select id into v_food from public.categories where name = 'Food' and kind = 'expense';

    insert into public.accounts (name, type, opening_balance) values ('Owner Bank', 'bank', 50000);
    insert into public.transactions (amount, type, account_id, category_id, note)
        values (1500, 'expense', v_acct, v_food, 'owner lunch') returning id into v_tx;
    insert into public.budgets (category_id, monthly_limit) values (v_food, 8000);
    insert into public.goals (name, target_amount) values ('Owner Emergency Fund', 500000) returning id into v_goal;
    insert into public.goal_contributions (goal_id, amount) values (v_goal, 25000);
    insert into public.labels (name) values ('owner-private') returning id into v_label;
    insert into public.transaction_labels (transaction_id, label_id) values (v_tx, v_label);
    insert into public.investments (name, kind, invested_amount, current_value)
        values ('Owner Mutual Fund', 'mutual_fund', 200000, 260000);
    insert into public.category_rules (match_text, category_id) values ('swiggy', v_food);
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('Owner Rent', 'expense', 30000, v_acct, v_food, 'monthly', current_date, current_date);
    insert into public.sync_status (last_found, last_added) values (5, 2);
    insert into public.net_worth_snapshots (as_of_month, assets, liabilities, net)
        values (date_trunc('month', current_date)::date, 100, 20, 80);
    insert into public.audit_log (event) values ('sign_in');
    insert into public.user_consents (notice_version) values ('2026-11-07');
    insert into public.user_prefs (home_layout, alert_prefs)
        values ('[{"id":"forecast","show":true}]'::jsonb, '{"low_balance":true,"low_floor":10000}'::jsonb);
    insert into public.pending_imports (source, direction, amount, occurred_on, note, source_message_id)
        values ('sbi_alert', 'expense', 999.00, current_date, 'owner import', 'owner-msg-1');
end $$;

-- Owner can see their own data (sanity: the rows really exist under RLS).
do $$
declare n int;
begin
    assert (select count(*) from public.transactions) = 1, 'owner cannot see own transaction';
    assert (select count(*) from public.investments) = 1, 'owner cannot see own investment';
    assert (select count(*) from public.goals) = 1, 'owner cannot see own goal';
    assert (select count(*) from public.accounts) = 2, 'owner should see Cash + Owner Bank';
end $$;

-- Stash the owner's real account + category ids (as superuser, bypassing RLS)
-- so the write-injection test below can try to plant a row using genuine ids.
reset role;
select set_config('test.owner_acct',
    (select id::text from public.accounts where user_id = '11111111-1111-1111-1111-111111111111' and name = 'Cash'), false);
select set_config('test.owner_cat',
    (select id::text from public.categories where user_id = '11111111-1111-1111-1111-111111111111' and kind = 'expense' limit 1), false);
set role app_user;

-- ===========================================================================
-- THE TEST: act as the newly added tester (B) and prove total isolation.
-- ===========================================================================
set request.jwt.claim.sub = :'B';

-- 1) Across EVERY table, B must see ZERO rows that belong to the owner.
do $$
declare
    t text; n bigint;
    owner uuid := '11111111-1111-1111-1111-111111111111';
    tbls text[] := array[
        'accounts','budgets','categories','category_rules','goal_contributions',
        'goals','investments','labels','net_worth_snapshots','pending_imports','recurring','sync_status',
        'transaction_labels','transactions','user_prefs','audit_log','user_consents'];
begin
    foreach t in array tbls loop
        execute format('select count(*) from public.%I where user_id = $1', t) into n using owner;
        assert n = 0, format('ISOLATION LEAK: as the tester, saw %s of the owner''s rows in %s', n, t);
    end loop;
end $$;

-- 2) B sees only B's own starter data, nothing else.
do $$ begin
    assert (select count(*) from public.categories) = 12, 'tester should see only their own 12 categories';
    assert (select count(*) from public.accounts) = 1, 'tester should see only their own Cash account';
    assert (select name from public.accounts) = 'Cash', 'tester can see an account that is not theirs';
    assert (select count(*) from public.transactions) = 0, 'tester can see transactions that are not theirs';
    assert (select count(*) from public.budgets) = 0, 'tester can see budgets that are not theirs';
    assert (select count(*) from public.goals) = 0, 'tester can see goals that are not theirs';
    assert (select count(*) from public.goal_contributions) = 0, 'tester can see goal contributions that are not theirs';
    assert (select count(*) from public.investments) = 0, 'tester can see investments that are not theirs';
    assert (select count(*) from public.labels) = 0, 'tester can see labels that are not theirs';
    assert (select count(*) from public.transaction_labels) = 0, 'tester can see tx labels that are not theirs';
    assert (select count(*) from public.recurring) = 0, 'tester can see recurring bills that are not theirs';
    assert (select count(*) from public.category_rules) = 0, 'tester can see rules that are not theirs';
    assert (select count(*) from public.pending_imports) = 0, 'tester can see email imports that are not theirs';
    assert (select count(*) from public.sync_status) = 0, 'tester can see sync status that is not theirs';
    assert (select count(*) from public.user_prefs) = 0, 'tester can see prefs/alert settings that are not theirs';
    assert (select count(*) from public.net_worth_snapshots) = 0, 'tester can see net-worth history that is not theirs';
    assert (select count(*) from public.audit_log) = 0, 'tester can see security activity that is not theirs';
    assert (select count(*) from public.user_consents) = 0, 'tester can see consent records that are not theirs';
end $$;

-- 3) B cannot CHANGE or DELETE the owner's rows (RLS hides them from writes too).
do $$
declare n int;
begin
    update public.transactions set note = 'tampered' where user_id = '11111111-1111-1111-1111-111111111111';
    get diagnostics n = row_count; assert n = 0, format('tester updated %s of the owner''s transactions', n);
    update public.user_prefs set alert_prefs = '{"low_balance":false}'::jsonb
        where user_id = '11111111-1111-1111-1111-111111111111';
    get diagnostics n = row_count; assert n = 0, format('tester changed %s of the owner''s alert settings', n);
    delete from public.budgets where user_id = '11111111-1111-1111-1111-111111111111';
    get diagnostics n = row_count; assert n = 0, format('tester deleted %s of the owner''s budgets', n);
    delete from public.investments where user_id = '11111111-1111-1111-1111-111111111111';
    get diagnostics n = row_count; assert n = 0, format('tester deleted %s of the owner''s investments', n);
end $$;

-- 4) B cannot INSERT a row attributed to the owner (write-side ownership check).
do $$
begin
    -- A category planted under the owner's id is refused.
    begin
        insert into public.categories (user_id, name, kind)
            values ('11111111-1111-1111-1111-111111111111', 'injected', 'expense');
        raise exception 'tester inserted a category owned by the owner';
    exception when insufficient_privilege then null; end;

    -- Even with the owner's genuine account + category ids, a transaction planted
    -- under the owner's id is refused (covers the restrictive recurring-link policy).
    begin
        insert into public.transactions (user_id, amount, type, account_id, category_id)
            values ('11111111-1111-1111-1111-111111111111', 10, 'expense',
                    current_setting('test.owner_acct')::uuid,
                    current_setting('test.owner_cat')::uuid);
        raise exception 'tester inserted a transaction owned by the owner';
    exception when insufficient_privilege then null; end;
end $$;

-- 5) B can use the app normally with their own data, and the owner is untouched.
do $$
declare v_acct uuid; v_cat uuid;
begin
    select id into v_acct from public.accounts where name = 'Cash';
    select id into v_cat from public.categories where name = 'Food' and kind = 'expense';
    insert into public.transactions (amount, type, account_id, category_id, note)
        values (250, 'expense', v_acct, v_cat, 'tester groceries');
    insert into public.goals (name, target_amount) values ('Tester Trip', 100000);
    assert (select count(*) from public.transactions) = 1, 'tester cannot see their own new transaction';
    assert (select count(*) from public.goals) = 1, 'tester cannot see their own new goal';
end $$;

-- The owner still sees exactly their own data, unchanged by anything B did.
set request.jwt.claim.sub = :'A';
do $$ begin
    assert (select count(*) from public.transactions) = 1, 'owner transaction count changed';
    assert (select note from public.transactions) = 'owner lunch', 'owner transaction was tampered with';
    assert (select count(*) from public.goals) = 1, 'owner goal count changed';
    assert (select count(*) from public.budgets) = 1, 'owner budget was deleted by the tester';
    assert (select count(*) from public.investments) = 1, 'owner investment was deleted by the tester';
    assert (select (alert_prefs ->> 'low_floor') from public.user_prefs) = '10000',
        'owner alert settings were changed by the tester';
end $$;

reset role;
\echo ''
\echo '========================================'
\echo '  USER ISOLATION TEST: ALL PASSED'
\echo '========================================'
