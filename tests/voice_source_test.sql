-- Money Tracker — voice source test (Sprint 16, migration 0021).
-- Checks that a recurring bill, a transaction and a pending import can all carry
-- source 'voice', the old values still work, and junk sources are still refused.
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
\i migrations/0022_voice.sql
-- Idempotent: running it twice is harmless.
\i migrations/0022_voice.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;
insert into auth.users (id, email) values (:'A', 'a@x.com');

set role app_user;
set request.jwt.claim.sub = :'A';

-- A voice bill, a voice transaction, a voice pending import.
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, source)
select 'Phone bill', 'expense', 999,
       (select id from public.accounts limit 1),
       (select id from public.categories where kind = 'expense' limit 1),
       'monthly', current_date, 'voice';
select count(*) = 1 as ok from public.recurring where source = 'voice' \gset
\if :ok \else \echo 'FAIL: voice recurring not saved' \quit 1 \endif

insert into public.transactions (type, amount, occurred_on, account_id, category_id, note, source)
select 'expense', 450, current_date, (select id from public.accounts limit 1),
       (select id from public.categories where kind = 'expense' limit 1), 'Starbucks', 'voice';
insert into public.pending_imports (source, direction, amount, occurred_on, note)
values ('voice', 'expense', 80, current_date, 'auto');
select (select count(*) from public.transactions where source = 'voice') = 1
   and (select count(*) from public.pending_imports where source = 'voice') = 1 as ok \gset
\if :ok \else \echo 'FAIL: voice transaction or import not saved' \quit 1 \endif

-- Old recurring sources still allowed; a junk one still refused.
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, source)
select 'Rent', 'expense', 18000, (select id from public.accounts limit 1),
       (select id from public.categories where kind = 'expense' limit 1), 'monthly', current_date, 'manual';
do $$ begin
    begin
        insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, source)
        select 'Bad', 'expense', 1, (select id from public.accounts limit 1),
               (select id from public.categories where kind = 'expense' limit 1), 'monthly', current_date, 'nonsense';
        raise exception 'junk recurring source allowed';
    exception when check_violation then null;
    end;
end $$;

\echo 'voice source (0021): all checks passed'
