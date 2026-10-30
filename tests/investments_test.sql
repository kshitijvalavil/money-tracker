-- Money Tracker — investments test (Sprint 6, migration 0010).
--
-- Same Supabase mock as the other SQL tests: real migrations 0001-0010 against
-- a throwaway PostgreSQL. Checks:
--   * anon has no table access,
--   * checks: blank name, unknown kind, negative amounts, long note,
--   * RLS: each user sees, updates and deletes only their own holdings, and
--     cannot insert a row owned by someone else,
--   * deleting a user removes their holdings.

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
grant usage on schema public, auth to app_user, authenticated, anon;
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

-- Table access exactly as the migration grants it: only via `authenticated`.
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- ============================================================================
-- anon: no access at all
-- ============================================================================
do $$ begin
    assert not has_table_privilege('anon', 'public.investments', 'select'), 'anon can select investments';
    assert not has_table_privilege('anon', 'public.investments', 'insert'), 'anon can insert investments';
    assert has_table_privilege('authenticated', 'public.investments', 'select'), 'authenticated cannot select';
end $$;

-- ============================================================================
-- User A: valid rows, then every check constraint
-- ============================================================================
set role app_user;
set request.jwt.claim.sub = :'A';

insert into public.investments (name, kind, invested_amount, current_value)
values ('Nifty 50 index fund', 'mutual_fund', 50000, 56250.50),
       ('SBI FD', 'fixed_deposit', 100000, 100000),
       ('Sovereign gold bond', 'gold', 20000, 18500);

do $$
declare n int; uid uuid;
begin
    select count(*), min(user_id::text)::uuid into n, uid from public.investments;
    assert n = 3, format('A should see 3 holdings, saw %s', n);
    assert uid = '11111111-1111-1111-1111-111111111111', 'user_id should default to auth.uid()';
    assert (select as_of from public.investments where name = 'SBI FD') = current_date, 'as_of defaults to today';
    assert (select kind from public.investments where name = 'SBI FD') = 'fixed_deposit', 'kind kept';
end $$;

do $$
declare bad text;
begin
    foreach bad in array array[
        $q$insert into public.investments (name, invested_amount, current_value) values ('   ', 1, 1)$q$,
        $q$insert into public.investments (name, kind, invested_amount, current_value) values ('X', 'nft', 1, 1)$q$,
        $q$insert into public.investments (name, invested_amount, current_value) values ('X', -1, 1)$q$,
        $q$insert into public.investments (name, invested_amount, current_value) values ('X', 1, -1)$q$,
        $q$insert into public.investments (name, invested_amount, current_value, note) values ('X', 1, 1, repeat('a', 201))$q$,
        $q$insert into public.investments (name, invested_amount, current_value) values (repeat('a', 81), 1, 1)$q$]
    loop
        begin
            execute bad;
            raise exception 'should have been rejected: %', bad;
        exception when check_violation then null;
        end;
    end loop;
end $$;

-- Updating the current value works for your own row.
update public.investments set current_value = 19000, as_of = '2026-10-01' where name = 'Sovereign gold bond';
do $$ begin
    assert (select current_value from public.investments where name = 'Sovereign gold bond') = 19000, 'update failed';
end $$;

-- ============================================================================
-- User B: cannot see, change, delete, or plant rows for A
-- ============================================================================
set request.jwt.claim.sub = :'B';

do $$
declare n int;
begin
    select count(*) into n from public.investments;
    assert n = 0, format('B should see 0 holdings, saw %s', n);

    update public.investments set current_value = 0;
    get diagnostics n = row_count;
    assert n = 0, format('B updated %s of A''s rows', n);

    delete from public.investments;
    get diagnostics n = row_count;
    assert n = 0, format('B deleted %s of A''s rows', n);

    begin
        insert into public.investments (user_id, name, invested_amount, current_value)
        values ('11111111-1111-1111-1111-111111111111', 'planted', 1, 1);
        raise exception 'B inserted a row owned by A';
    exception when insufficient_privilege then null;
    end;
end $$;

insert into public.investments (name, kind, invested_amount, current_value)
values ('B stocks', 'stocks', 10000, 12000);

-- B cannot move their own row over to A either.
do $$ begin
    begin
        update public.investments set user_id = '11111111-1111-1111-1111-111111111111';
        raise exception 'B reassigned a row to A';
    exception when insufficient_privilege then null;
    end;
end $$;

-- No user: sees nothing.
set request.jwt.claim.sub = '';
do $$ begin
    assert (select count(*) from public.investments) = 0, 'signed-out caller saw holdings';
end $$;

-- ============================================================================
-- A still has exactly their 3 rows, untouched by B; deleting A cascades
-- ============================================================================
set request.jwt.claim.sub = :'A';
do $$ begin
    assert (select count(*) from public.investments) = 3, 'A lost rows';
    assert (select sum(current_value) from public.investments) = 56250.50 + 100000 + 19000, 'A totals changed';
end $$;

reset role;
delete from auth.users where id = :'A';
do $$ begin
    assert (select count(*) from public.investments) = 1, 'deleting A should leave only B''s holding';
end $$;

\echo '  investments (0010): checks, RLS between users, cascade - all passed'
