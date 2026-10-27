-- Money Tracker — end-to-end email import test (QA, Sprint 1).
--
-- scanner output (tests/e2e_payload.py on tests/fixtures)
--   -> PostgREST-style bulk upsert into pending_imports as the signed-in user
--   -> approve_import / reject_import
--   -> account balances and dashboard totals.
--
-- The schema comes from setup_database.sql (the one-file setup users paste
-- into Supabase), so this also proves that file applies cleanly.
-- tests/run.sh passes the scanner payload as the psql variable "payload" and
-- runs it from the repo root. Afterwards run.sh drives a two-session
-- double-approve race against the 'race-1' import created here.

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'

-- ---- Mock the parts of Supabase we rely on (same as rls_and_logic_test) ----
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
-- app_user is cluster-wide and may already exist from rls_and_logic_test.
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'app_user') then create role app_user nologin; end if;
end $$;
grant usage on schema public, auth to app_user;
grant select on auth.users to app_user;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;

\i setup_database.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all routines in schema public to app_user;
insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- ---- Schema matches the scanner's PostgREST assumptions --------------------
do $$
declare n int;
begin
    -- on_conflict=user_id,source_message_id needs a NON-partial unique
    -- constraint/index on exactly those columns, or PostgREST returns 42P10.
    select count(*) into n
      from pg_index i
     where i.indrelid = 'public.pending_imports'::regclass
       and i.indisunique and i.indpred is null
       and (select array_agg(a.attname::text order by k.ord)
              from unnest(i.indkey) with ordinality k(attnum, ord)
              join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.attnum)
           = array['user_id', 'source_message_id'];
    assert n = 1, 'pending_imports lacks a unique (user_id, source_message_id) for on_conflict';

    -- The payload omits user_id, so the column must default to auth.uid().
    assert (select pg_get_expr(d.adbin, d.adrelid)
              from pg_attrdef d join pg_attribute a
                on a.attrelid = d.adrelid and a.attnum = d.adnum
             where d.adrelid = 'public.pending_imports'::regclass and a.attname = 'user_id')
           = 'auth.uid()', 'pending_imports.user_id must default to auth.uid()';

    -- Every key the scanner sends is a real column.
    select count(*) into n from unnest(array['source','direction','amount','occurred_on',
        'account_hint','note','source_message_id']) k
     where not exists (select 1 from information_schema.columns
                        where table_schema = 'public' and table_name = 'pending_imports'
                          and column_name = k);
    assert n = 0, 'scanner sends a key that is not a pending_imports column';
end $$;

-- RPC grants as Supabase will see them: signed-in users only, never anon.
do $$ begin
    assert has_function_privilege('authenticated', 'public.approve_import(uuid, uuid, uuid)', 'execute'),
        'authenticated cannot execute approve_import';
    assert has_function_privilege('authenticated', 'public.reject_import(uuid)', 'execute'),
        'authenticated cannot execute reject_import';
    assert not has_function_privilege('anon', 'public.approve_import(uuid, uuid, uuid)', 'execute'),
        'anon can execute approve_import';
    assert not has_function_privilege('anon', 'public.reject_import(uuid)', 'execute'),
        'anon can execute reject_import';
    assert (select prosecdef from pg_proc where oid = 'public.approve_import(uuid, uuid, uuid)'::regprocedure) = false,
        'approve_import must stay SECURITY INVOKER';
end $$;

-- ---- Push the scanner's payload as user A, the way PostgREST does it -------
-- PostgREST: INSERT INTO t (<payload keys>) SELECT ... FROM
--            json_populate_recordset(NULL::t, $body) ON CONFLICT (...) DO NOTHING
set role app_user;
set request.jwt.claim.sub = :'A';
-- Session-local helper so every push below runs the identical statement.
create function pg_temp.push(p json) returns int language sql as $f$
    with ins as (
        insert into public.pending_imports
            (source, direction, amount, occurred_on, account_hint, note, source_message_id)
        select source, direction, amount, occurred_on, account_hint, note, source_message_id
          from json_populate_recordset(null::public.pending_imports, p)
        on conflict (user_id, source_message_id) do nothing
        returning 1)
    select count(*)::int from ins
$f$;
select pg_temp.push(:'payload'::json) as pushed \gset

