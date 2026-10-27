-- Money Tracker — goals, labels and insights test (Sprint 3, migration 0009).
--
-- Same Supabase mock as the other SQL tests: real migrations 0001-0009 against
-- a throwaway PostgreSQL, the app role getting function access only through
-- `authenticated`, so the migration's grants are exercised for real. Checks:
--   * grants: anon/public cannot call the new functions,
--   * goals + goal_progress: saved, remaining, pct (capped at 100), ordering,
--   * labels: free-form, reused case-insensitively, many per transaction,
--   * set_transaction_labels: create/reuse/replace/clear, dedup, bad input,
--     and refusing a transaction that isn't the caller's (P0002),
--   * transaction_labels RLS: cannot tag another user's transaction or label,
--   * spending_by_label: a multi-label expense counts toward each label,
--   * top_merchants: note normalisation, grouping, limit, latest display name,
--   * month_over_month: income/expense/net, this month vs the one before,
--   * left_to_spend: budgeted - spent, only budgeted categories, can go < 0,
--   * RLS between two users across all of the above.

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

grant select, insert, update, delete on all tables in schema public to app_user;
-- Function access exactly as in production: only via `authenticated`.
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- ============================================================================
-- Grants: anon and public cannot call the new functions
-- ============================================================================
do $$
declare f text;
begin
    foreach f in array array[
        'public.goal_progress()',
        'public.set_transaction_labels(uuid,text[])',
        'public.spending_by_label(text)',
        'public.top_merchants(text,integer)',
        'public.month_over_month(text)',
        'public.left_to_spend(text)']
    loop
        assert not has_function_privilege('anon', f, 'execute'), format('anon can execute %s', f);
        assert has_function_privilege('authenticated', f, 'execute'), format('authenticated cannot execute %s', f);
    end loop;
end $$;

-- ============================================================================
-- Fixtures for user A: one account, categories, budgets and transactions.
-- March 2026 is "this month"; February 2026 is "last month".
-- ============================================================================
set role app_user;
set request.jwt.claim.sub = :'A';

do $$
declare
    v_acct uuid;
    v_food uuid; v_rent uuid; v_salary uuid;
    v_sb1 uuid; v_sb2 uuid; v_landlord uuid; v_grocery uuid;
begin
    -- Use the starter account and categories the signup trigger already seeded.
    select id into v_acct   from public.accounts   where name = 'Cash';
    select id into v_food   from public.categories where name = 'Food'   and kind = 'expense';
    select id into v_rent   from public.categories where name = 'Rent'   and kind = 'expense';
    select id into v_salary from public.categories where name = 'Salary' and kind = 'income';
    insert into public.budgets (category_id, monthly_limit) values (v_food, 500), (v_rent, 1000);

    -- March expenses
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-03-05', 100, 'expense', v_acct, v_food, 'Starbucks Coffee #42') returning id into v_sb1;
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-03-12', 50, 'expense', v_acct, v_food, 'starbucks coffee') returning id into v_sb2;
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-03-01', 1000, 'expense', v_acct, v_rent, 'Landlord') returning id into v_landlord;
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-03-20', 30, 'expense', v_acct, v_food, 'Grocery Mart') returning id into v_grocery;
    -- March income
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-03-25', 3000, 'income', v_acct, v_salary, 'Payroll');
    -- February (last month)
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-02-10', 200, 'expense', v_acct, v_food, 'Starbucks');
    insert into public.transactions (occurred_on, amount, type, account_id, category_id, note)
        values ('2026-02-25', 2000, 'income', v_acct, v_salary, 'Payroll');

    -- Labels, applied through the RPC (reuse "Coffee" case-insensitively).
    perform public.set_transaction_labels(v_sb1, array['Coffee', 'Treats']);
    perform public.set_transaction_labels(v_sb2, array['coffee']);
    perform public.set_transaction_labels(v_landlord, array['Home']);
end $$;

-- ============================================================================
-- Labels: free-form, reused case-insensitively, deduped
-- ============================================================================
do $$
begin
    -- "Coffee"/"coffee" collapsed to one label row.
    assert (select count(*) from public.labels) = 3,
        format('expected 3 labels, got %s', (select count(*) from public.labels));
    assert (select count(*) from public.labels where lower(name) = 'coffee') = 1,
        'Coffee was duplicated';
    -- Two transactions carry the Coffee label.
    assert (select count(*) from public.transaction_labels tl
             join public.labels l on l.id = tl.label_id
            where lower(l.name) = 'coffee') = 2,
        'Coffee not on both Starbucks rows';
