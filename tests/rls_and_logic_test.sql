-- Money Tracker — schema, security and money-logic test.
--
-- This runs the real migrations against a local PostgreSQL, mocking the small
-- part of Supabase we depend on (the `auth` schema and `auth.uid()`), then
-- checks that:
--   * new users get seeded categories and a Cash account,
--   * Row Level Security isolates one user's data from another's,
--   * account balances, the dashboard summary and budgets compute correctly,
--   * the transaction shape constraints reject bad rows,
--   * email imports are private per user, and approve_import / reject_import
--     create transactions atomically and refuse bad or repeated requests.
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

drop role if exists app_user;
create role app_user nologin;
grant usage on schema public, auth to app_user;
grant select on auth.users to app_user;
-- Supabase's API roles, which migrations grant/revoke on.
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
end $$;

-- ---- The real migrations ---------------------------------------------------
\i migrations/0001_schema.sql
\i migrations/0002_rls.sql
\i migrations/0003_functions.sql
\i migrations/0004_seed_trigger.sql
\i migrations/0005_imports.sql
\i migrations/0006_approve_import.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all routines in schema public to app_user;

-- ---- Two users; the trigger seeds their categories + Cash account ----------
insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

do $$
declare n int;
begin
    select count(*) into n from public.categories
        where user_id = '11111111-1111-1111-1111-111111111111';
    assert n = 12, format('expected 12 seeded categories, got %s', n);
    select count(*) into n from public.accounts
        where user_id = '11111111-1111-1111-1111-111111111111';
    assert n = 1, format('expected 1 seeded account, got %s', n);
end $$;

-- ---- Act as user A, through RLS -------------------------------------------
set role app_user;
set request.jwt.claim.sub = :'A';

insert into public.accounts (name, type, opening_balance) values ('HDFC', 'bank', 5000);
insert into public.transactions (amount, type, account_id, category_id)
    select 60000, 'income', (select id from accounts where name='HDFC'),
           (select id from categories where name='Salary' and kind='income');
insert into public.transactions (amount, type, account_id, category_id)
    select 1500, 'expense', (select id from accounts where name='HDFC'),
           (select id from categories where name='Food' and kind='expense');
insert into public.transactions (amount, type, account_id, to_account_id)
    select 2000, 'transfer', (select id from accounts where name='HDFC'),
           (select id from accounts where name='Cash');

do $$
declare hdfc numeric; cash numeric; total numeric; inc numeric; exp numeric;
begin
    select balance into hdfc from public.account_balances() where name='HDFC';
    select balance into cash from public.account_balances() where name='Cash';
    assert hdfc = 61500, format('HDFC balance wrong: %s', hdfc);  -- 5000+60000-1500-2000
    assert cash = 2000,  format('Cash balance wrong: %s', cash);  -- 0+2000

    select total_balance, month_income, month_expense into total, inc, exp
        from public.dashboard_summary(to_char(current_date,'YYYY-MM'));
    assert total = 63500, format('total balance wrong: %s', total);
    assert inc = 60000,   format('income wrong: %s', inc);
    assert exp = 1500,    format('expense wrong: %s', exp);
end $$;

insert into public.budgets (category_id, monthly_limit)
    select id, 3000 from categories where name='Food' and kind='expense';

do $$
declare v_lim numeric; v_spent numeric;
begin
    select monthly_limit, spent into v_lim, v_spent
        from public.budget_status(to_char(current_date,'YYYY-MM')) where category='Food';
    assert v_lim = 3000,   format('budget limit wrong: %s', v_lim);
    assert v_spent = 1500, format('budget spent wrong: %s', v_spent);
end $$;

-- ---- User B must see none of A's data -------------------------------------
set request.jwt.claim.sub = :'B';
do $$
declare txs int; accs int;
begin
    select count(*) into txs from public.transactions;
    assert txs = 0, format('RLS leak: B sees %s of A''s transactions', txs);
    select count(*) into accs from public.accounts;
    assert accs = 1, format('RLS: B should see only own Cash, saw %s accounts', accs);
    assert (select name from public.accounts) = 'Cash', 'B sees an account that is not theirs';
end $$;

-- ---- Constraint checks (as A) ---------------------------------------------
set request.jwt.claim.sub = :'A';
do $$ begin
    insert into public.transactions(amount, type, account_id, category_id)
        select -5, 'expense', (select id from accounts where name='Cash'),
               (select id from categories where name='Food' limit 1);
    raise exception 'negative amount should have been rejected';
exception when check_violation then null; end $$;