do $$
declare n int; r record;
begin
    select count(*) into n from public.pending_imports where status = 'pending';
    assert n = 3, format('expected the 3 fixture rows pending, got %s', n);
    assert (select bool_and(user_id = '11111111-1111-1111-1111-111111111111')
              from public.pending_imports), 'pushed rows not owned by the pushing user';
    select * into r from public.pending_imports where source_message_id = 'fixture-sbi-debit@example.test';
    assert r.amount = 1250.50 and r.direction = 'expense' and r.occurred_on = '2026-10-15'
       and r.account_hint = '000002', format('debit row wrong: %s', row_to_json(r));
    select * into r from public.pending_imports where source_message_id = 'fixture-sbi-credit@example.test';
    assert r.amount = 7000 and r.direction = 'income' and r.occurred_on = '2026-10-26',
        format('credit row wrong: %s', row_to_json(r));
    select * into r from public.pending_imports where source_message_id = 'fixture-dominos@example.test';
    assert r.amount = 450 and r.source = 'receipt' and r.occurred_on = '2026-10-02',
        format('receipt row wrong: %s', row_to_json(r));
end $$;

-- Pushing the same scan again is a no-op (ignore-duplicates), not an error.
select pg_temp.push(:'payload'::json) as pushed \gset
do $$ begin
    assert (select count(*) from public.pending_imports) = 3, 're-push duplicated imports';
end $$;

-- B pushes the same emails: B gets their own copies, A's rows are untouched.
set request.jwt.claim.sub = :'B';
select pg_temp.push(:'payload'::json) as pushed \gset
do $$ begin
    assert (select count(*) from public.pending_imports) = 3, 'B should see exactly own 3 rows';
end $$;
-- B cannot plant a row in A's space.
do $$ begin
    insert into public.pending_imports (user_id, source, direction, amount, occurred_on, source_message_id)
    values ('11111111-1111-1111-1111-111111111111', 'x', 'expense', 1, current_date, 'planted');
    raise exception 'B inserted a pending import owned by A';
exception when insufficient_privilege then null; end $$;

-- ---- A approves the scanner rows -------------------------------------------
set request.jwt.claim.sub = :'A';
insert into public.accounts (name, type, opening_balance) values ('SBI 000001', 'bank', 10000);

do $$
declare
    sbi uuid := (select id from public.accounts where name = 'SBI 000001');
    cash uuid := (select id from public.accounts where name = 'Cash');
    food uuid := (select id from public.categories where name = 'Food' and kind = 'expense');
    shop uuid := (select id from public.categories where name = 'Shopping' and kind = 'expense');
    sal uuid := (select id from public.categories where name = 'Salary' and kind = 'income');
    v numeric; tot numeric; inc numeric; exp numeric; n int;
begin
    perform public.approve_import((select id from public.pending_imports
        where source_message_id = 'fixture-sbi-credit@example.test'), sbi, sal);
    perform public.approve_import((select id from public.pending_imports
        where source_message_id = 'fixture-sbi-debit@example.test'), sbi, shop);
    perform public.approve_import((select id from public.pending_imports
        where source_message_id = 'fixture-dominos@example.test'), cash, food);

    select balance into v from public.account_balances() where name = 'SBI 000001';
    assert v = 15749.50, format('SBI balance: want 10000+7000-1250.50=15749.50, got %s', v);
    select balance into v from public.account_balances() where name = 'Cash';
    assert v = -450, format('Cash balance: want -450, got %s', v);

    select total_balance, month_income, month_expense into tot, inc, exp
      from public.dashboard_summary('2026-10');
    assert tot = 15299.50, format('dashboard total: want 15299.50, got %s', tot);
    assert inc = 7000, format('dashboard income: want 7000, got %s', inc);
    assert exp = 1700.50, format('dashboard expense: want 1700.50, got %s', exp);

    select total into v from public.spending_by_category('2026-10') where category = 'Shopping';
    assert v = 1250.50, format('Shopping spend: %s', v);
    select total into v from public.spending_by_category('2026-10') where category = 'Food';
    assert v = 450, format('Food spend: %s', v);

    select count(*) into n from public.transactions
     where source <> 'manual' and source_message_id like 'fixture-%';
    assert n = 3, format('expected 3 imported transactions, got %s', n);
    assert (select count(*) from public.pending_imports where status = 'pending') = 0,
        'imports still pending after approval';
end $$;

