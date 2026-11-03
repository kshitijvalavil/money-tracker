-- Money Tracker — flex budget test (Sprint 10, migration 0014).
--
-- Real migrations 0001-0014 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * categories.bucket default + check constraint; Rent/Bills moved to fixed,
--   * budgets.rollover default off,
--   * flex_overview: per-bucket budgeted and spent, bad month rejected,
--   * category_rollovers: carried pot = unspent over prior months, floored at
--     zero; off when rollover is off; current-month spend not counted,
--   * monthly_recap: saved, savings rate, biggest jump / best saver vs prior
--     month, rolled-over pot, flexible left,
--   * grants: authenticated may call, anon may not,
--   * user B never sees A's buckets, budgets or rollovers.

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

-- Seed two users through the sign-up trigger, so buckets come from 0014.
insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- Grants: signed-in only.
do $$ begin
    assert has_function_privilege('authenticated', 'public.flex_overview(text)', 'execute'),
        'authenticated cannot execute flex_overview';
    assert has_function_privilege('authenticated', 'public.category_rollovers(text)', 'execute'),
        'authenticated cannot execute category_rollovers';
    assert has_function_privilege('authenticated', 'public.monthly_recap(text)', 'execute'),
        'authenticated cannot execute monthly_recap';
    assert not has_function_privilege('anon', 'public.flex_overview(text)', 'execute'),
        'anon can execute flex_overview';
    assert not has_function_privilege('anon', 'public.monthly_recap(text)', 'execute'),
        'anon can execute monthly_recap';
end $$;

-- ---------------------------------------------------------------- user A ----
-- Everything below runs as app_user, so Row Level Security scopes each query
-- to the current user (A, then B) without naming the UUID inside a plpgsql
-- block — psql does not substitute :'A' inside dollar-quoted DO blocks.
set role app_user;
set request.jwt.claim.sub = :'A';

-- Starter buckets from the trigger: Rent and Bills are fixed, the rest flexible.
do $$ begin
    assert (select bucket from public.categories where name = 'Rent')  = 'fixed',    'Rent not fixed';
    assert (select bucket from public.categories where name = 'Bills') = 'fixed',    'Bills not fixed';
    assert (select bucket from public.categories where name = 'Food')  = 'flexible', 'Food not flexible';
    assert (select count(*) from public.categories where bucket not in ('fixed','flexible','non_monthly')) = 0,
        'bad bucket slipped in';
end $$;

-- The bucket check constraint holds.
do $$ begin
    begin
        update public.categories set bucket = 'nope' where name = 'Food';
        raise exception 'bad bucket accepted';
    exception when check_violation then null;
    end;
end $$;

-- A cash account to spend from.
insert into public.accounts (name, type, opening_balance) values ('Bank', 'bank', 100000);

-- Put Food in flexible (already), Travel into non_monthly with rollover on.
insert into public.categories (name, kind, bucket) values ('Travel', 'expense', 'non_monthly');

-- Budgets: Food flexible 10000 (no rollover), Travel 5000 (rollover on).
insert into public.budgets (category_id, monthly_limit, rollover)
select id, 10000, false from public.categories where name = 'Food';
insert into public.budgets (category_id, monthly_limit, rollover)
select id, 5000, true from public.categories where name = 'Travel';

-- rollover defaults off for a budget inserted without it.
insert into public.budgets (category_id, monthly_limit)
select id, 2000 from public.categories where name = 'Shopping';
do $$ begin
    assert (select rollover from public.budgets b join public.categories c on c.id = b.category_id
             where c.name = 'Shopping') = false, 'rollover default not false';
end $$;

-- Helper to add an expense in a category on a given date.
-- Prior months: Travel spent 1000 (Sep) and 2000 (Oct) against a 5000 limit,
-- so the rollover pot entering Nov is (5000-1000)+(5000-2000)=4000 for those two
-- months, plus 5000 for each of the other ten prior months in the 12-month
-- window = 4000 + 50000 = 54000. We only assert the two-month shape below by
-- using dates far in the past get no limit... simpler: assert pot >= 4000 and
-- that current-month spend is excluded.
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-09-10', 1000, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Travel';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-10-12', 2000, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Travel';

-- Current month (2025-11): income 80000, Food 4200, Travel 1500, Shopping 900.
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-01', 80000, 'income', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Salary';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-05', 4200, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Food';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-08', 1500, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Travel';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-09', 900, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Shopping';
-- Transport is flexible but has NO budget; its spend must NOT count toward the
-- flexible bucket total or "left to spend freely", only toward total expense.
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-07', 700, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Transport';