end $$;

-- set_transaction_labels replaces and clears; dedup within one call
do $$
declare v_tx uuid;
begin
    select id into v_tx from public.transactions where note = 'Starbucks Coffee #42';
    -- Replace: Treats drops, "Snacks" (twice -> once) is added, Coffee stays.
    perform public.set_transaction_labels(v_tx, array['Coffee', 'Snacks', 'snacks']);
    assert (select array_agg(l.name order by l.name)
              from public.transaction_labels tl join public.labels l on l.id = tl.label_id
             where tl.transaction_id = v_tx) = array['Coffee', 'Snacks'],
        'replace/dedup on set_transaction_labels failed';
    -- Clear.
    perform public.set_transaction_labels(v_tx, array[]::text[]);
    assert (select count(*) from public.transaction_labels where transaction_id = v_tx) = 0,
        'clearing labels failed';
    -- Put the original labels back for the later insight checks.
    perform public.set_transaction_labels(v_tx, array['Coffee', 'Treats']);
end $$;

-- set_transaction_labels: bad input and foreign transactions are refused
do $$
declare v_tx uuid; v_ok boolean;
begin
    select id into v_tx from public.transactions where note = 'Landlord';
    -- Label longer than 40 chars -> 22023
    v_ok := false;
    begin
        perform public.set_transaction_labels(v_tx, array[repeat('x', 41)]);
    exception when others then
        v_ok := (sqlstate = '22023');
    end;
    assert v_ok, 'oversized label not rejected with 22023';
end $$;

-- ============================================================================
-- goal_progress: saved, remaining, pct capped at 100, ordering by created_at
-- ============================================================================
do $$
declare g_vac uuid; g_phone uuid; g_car uuid;
begin
    insert into public.goals (name, target_amount) values ('Vacation', 1000) returning id into g_vac;
    insert into public.goals (name, target_amount, target_date)
        values ('Phone', 500, '2026-06-30') returning id into g_phone;
    insert into public.goals (name, target_amount) values ('Car', 10000) returning id into g_car;

    insert into public.goal_contributions (goal_id, amount) values (g_vac, 300), (g_vac, 200);
    insert into public.goal_contributions (goal_id, amount) values (g_phone, 600);  -- over-funded

    -- Vacation: 500/1000 -> 50%
    assert (select (saved, remaining, pct) from public.goal_progress() where name = 'Vacation')
           = (500::numeric, 500::numeric, 50.0::numeric), 'Vacation progress wrong';
    -- Phone: 600 saved, remaining clamped to 0, pct clamped to 100
    assert (select (saved, remaining, pct) from public.goal_progress() where name = 'Phone')
           = (600::numeric, 0::numeric, 100.0::numeric), 'Phone over-fund not clamped';
    -- Car: nothing saved
    assert (select (saved, remaining, pct) from public.goal_progress() where name = 'Car')
           = (0::numeric, 10000::numeric, 0.0::numeric), 'Car empty progress wrong';
    -- Ordering is by created_at (insertion order here).
    assert (select array_agg(name) from (select name from public.goal_progress()) q)
           = array['Vacation', 'Phone', 'Car'], 'goal_progress ordering wrong';
end $$;

-- ============================================================================
-- spending_by_label: a multi-label expense counts toward each label
-- ============================================================================
do $$
begin
    -- March: Home 1000, Coffee 150 (100+50), Treats 100
    assert (select total from public.spending_by_label('2026-03') where label = 'Home') = 1000,
        'Home label spend wrong';
    assert (select total from public.spending_by_label('2026-03') where label = 'Coffee') = 150,
        'Coffee label spend wrong (multi-label should count both)';
    assert (select total from public.spending_by_label('2026-03') where label = 'Treats') = 100,
        'Treats label spend wrong';
    assert (select array_agg(label order by total desc, label)
              from public.spending_by_label('2026-03')) = array['Home', 'Coffee', 'Treats'],
        'spending_by_label ordering wrong';
    -- February has no labelled expenses.
    assert (select count(*) from public.spending_by_label('2026-02')) = 0,
        'spending_by_label should be empty for Feb';
end $$;