do $$ begin
    insert into public.transactions(amount, type, account_id, to_account_id, category_id)
        select 10, 'transfer', (select id from accounts where name='Cash'),
               (select id from accounts where name='HDFC'),
               (select id from categories where name='Food' limit 1);
    raise exception 'transfer with a category should have been rejected';
exception when check_violation then null; end $$;

-- ---- Email imports: RLS on pending_imports --------------------------------
set request.jwt.claim.sub = :'A';
insert into public.pending_imports (source, direction, amount, occurred_on, note, source_message_id)
values ('sbi_alert', 'expense',  250.55, current_date, 'Swiggy', 'msg-1'),
       ('sbi_alert', 'income',  1000.10, current_date, 'Refund', 'msg-2'),
       ('receipt',   'expense',   99.99, current_date, 'Dup',    'msg-3'),
       ('statement', 'expense',   10.00, current_date, 'Junk',   'msg-4');

-- Remember ids from the other user's side (as superuser) for cross-user tries.
reset role;
do $$ begin
    perform set_config('test.a_imp1', id::text, false) from public.pending_imports
        where user_id = '11111111-1111-1111-1111-111111111111' and source_message_id = 'msg-1';
    perform set_config('test.b_cash', id::text, false) from public.accounts
        where user_id = '22222222-2222-2222-2222-222222222222' and name = 'Cash';
end $$;
set role app_user;

set request.jwt.claim.sub = :'B';
do $$
declare n int;
begin
    select count(*) into n from public.pending_imports;
    assert n = 0, format('RLS leak: B sees %s of A''s imports', n);
    update public.pending_imports set status = 'approved';
    get diagnostics n = row_count;
    assert n = 0, format('RLS leak: B updated %s of A''s imports', n);
    delete from public.pending_imports;
    get diagnostics n = row_count;
    assert n = 0, format('RLS leak: B deleted %s of A''s imports', n);
end $$;

-- B cannot approve or reject A's import through the functions either.
do $$
declare v_imp uuid := current_setting('test.a_imp1')::uuid;
begin
    begin
        perform public.approve_import(v_imp,
            (select id from public.accounts where name = 'Cash'),
            (select id from public.categories where name = 'Food' and kind = 'expense'));
        raise exception 'B approved A''s import';
    exception when sqlstate 'P0002' then null; end;
    begin
        perform public.reject_import(v_imp);
        raise exception 'B rejected A''s import';
    exception when sqlstate 'P0002' then null; end;
end $$;

-- ---- approve_import: happy path (as A) ------------------------------------
set request.jwt.claim.sub = :'A';
do $$
declare
    v_imp uuid; v_tx uuid; v_before numeric; v_after numeric;
    t public.transactions%rowtype;
begin
    select id into v_imp from public.pending_imports where source_message_id = 'msg-1';
    assert (select status from public.pending_imports where id = v_imp) = 'pending',
        'B''s attempts changed A''s import';
    select balance into v_before from public.account_balances() where name = 'HDFC';

    v_tx := public.approve_import(v_imp,
        (select id from public.accounts where name = 'HDFC'),
        (select id from public.categories where name = 'Food' and kind = 'expense'));
    assert v_tx is not null, 'approve_import returned null';

    select * into t from public.transactions where id = v_tx;
    assert t.type = 'expense', format('wrong type %s', t.type);
    assert t.amount = 250.55, format('wrong amount %s', t.amount);
    assert t.occurred_on = current_date, 'wrong date';
    assert t.note = 'Swiggy', format('wrong note %s', t.note);
    assert t.source = 'sbi_alert', format('wrong source %s', t.source);
    assert t.source_message_id = 'msg-1', 'wrong source_message_id';
    assert t.user_id = '11111111-1111-1111-1111-111111111111', 'wrong owner';

    select balance into v_after from public.account_balances() where name = 'HDFC';
    assert v_before - v_after = 250.55,
        format('balance should drop by exactly 250.55, went %s -> %s', v_before, v_after);
    assert (select status from public.pending_imports where id = v_imp) = 'approved',
        'import not marked approved';
end $$;

-- ---- approve_import: double approve is refused cleanly --------------------
do $$
declare v_imp uuid; n int;
begin
    select id into v_imp from public.pending_imports where source_message_id = 'msg-1';
    begin
        perform public.approve_import(v_imp,
            (select id from public.accounts where name = 'HDFC'),
            (select id from public.categories where name = 'Food' and kind = 'expense'));
        raise exception 'second approve should have been refused';
    exception when sqlstate '55000' then null; end;
    select count(*) into n from public.transactions where source_message_id = 'msg-1';
    assert n = 1, format('double approve created %s transactions', n);
