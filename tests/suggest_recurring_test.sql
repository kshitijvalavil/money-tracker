-- Money Tracker — suggest_recurring() test (Sprint 2 detection).
--
-- Loads the shared synthetic fixture (tests/fixtures/recurring_transactions.csv)
-- as user A's transactions and checks that suggest_recurring() returns exactly
-- tests/fixtures/recurring_expected.csv, in the same order. That file is what
-- bot/recurring.py detect() produces (tests/test_recurring.py asserts it), so
-- this proves the SQL and the Python agree. Also checks:
--   * the date math against the Python occurrence() grid
--     (tests/fixtures/recurring_occurrences.csv), both the inline math in 0008
--     and public.recurring_occurrence from 0007,
--   * exclusion of groups already covered by a recurring row,
--   * another user sees no suggestions (RLS / user filter),
--   * errors 22023 / 42501, and grants (anon cannot execute).
--
-- Run from the supabase/ directory, like tests/rls_and_logic_test.sql:
--   psql ... -c "\cd <repo>/supabase" -f <repo>/tests/suggest_recurring_test.sql
-- If migrations/0007_recurring.sql is missing, a minimal stub of
-- public.recurring is created instead (pass -v has0007=0 to force the stub).

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'
\if :{?has0007}
\else
    \set has0007 `test -f migrations/0007_recurring.sql && echo 1 || echo 0`
\endif

-- ---- Mock the parts of Supabase we rely on --------------------------------
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'app_user') then
        create role app_user nologin;
    end if;
end $$;
grant usage on schema public, auth to app_user;
grant select on auth.users to app_user;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
end $$;

-- ---- Migrations --------------------------------------------------------------
\i migrations/0001_schema.sql
\i migrations/0002_rls.sql
\i migrations/0003_functions.sql
\i migrations/0004_seed_trigger.sql
\i migrations/0005_imports.sql
\i migrations/0006_approve_import.sql
\if :has0007
    \i migrations/0007_recurring.sql
\else
    \echo '  (0007 missing: using a minimal public.recurring stub)'
    create table public.recurring (
        id          uuid primary key default gen_random_uuid(),
        user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
        name        text not null check (length(btrim(name)) between 1 and 80),
        type        text not null check (type in ('income', 'expense')),
        amount      numeric(14, 2) not null check (amount > 0),
        account_id  uuid not null references public.accounts (id) on delete cascade,
        category_id uuid not null references public.categories (id) on delete restrict,
        frequency   text not null check (frequency in ('weekly', 'monthly', 'quarterly', 'yearly')),
        start_date  date not null,
        next_seq    int not null default 0,
        next_due    date not null default current_date,
        auto_post   boolean not null default false,
        active      boolean not null default true,
        source      text not null default 'manual',
        created_at  timestamptz not null default now()
    );
    alter table public.recurring enable row level security;
    create policy "own recurring" on public.recurring
        using (user_id = auth.uid()) with check (user_id = auth.uid());
\endif
\i migrations/0008_suggest_recurring.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant execute on all routines in schema public to app_user;

-- ---- Fixtures (loaded as superuser) -------------------------------------------
insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

create schema fx;
create table fx.tx (occurred_on date, amount numeric(14, 2), type text, note text,
                    account text, category text);
create table fx.expected (ord serial, name text, type text, amount text, frequency text,
                          account text, category text, last_seen text, next_guess text,
                          occurrences text, confidence text);
create table fx.occ (start_date date, frequency text, n int, occurs_on date);
\copy fx.tx from '../tests/fixtures/recurring_transactions.csv' with (format csv, header true)
\copy fx.expected (name, type, amount, frequency, account, category, last_seen, next_guess, occurrences, confidence) from '../tests/fixtures/recurring_expected.csv' with (format csv, header true)
\copy fx.occ from '../tests/fixtures/recurring_occurrences.csv' with (format csv, header true)
grant usage on schema fx to app_user;
grant select on all tables in schema fx to app_user;

insert into public.accounts (user_id, name, type) values
    (:'A', 'Bank', 'bank'), (:'A', 'Card', 'card'), (:'B', 'Bank', 'bank');

insert into public.transactions
       (user_id, occurred_on, amount, type, account_id, to_account_id, category_id, note)
select :'A', f.occurred_on, f.amount, f.type, a.id,
       case when f.type = 'transfer' then cash.id end,
       c.id, f.note
  from fx.tx f
  join public.accounts a on a.user_id = :'A' and a.name = f.account
  join public.accounts cash on cash.user_id = :'A' and cash.name = 'Cash'
  left join public.categories c
         on c.user_id = :'A' and c.name = f.category and c.kind = f.type;

-- B gets the same Netflix history in B's own account: must never leak into A's
-- results, and B sees only B's own suggestion.
insert into public.transactions (user_id, occurred_on, amount, type, account_id, category_id, note)
select :'B', f.occurred_on, f.amount, f.type, a.id, c.id, f.note
  from fx.tx f
  join public.accounts a on a.user_id = :'B' and a.name = 'Bank'
  join public.categories c on c.user_id = :'B' and c.name = f.category and c.kind = f.type
 where f.note like 'NETFLIX%';

do $$
declare n int;
begin
    select count(*) into n from fx.tx;
    assert n > 50, format('fixture not loaded (%s rows)', n);
    select count(*) into n from public.transactions
     where user_id = '11111111-1111-1111-1111-111111111111';
    assert n = (select count(*) from fx.tx), 'every fixture row became a transaction';
    select count(*) into n from fx.expected;
    assert n >= 5, format('expected file has %s rows', n);
