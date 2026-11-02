-- Money Tracker — review inbox test (Sprint 9, migration 0013).
--
-- Real migrations 0001-0013 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * pending_imports: merchant, suggested category (own categories only),
--     merged emails columns,
--   * category_rules: owner-only, can't point at someone else's category,
--     one rule per match text (case-insensitive), length limits,
--   * sync_status: owner-only,
--   * undo_approve: reverses an accept (transaction gone, import pending),
--     refuses pending imports, other users and old accepts,
--   * user B never sees A's rules, sync status or imports.

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

do $$ begin
    assert has_function_privilege('authenticated', 'public.undo_approve(uuid)', 'execute'),
        'authenticated cannot execute undo_approve';
    assert not has_function_privilege('anon', 'public.undo_approve(uuid)', 'execute'),
        'anon can execute undo_approve';
end $$;

-- ---------------------------------------------------------------- user A ----
set role app_user;
set request.jwt.claim.sub = :'A';

-- A rule on A's own category works; duplicates (any case) are refused.
insert into public.category_rules (match_text, category_id)
select 'Swiggy', id from public.categories where name = 'Food' and kind = 'expense';
do $$ begin
    begin
        insert into public.category_rules (match_text, category_id)
        select '  swiggy ', id from public.categories where name = 'Food' and kind = 'expense';
        raise exception 'duplicate rule allowed';
    exception when unique_violation then null;
    end;
    begin
        insert into public.category_rules (match_text, category_id)
        select 'x', id from public.categories where name = 'Food' and kind = 'expense';
        raise exception 'one-letter rule allowed';
    exception when check_violation then null;
    end;
end $$;

-- Pending import with merchant, suggestion and merged emails.
insert into public.pending_imports
    (source, direction, amount, occurred_on, note, source_message_id,
     merchant, suggested_category_id, merged_message_ids, merged_sources, channel)
select 'receipt', 'expense', 450, current_date, 'Swiggy order', 'msg-a1',
       'Swiggy', id, '{msg-a2,msg-a3}', '{Google Pay,HDFC Bank}', 'Google Pay'
  from public.categories where name = 'Food' and kind = 'expense';

select count(*) = 1 as ok from public.pending_imports where merchant = 'Swiggy' \gset
\if :ok \else \echo 'FAIL: import with merchant not saved' \quit 1 \endif

-- Sync status row.
insert into public.sync_status (last_synced_at, last_found, last_added) values (now(), 5, 1);

-- Accept, then undo.
select public.approve_import(
    (select id from public.pending_imports where source_message_id = 'msg-a1'),
    (select id from public.accounts limit 1),
    (select id from public.categories where name = 'Food' and kind = 'expense')) \gset
select count(*) = 1 as ok from public.transactions where source_message_id = 'msg-a1' \gset
\if :ok \else \echo 'FAIL: accept did not create the transaction' \quit 1 \endif

select public.undo_approve((select id from public.pending_imports where source_message_id = 'msg-a1'));
select (select count(*) from public.transactions where source_message_id = 'msg-a1') = 0
   and (select status from public.pending_imports where source_message_id = 'msg-a1') = 'pending' as ok \gset
\if :ok \else \echo 'FAIL: undo did not reverse the accept' \quit 1 \endif

-- Undo on a pending import is refused.
do $$ begin
    begin
        perform public.undo_approve((select id from public.pending_imports where source_message_id = 'msg-a1'));
        raise exception 'undo on pending allowed';
    exception when sqlstate '55000' then null;
    end;
end $$;

-- Old accepts can't be undone: accept, then age the transaction past 10 minutes.
select public.approve_import(
    (select id from public.pending_imports where source_message_id = 'msg-a1'),
    (select id from public.accounts limit 1),
    (select id from public.categories where name = 'Food' and kind = 'expense')) \gset
reset role;
update public.transactions set created_at = now() - interval '11 minutes' where source_message_id = 'msg-a1';
set role app_user;
set request.jwt.claim.sub = :'A';
do $$ begin
    begin
        perform public.undo_approve((select id from public.pending_imports where source_message_id = 'msg-a1'));
        raise exception 'old undo allowed';
    exception when sqlstate '55000' then null;
    end;
end $$;

-- ---------------------------------------------------------------- user B ----
set request.jwt.claim.sub = :'B';

select (select count(*) from public.category_rules) = 0
   and (select count(*) from public.sync_status) = 0
   and (select count(*) from public.pending_imports) = 0 as ok \gset
\if :ok \else \echo 'FAIL: B can see A''s rules, sync status or imports' \quit 1 \endif

-- B can't point a rule or a suggestion at A's category.
reset role;
select id as a_food from public.categories
 where user_id = :'A' and name = 'Food' and kind = 'expense' \gset
set role app_user;
set request.jwt.claim.sub = :'B';
select set_config('my.a_food', :'a_food', false);
do $$ begin
    begin
        insert into public.category_rules (match_text, category_id)
        values ('zomato', current_setting('my.a_food')::uuid);
        raise exception 'rule on another user''s category allowed';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.pending_imports (source, direction, amount, occurred_on, suggested_category_id)
        values ('receipt', 'expense', 10, current_date, current_setting('my.a_food')::uuid);
        raise exception 'suggestion of another user''s category allowed';
    exception when sqlstate 'P0002' then null;
    end;
end $$;

-- B can't undo A's import.
reset role;
select id as a_imp from public.pending_imports where source_message_id = 'msg-a1' \gset
set role app_user;
set request.jwt.claim.sub = :'B';
select set_config('my.a_imp', :'a_imp', false);
do $$ begin
    begin
        perform public.undo_approve(current_setting('my.a_imp')::uuid);
        raise exception 'B undid A''s import';
    exception when sqlstate 'P0002' then null;
    end;
end $$;

reset role;
\echo 'review inbox SQL tests: OK'