-- Re-scan after approval: nothing comes back to pending, nothing duplicated.
select pg_temp.push(:'payload'::json) as pushed \gset
do $$ begin
    assert (select count(*) from public.pending_imports where status = 'pending') = 0,
        're-push after approval re-queued an approved email';
    assert (select count(*) from public.transactions where source_message_id like 'fixture-%') = 3,
        're-push after approval duplicated a transaction';
end $$;

-- B's copies are still pending and B's balances did not move.
set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.pending_imports where status = 'pending') = 3,
        'A approving changed B''s imports';
    assert (select total_balance from public.dashboard_summary('2026-10')) = 0,
        'A approving changed B''s dashboard';
end $$;

-- ---- Adversarial: other user's category, null category, precision ----------
reset role;
select set_config('test.b_food', (select id::text from public.categories
    where user_id = :'B' and name = 'Food' and kind = 'expense'), false) as b_food \gset
set role app_user;
set request.jwt.claim.sub = :'A';
insert into public.pending_imports (source, direction, amount, occurred_on, note, source_message_id)
values ('sbi_alert', 'expense', 0.01,             '2026-10-20', 'one paisa',    'prec-min'),
       ('sbi_alert', 'income',  99999999999.99,   '2026-10-20', 'very large',   'prec-max'),
       ('sbi_alert', 'expense', 5.00,             '2026-10-20', 'cross-user',   'adv-cat'),
       ('sbi_alert', 'expense', 7.00,             '2026-10-20', 'race',         'race-1');

do $$
declare
    sbi uuid := (select id from public.accounts where name = 'SBI 000001');
    food uuid := (select id from public.categories where name = 'Food' and kind = 'expense');
    sal uuid := (select id from public.categories where name = 'Salary' and kind = 'income');
    imp uuid := (select id from public.pending_imports where source_message_id = 'adv-cat');
    before numeric; after numeric;
begin
    -- Another user's category.
    begin
        perform public.approve_import(imp, sbi, current_setting('test.b_food')::uuid);
        raise exception 'approved with another user''s category';
    exception when sqlstate 'P0002' then null; end;
    -- No category (the UI sends null when the user has no category of that kind).
    begin
        perform public.approve_import(imp, sbi, null);
        raise exception 'approved with a null category';
    exception when sqlstate 'P0002' then null; end;
    -- Null account.
    begin
        perform public.approve_import(imp, null, food);
        raise exception 'approved with a null account';
    exception when sqlstate 'P0002' then null; end;
    assert (select status from public.pending_imports where id = imp) = 'pending',
        'failed approvals changed status';

    -- Exact money at both ends of the range.
    select balance into before from public.account_balances() where name = 'SBI 000001';
    perform public.approve_import((select id from public.pending_imports
        where source_message_id = 'prec-min'), sbi, food);
    select balance into after from public.account_balances() where name = 'SBI 000001';
    assert before - after = 0.01, format('0.01 approve moved balance by %s', before - after);
    perform public.approve_import((select id from public.pending_imports
        where source_message_id = 'prec-max'), sbi, sal);
    select balance into after from public.account_balances() where name = 'SBI 000001';
    assert after = before - 0.01 + 99999999999.99,
        format('large approve: %s -> %s', before, after);
    assert (select month_income from public.dashboard_summary('2026-10')) = 100000006999.99,
        'dashboard income wrong after the large import';
end $$;

-- Amount above numeric(14,2) is refused by the table (what a bad scanner
-- row would hit; one bad row fails the whole PostgREST bulk insert).
do $$ begin
    insert into public.pending_imports (source, direction, amount, occurred_on, source_message_id)
    values ('sbi_alert', 'expense', 1234567890123.45, '2026-10-20', 'too-big');
    raise exception 'oversized amount accepted';
exception when numeric_value_out_of_range then null; end $$;

-- Reject then approve, approve then reject.
do $$
declare imp uuid := (select id from public.pending_imports where source_message_id = 'adv-cat');
begin
    perform public.reject_import(imp);
    begin
        perform public.approve_import(imp, (select id from public.accounts where name = 'Cash'),
            (select id from public.categories where name = 'Food' and kind = 'expense'));
        raise exception 'approved a rejected import';
    exception when sqlstate '55000' then null; end;
    begin
        perform public.reject_import((select id from public.pending_imports
            where source_message_id = 'prec-min'));
        raise exception 'rejected an approved import';
    exception when sqlstate '55000' then null; end;
end $$;

reset role;
\echo ''
\echo '================================'
\echo '  E2E IMPORT TEST PASSED'
\echo '================================'