-- flex_overview for 2025-11. Spent counts budgeted categories only, so the
-- unbudgeted Transport 700 is excluded from the flexible total.
--   flexible budgeted = Food 10000 + Shopping 2000 = 12000; spent = 4200 + 900 = 5100
--   non_monthly budgeted = Travel 5000; spent = 1500
--   fixed budgeted = 0; spent = 0
do $$
declare r record;
begin
    select * into r from public.flex_overview('2025-11') where bucket = 'flexible';
    assert r.budgeted = 12000, 'flexible budgeted wrong: ' || r.budgeted;
    assert r.spent    = 5100,  'flexible spent wrong: ' || r.spent;
    select * into r from public.flex_overview('2025-11') where bucket = 'non_monthly';
    assert r.budgeted = 5000,  'non_monthly budgeted wrong: ' || r.budgeted;
    assert r.spent    = 1500,  'non_monthly spent wrong: ' || r.spent;
    select * into r from public.flex_overview('2025-11') where bucket = 'fixed';
    assert r.budgeted = 0 and r.spent = 0, 'fixed should be empty';
end $$;

-- flex_overview rejects a bad month.
do $$ begin
    begin
        perform public.flex_overview('2025/11');
        raise exception 'bad month accepted';
    exception when others then
        assert sqlstate = '22023', 'wrong error for bad month: ' || sqlstate;
    end;
end $$;

-- category_rollovers for 2025-11.
--   Travel (rollover on): spent this month 1500; carried pot counts only active
--     prior months (Sep, Oct — the only prior months with any transaction):
--     (5000-1000) + (5000-2000) = 7000. Phantom empty months don't count.
--   Food (rollover off): carried = 0.
do $$
declare r record;
begin
    select * into r from public.category_rollovers('2025-11') where name = 'Travel';
    assert r.bucket = 'non_monthly', 'Travel bucket wrong';
    assert r.rollover = true, 'Travel rollover should be on';
    assert r.spent = 1500, 'Travel current spend wrong: ' || r.spent;
    assert r.carried = 7000, 'Travel carried pot wrong (want 7000): ' || r.carried;

    select * into r from public.category_rollovers('2025-11') where name = 'Food';
    assert r.rollover = false, 'Food rollover should be off';
    assert r.carried = 0, 'Food carried should be 0 when rollover off: ' || r.carried;
    assert r.spent = 4200, 'Food spent wrong: ' || r.spent;
end $$;

-- monthly_recap for 2025-11 (prev month 2025-10 had only Travel 2000 expense,
-- no income).
--   income 80000, expense 4200+1500+900+700 = 7300, saved 72700.
--   savings_rate = round(72700/80000, 4) = 0.9088.
--   prev month income 0 -> prev_savings_rate null.
--   vs Oct: Food +4200, Shopping +900, Transport +700, Travel 1500-2000 = -500.
--     biggest jump = Food +4200; best saver = Travel -500.
--   rolled_over = unspent this month in rollover cats = Travel max(0,5000-1500)=3500.
--   flexible_left = 12000 - 5100 = 6900 (Transport excluded, no budget).
do $$
declare r record;
begin
    select * into r from public.monthly_recap('2025-11');
    assert r.income = 80000, 'recap income wrong: ' || r.income;
    assert r.expense = 7300, 'recap expense wrong: ' || r.expense;
    assert r.saved = 72700, 'recap saved wrong: ' || r.saved;
    assert r.savings_rate = 0.9088, 'recap savings_rate wrong: ' || r.savings_rate;
    assert r.prev_savings_rate is null, 'prev savings rate should be null (no prior income)';
    assert r.biggest_jump_cat = 'Food', 'biggest jump cat wrong: ' || r.biggest_jump_cat;
    assert r.biggest_jump_delta = 4200, 'biggest jump delta wrong: ' || r.biggest_jump_delta;
    assert r.best_saver_cat = 'Travel', 'best saver cat wrong: ' || r.best_saver_cat;
    assert r.best_saver_delta = -500, 'best saver delta wrong: ' || r.best_saver_delta;
    assert r.rolled_over = 3500, 'rolled_over wrong: ' || r.rolled_over;
    assert r.flexible_left = 6900, 'flexible_left wrong: ' || r.flexible_left;
end $$;

-- ---------------------------------------------------------------- user B ----
-- B has only its own starter data: B sees no budgets, and its own buckets only.
set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.budgets) = 0, 'B can see budgets that are not its own';
    assert (select count(*) from public.category_rollovers('2025-11')) = 0, 'B sees A rollovers';
    -- B's own flex_overview is all zero (no budgets, no spend).
    assert (select coalesce(sum(budgeted + spent), 0) from public.flex_overview('2025-11')) = 0,
        'B flex_overview not empty';
    assert (select count(*) from public.categories where name = 'Travel') = 0, 'B sees A Travel category';
end $$;

reset role;
select 'flex_budget_test: all assertions passed' as result;