end $$;

-- ---- Date math agrees with Python occurrence() ------------------------------------
do $$
declare bad int;
begin
    -- the inline math used by suggest_recurring
    select count(*) into bad from fx.occ o
     where o.occurs_on <> case when o.frequency = 'weekly' then o.start_date + 7 * o.n
              else (o.start_date + make_interval(months => o.n *
                    case o.frequency when 'monthly' then 1 when 'quarterly' then 3 else 12 end))::date
              end;
    assert bad = 0, format('%s inline date-math rows differ from Python', bad);
end $$;
\if :has0007
do $$
declare bad int;
begin
    select count(*) into bad from fx.occ o
     where public.recurring_occurrence(o.start_date, o.frequency, o.n) <> o.occurs_on;
    assert bad = 0, format('%s recurring_occurrence rows differ from Python occurrence()', bad);
end $$;
\endif

-- ---- suggest_recurring() as user A == Python detect() ---------------------------
set role app_user;
set request.jwt.claim.sub = :'A';

create temp table got as
select s.ord, s.name, s.type, s.amount::text as amount, s.frequency,
       a.name as account, c.name as category, s.last_seen::text as last_seen,
       s.next_guess::text as next_guess, s.occurrences::text as occurrences,
       s.confidence::text as confidence
  from public.suggest_recurring() with ordinality
       as s(name, type, amount, frequency, account_id, category_id, last_seen,
            next_guess, occurrences, confidence, ord)
  join public.accounts a on a.id = s.account_id
  join public.categories c on c.id = s.category_id;

do $$
declare diff text;
begin
    select string_agg(format('%s %s', side, r), E'\n') into diff from (
        select 'missing in SQL:' as side, e::text as r
          from (select ord, name, type, amount, frequency, account, category, last_seen,
                       next_guess, occurrences, confidence from fx.expected
                except
                select ord, name, type, amount, frequency, account, category, last_seen,
                       next_guess, occurrences, confidence from got) e
        union all
        select 'extra in SQL:', g::text
          from (select ord, name, type, amount, frequency, account, category, last_seen,
                       next_guess, occurrences, confidence from got
                except
                select ord, name, type, amount, frequency, account, category, last_seen,
                       next_guess, occurrences, confidence from fx.expected) g
    ) d;
    assert diff is null, E'suggest_recurring() differs from bot/recurring.py:\n' || diff;
end $$;
\echo '  suggest_recurring() == bot/recurring.py detect() on the fixture'

-- min_occurrences is honoured and validated
do $$
declare n3 int; n2 int; n9 int;
begin
    select count(*) into n3 from public.suggest_recurring(3);
    select count(*) into n2 from public.suggest_recurring(2);
    select count(*) into n9 from public.suggest_recurring(7);
    assert n2 >= n3, 'lower threshold never returns fewer rows';
    assert n9 = 1, format('only the 8x weekly gym has >= 7 occurrences (got %s)', n9);
    begin
        perform public.suggest_recurring(1);
        assert false, 'p_min_occurrences = 1 accepted';
    exception when sqlstate '22023' then null;
    end;
end $$;

-- ---- Exclusion: a bill with the same normalised name hides the group --------------
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
select 'Netflix.com', 'expense', 649, a.id, c.id, 'monthly', '2026-07-05', '2026-07-05'
  from public.accounts a, public.categories c
 where a.user_id = auth.uid() and a.name = 'Card'
   and c.user_id = auth.uid() and c.name = 'Entertainment' and c.kind = 'expense';

do $$
declare n int; total int;
begin
    select count(*) into n from public.suggest_recurring() where name like 'NETFLIX%';
    assert n = 0, 'Netflix still suggested after adding it as a bill';
    select count(*) into total from public.suggest_recurring();
    assert total = (select count(*) from fx.expected) - 1, 'only the Netflix group is excluded';
end $$;

-- ---- Per-user isolation --------------------------------------------------------------
set request.jwt.claim.sub = :'B';
do $$
declare n int; foreign_acct int;
begin
    select count(*) into n from public.suggest_recurring();
    -- also proves A's Netflix bill does not hide B's group (exclusion is per user)
    assert n = 1, format('B sees only B''s own Netflix suggestion (got %s)', n);
    select count(*) into foreign_acct from public.suggest_recurring() s
     where s.account_id not in (select id from public.accounts
                                 where user_id = '22222222-2222-2222-2222-222222222222');
    assert foreign_acct = 0, 'B''s suggestions only reference B''s accounts';
end $$;

-- ---- Not signed in ---------------------------------------------------------------------
reset request.jwt.claim.sub;
set request.jwt.claim.sub = '';
do $$
begin
    perform public.suggest_recurring();
    assert false, 'anonymous call accepted';
exception when sqlstate '42501' then null;
end $$;
reset role;

-- ---- Grants -----------------------------------------------------------------------------
do $$
begin
    assert not has_function_privilege('anon', 'public.suggest_recurring(int)', 'execute'),
        'anon must not execute suggest_recurring';
    assert has_function_privilege('authenticated', 'public.suggest_recurring(int)', 'execute'),
        'authenticated must execute suggest_recurring';
    assert (select provolatile from pg_proc where oid = 'public.suggest_recurring(int)'::regprocedure) = 's',
        'suggest_recurring is stable (read-only)';
    assert not (select prosecdef from pg_proc where oid = 'public.suggest_recurring(int)'::regprocedure),
        'suggest_recurring is security invoker';
end $$;

\echo 'ALL SUGGEST_RECURRING TESTS PASSED'