end $$;

-- ---- approve_import: wrong-kind category is rejected, nothing changes -----
do $$
declare v_imp uuid; n_before int; n_after int;
begin
    select id into v_imp from public.pending_imports where source_message_id = 'msg-2';
    select count(*) into n_before from public.transactions;
    begin
        perform public.approve_import(v_imp,           -- income import, expense category
            (select id from public.accounts where name = 'HDFC'),
            (select id from public.categories where name = 'Food' and kind = 'expense'));
        raise exception 'wrong-kind category should have been rejected';
    exception when sqlstate '22023' then null; end;
    select count(*) into n_after from public.transactions;
    assert n_after = n_before, 'wrong-kind approve left a transaction behind';
    assert (select status from public.pending_imports where id = v_imp) = 'pending',
        'wrong-kind approve changed the import status';
end $$;

-- ---- approve_import: another user's account is rejected -------------------
do $$
declare v_imp uuid;
begin
    select id into v_imp from public.pending_imports where source_message_id = 'msg-2';
    begin
        perform public.approve_import(v_imp,
            current_setting('test.b_cash')::uuid,       -- B's Cash account
            (select id from public.categories where name = 'Salary' and kind = 'income'));
        raise exception 'approving into another user''s account should have failed';
    exception when sqlstate 'P0002' then null; end;
    assert (select status from public.pending_imports where id = v_imp) = 'pending',
        'failed approve changed the import status';
    -- An unknown category is refused too.
    begin
        perform public.approve_import(v_imp,
            (select id from public.accounts where name = 'HDFC'), gen_random_uuid());
        raise exception 'unknown category should have been rejected';
    exception when sqlstate 'P0002' then null; end;
    -- With the right inputs the same import still goes through.
    perform public.approve_import(v_imp,
        (select id from public.accounts where name = 'HDFC'),
        (select id from public.categories where name = 'Salary' and kind = 'income'));
    assert (select amount from public.transactions where source_message_id = 'msg-2') = 1000.10,
        'income import amount wrong';
end $$;

-- ---- approve_import: email already imported -> reuse existing transaction --
do $$
declare v_imp uuid; v_existing uuid; v_tx uuid; n int;
begin
    insert into public.transactions (amount, type, account_id, category_id, source, source_message_id)
        select 99.99, 'expense', (select id from accounts where name = 'Cash'),
               (select id from categories where name = 'Food' and kind = 'expense'),
               'receipt', 'msg-3'
        returning id into v_existing;
    select id into v_imp from public.pending_imports where source_message_id = 'msg-3';
    v_tx := public.approve_import(v_imp,
        (select id from public.accounts where name = 'HDFC'),
        (select id from public.categories where name = 'Food' and kind = 'expense'));
    assert v_tx = v_existing, 'duplicate email should return the existing transaction id';
    select count(*) into n from public.transactions where source_message_id = 'msg-3';
    assert n = 1, format('duplicate email created %s transactions', n);
    assert (select status from public.pending_imports where id = v_imp) = 'approved',
        'duplicate-email import not marked approved';
end $$;

-- ---- reject_import ---------------------------------------------------------
do $$
declare v_imp uuid; n_before int;
begin
    select id into v_imp from public.pending_imports where source_message_id = 'msg-4';
    select count(*) into n_before from public.transactions;
    perform public.reject_import(v_imp);
    assert (select status from public.pending_imports where id = v_imp) = 'rejected',
        'import not marked rejected';
    assert (select count(*) from public.transactions) = n_before, 'reject created a transaction';
    begin
        perform public.reject_import(v_imp);
        raise exception 'second reject should have been refused';
    exception when sqlstate '55000' then null; end;
    begin
        perform public.approve_import(v_imp,
            (select id from public.accounts where name = 'HDFC'),
            (select id from public.categories where name = 'Food' and kind = 'expense'));
        raise exception 'approving a rejected import should have been refused';
    exception when sqlstate '55000' then null; end;
    begin
        perform public.reject_import(gen_random_uuid());
        raise exception 'rejecting an unknown import should have failed';
    exception when sqlstate 'P0002' then null; end;
end $$;

-- ---- Not signed in: functions refuse ---------------------------------------
set request.jwt.claim.sub = '';
do $$ begin
    perform public.reject_import(gen_random_uuid());
    raise exception 'anonymous reject should have been refused';
exception when sqlstate '42501' then null; end $$;

reset role;
\echo ''
\echo '================================'
\echo '  ALL TESTS PASSED'
\echo '================================'
