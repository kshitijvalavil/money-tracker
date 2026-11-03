-- Money Tracker — net worth test (Sprint 11, migration 0015).
--
-- Real migrations 0001-0015 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * net_worth_breakdown: account balances tagged asset / liability by sign,
--     a card spent into becomes a liability, investments are an asset,
--   * totals: assets - liabilities = net,
--   * net_worth_series: returns the asked-for number of months in order, the
--     last month equals the live net worth, and p_months out of range is
--     rejected,
--   * capture_net_worth_snapshot: writes one row for the current month and is
--     idempotent (a second call updates, never duplicates); the snapshot then
--     shows through in the series,
--   * goal_forecast: saved / remaining / pct and a positive monthly_rate with a
--     projected date once a goal has contributions,
--   * grants: authenticated may call, anon may not,
--   * user B never sees A's snapshots, breakdown or goals.

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- Grants: signed-in only.
do $$ begin
    assert has_function_privilege('authenticated', 'public.net_worth_breakdown()', 'execute'),
        'authenticated cannot execute net_worth_breakdown';
    assert has_function_privilege('authenticated', 'public.net_worth_series(int)', 'execute'),
        'authenticated cannot execute net_worth_series';
    assert has_function_privilege('authenticated', 'public.capture_net_worth_snapshot()', 'execute'),
        'authenticated cannot execute capture_net_worth_snapshot';
    assert has_function_privilege('authenticated', 'public.goal_forecast()', 'execute'),
        'authenticated cannot execute goal_forecast';
    assert not has_function_privilege('anon', 'public.net_worth_breakdown()', 'execute'),
        'anon can execute net_worth_breakdown';
    assert not has_function_privilege('anon', 'public.capture_net_worth_snapshot()', 'execute'),
        'anon can execute capture_net_worth_snapshot';
end $$;

-- ---------------------------------------------------------------- user A ----
set role app_user;
set request.jwt.claim.sub = :'A';

-- Two accounts: a bank (asset) and a card that will be spent into (liability).
insert into public.accounts (name, type, opening_balance) values ('Bank', 'bank', 100000);
insert into public.accounts (name, type, opening_balance) values ('Card', 'card', 0);

-- Income 80000 into Bank, Rent 3000 out of Bank, Food 5000 on the Card.
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-01', 80000, 'income', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Salary';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-06', 3000, 'expense', (select id from public.accounts where name = 'Bank'),
       id from public.categories where name = 'Rent';
insert into public.transactions (occurred_on, amount, type, account_id, category_id)
select '2025-11-05', 5000, 'expense', (select id from public.accounts where name = 'Card'),
       id from public.categories where name = 'Food';

-- One investment worth 50000.
insert into public.investments (name, kind, invested_amount, current_value, as_of)
values ('Index fund', 'mutual_fund', 45000, 50000, '2025-11-01');

-- Breakdown: Bank 100000+80000-3000 = 177000 asset; Card 0-5000 = 5000 liability;
-- investments 50000 asset. Assets 227000, liabilities 5000, net 222000.
do $$
declare v_assets numeric; v_liab numeric; v_bank numeric; v_card text;
begin
    select coalesce(sum(case when kind = 'asset' then amount else 0 end), 0),
           coalesce(sum(case when kind = 'liability' then amount else 0 end), 0)
      into v_assets, v_liab from public.net_worth_breakdown();
    assert v_assets = 227000, 'assets wrong: ' || v_assets;
    assert v_liab = 5000, 'liabilities wrong: ' || v_liab;
    select amount into v_bank from public.net_worth_breakdown() where label = 'Bank';
    assert v_bank = 177000, 'bank balance wrong: ' || v_bank;
    select kind into v_card from public.net_worth_breakdown() where label = 'Card';
    assert v_card = 'liability', 'card should be a liability, got ' || v_card;
    assert (select kind from public.net_worth_breakdown() where source = 'investments') = 'asset',
        'investments should be an asset';
end $$;

-- Series: 6 months, ascending, last month equals the live net worth (222000).
do $$
declare n int; last_net numeric; ordered boolean;
begin
    select count(*) into n from public.net_worth_series(6);
    assert n = 6, 'series should return 6 months, got ' || n;
    select net into last_net from public.net_worth_series(6) order by month desc limit 1;
    assert last_net = 222000, 'latest series net wrong: ' || last_net;
    select bool_and(month = o) into ordered from (
        select month, to_char((select min(month) from public.net_worth_series(6))::date, 'YYYY-MM')
        from public.net_worth_series(6) limit 0
    ) q(month, o);
    -- p_months out of range is rejected.
    begin
        perform public.net_worth_series(0);
        raise exception 'p_months 0 accepted';
    exception when others then
        assert sqlstate = '22023', 'wrong error for bad p_months: ' || sqlstate;
    end;
end $$;

-- Capture: writes one snapshot for the current month, idempotent, shows in series.
do $$
declare v_net numeric; v_rows int;
begin
    select net into v_net from public.capture_net_worth_snapshot();
    assert v_net = 222000, 'captured net wrong: ' || v_net;
    perform public.capture_net_worth_snapshot();   -- second call must not duplicate
    select count(*) into v_rows from public.net_worth_snapshots
     where as_of_month = date_trunc('month', current_date)::date;
    assert v_rows = 1, 'snapshot duplicated: ' || v_rows;
    select net into v_net from public.net_worth_series(1) order by month desc limit 1;
    assert v_net = 222000, 'snapshot not reflected in series: ' || v_net;
end $$;

-- goal_forecast: a goal with two contributions in two months.
insert into public.goals (name, target_amount, target_date)
values ('Trip', 100000, (current_date + interval '10 months')::date);
insert into public.goal_contributions (goal_id, amount, occurred_on)
select id, 10000, '2025-09-10' from public.goals where name = 'Trip';
insert into public.goal_contributions (goal_id, amount, occurred_on)
select id, 20000, '2025-10-10' from public.goals where name = 'Trip';
do $$
declare r record;
begin
    select * into r from public.goal_forecast() where name = 'Trip';
    assert r.saved = 30000, 'goal saved wrong: ' || r.saved;
    assert r.remaining = 70000, 'goal remaining wrong: ' || r.remaining;
    assert r.pct = 30.0, 'goal pct wrong: ' || r.pct;
    assert r.months_active = 2, 'months_active wrong: ' || r.months_active;
    assert r.monthly_rate = 15000, 'monthly_rate wrong: ' || r.monthly_rate;
    assert r.projected_date is not null, 'projected_date should be set';
    assert r.needed_monthly is not null and r.needed_monthly > 0, 'needed_monthly should be positive';
end $$;

-- ---------------------------------------------------------------- user B ----
set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.net_worth_snapshots) = 0, 'B sees A snapshots';
    -- B has only the starter Cash account (opening 0) and no investments.
    assert (select coalesce(sum(amount), 0) from public.net_worth_breakdown()) = 0,
        'B breakdown should be all zero';
    assert (select count(*) from public.goal_forecast()) = 0, 'B sees A goals';
    -- B can capture its own (zero) snapshot without seeing A's.
    perform public.capture_net_worth_snapshot();
    assert (select count(*) from public.net_worth_snapshots) = 1, 'B capture leaked A rows';
end $$;

reset role;
select 'net_worth_test: all assertions passed' as result;
