-- Money Tracker — security hardening test (Sprint 6, migration 0012).
--
-- Real migrations 0001-0012. Checks that the five reporting functions have a
-- pinned search_path and still return the right numbers with it, that anon and
-- signed-in users can no longer call handle_new_user() directly, and that
-- signing up still seeds categories and a Cash account through the trigger.

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

do $$
declare f text;
begin
    foreach f in array array['account_balances', 'dashboard_summary', 'spending_by_category',
                             'budget_status', 'monthly_totals']
    loop
        assert exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                        where n.nspname = 'public' and p.proname = f
                          and p.proconfig @> array['search_path=""']),
            format('%s has no pinned search_path', f);
    end loop;
    assert not has_function_privilege('anon', 'public.handle_new_user()', 'execute'), 'anon can call handle_new_user';
    assert not has_function_privilege('authenticated', 'public.handle_new_user()', 'execute'),
        'authenticated can call handle_new_user';
end $$;

-- A schema a caller could put first on their search_path, with a decoy table
-- shadowing public.transactions.
create schema evil;
create table evil.transactions (like public.transactions);
grant usage on schema evil to app_user;
grant select on evil.transactions to app_user;

-- Sign-up still seeds the new user through the trigger.
insert into auth.users (id, email) values (:'A', 'a@x.com');

set role app_user;
set request.jwt.claim.sub = :'A';
-- A hostile search_path must not change what the functions read.
set search_path = evil, public;

do $$
declare acc uuid; food uuid; pay uuid; n numeric;
begin
    assert (select count(*) from public.categories) > 0, 'seed trigger did not create categories';
    select id into acc from public.accounts where name = 'Cash';
    assert acc is not null, 'seed trigger did not create a Cash account';
    select id into food from public.categories where kind = 'expense' limit 1;
    select id into pay from public.categories where kind = 'income' limit 1;

    insert into public.transactions (type, amount, occurred_on, account_id, category_id)
    values ('income', 1000, '2026-10-02', acc, pay), ('expense', 300, '2026-10-05', acc, food);
    insert into public.budgets (category_id, monthly_limit) values (food, 500);

    select balance into n from public.account_balances() where id = acc;
    assert n = (select opening_balance from public.accounts where id = acc) + 700,
        format('account_balances wrong: %s', n);
    assert (select sum(total) from public.spending_by_category('2026-10')) = 300, 'spending_by_category wrong';
    assert (select spent from public.budget_status('2026-10') limit 1) = 300, 'budget_status wrong';
    assert (select count(*) from public.monthly_totals()) = 12, 'monthly_totals should cover 12 months';
    perform * from public.dashboard_summary('2026-10');
end $$;

\echo '  security hardening (0012): pinned search_path, trigger-only seed, functions still correct - all passed'