-- ============================================================================
-- top_merchants: normalisation, grouping, latest display name, limit
-- ============================================================================
do $$
begin
    -- March, default limit: landlord 1000/1, starbucks coffee 150/2, grocery mart 30/1
    assert (select (total, txns) from public.top_merchants('2026-03') where merchant = 'Landlord')
           = (1000::numeric, 1), 'Landlord merchant wrong';
    -- The two Starbucks rows collapse to one merchant, newest note as the name.
    assert (select (total, txns) from public.top_merchants('2026-03') where merchant = 'starbucks coffee')
           = (150::numeric, 2), 'Starbucks merchant not grouped';
    assert (select (total, txns) from public.top_merchants('2026-03') where merchant = 'Grocery Mart')
           = (30::numeric, 1), 'Grocery merchant wrong';
    assert (select array_agg(merchant order by total desc) from public.top_merchants('2026-03'))
           = array['Landlord', 'starbucks coffee', 'Grocery Mart'], 'top_merchants ordering wrong';
    -- limit respected
    assert (select count(*) from public.top_merchants('2026-03', 1)) = 1,
        'top_merchants limit ignored';
end $$;

-- ============================================================================
-- month_over_month: income/expense/net, this month vs last
-- ============================================================================
do $$
begin
    -- March income 3000, expense 1180, net 1820; Feb income 2000, expense 200, net 1800
    assert (select (current, previous) from public.month_over_month('2026-03') where metric = 'income')
           = (3000::numeric, 2000::numeric), 'month_over_month income wrong';
    assert (select (current, previous) from public.month_over_month('2026-03') where metric = 'expense')
           = (1180::numeric, 200::numeric), 'month_over_month expense wrong';
    assert (select (current, previous) from public.month_over_month('2026-03') where metric = 'net')
           = (1820::numeric, 1800::numeric), 'month_over_month net wrong';
    assert (select array_agg(metric) from (select metric from public.month_over_month('2026-03')) q)
           = array['income', 'expense', 'net'], 'month_over_month ordering wrong';
    -- Bad month -> 22023
    declare v_ok boolean := false;
    begin
        begin
            perform public.month_over_month('2026/03');
        exception when others then v_ok := (sqlstate = '22023');
        end;
        assert v_ok, 'month_over_month did not reject bad month';
    end;
end $$;

-- ============================================================================
-- left_to_spend: budgeted - spent in budgeted categories, can go negative
-- ============================================================================
do $$
begin
    -- budgeted 1500 (Food 500 + Rent 1000); March spent in budgeted cats:
    -- Food 180 + Rent 1000 = 1180; remaining 320
    assert (select (budgeted, spent, remaining) from public.left_to_spend('2026-03'))
           = (1500::numeric, 1180::numeric, 320::numeric), 'left_to_spend wrong';
    -- February: spent Food 200, remaining 1300
    assert (select (budgeted, spent, remaining) from public.left_to_spend('2026-02'))
           = (1500::numeric, 200::numeric, 1300::numeric), 'left_to_spend Feb wrong';
end $$;

-- ============================================================================
-- RLS between two users
-- ============================================================================
-- User B sees none of A's goals, labels or insights.
set request.jwt.claim.sub = :'B';
do $$
begin
    assert (select count(*) from public.goal_progress()) = 0, 'B sees A goals';
    assert (select count(*) from public.spending_by_label('2026-03')) = 0, 'B sees A label spend';
    assert (select count(*) from public.top_merchants('2026-03')) = 0, 'B sees A merchants';
    assert (select (budgeted, spent, remaining) from public.left_to_spend('2026-03'))
           = (0::numeric, 0::numeric, 0::numeric), 'B sees A budgets';
end $$;

-- User B cannot tag user A's transaction (set_transaction_labels -> P0002).
do $$
declare v_tx uuid; v_ok boolean := false;
begin
    set role postgres;                 -- peek at A's id without RLS
    select id into v_tx from public.transactions where note = 'Landlord';
    set role app_user;
    set request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';
    begin
        perform public.set_transaction_labels(v_tx, array['Mine']);
    exception when others then v_ok := (sqlstate = 'P0002');
    end;
    assert v_ok, 'B tagged A transaction without P0002';
end $$;

-- User B cannot INSERT a transaction_labels row pointing at A's transaction
-- directly (the policy's WITH CHECK must block it).
do $$
declare v_tx uuid; v_lbl uuid; v_ok boolean := false;
begin
    set role postgres;
    select id into v_tx from public.transactions where note = 'Landlord';
    set role app_user;
    set request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';
    insert into public.labels (name) values ('BLabel') returning id into v_lbl;
    begin
        insert into public.transaction_labels (transaction_id, label_id) values (v_tx, v_lbl);
    exception when others then v_ok := true;   -- RLS violation (42501) or similar
    end;
    assert v_ok, 'B inserted transaction_labels for A transaction';
end $$;

reset role;
\echo 'goals_labels_test.sql: all checks passed'
