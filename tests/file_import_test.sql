-- Money Tracker — bulk file import test (Sprint 15, migration 0020).
--
-- Real migrations 0001-0020 against a throwaway PostgreSQL with the usual
-- Supabase mock. Checks:
--   * import_batches: owner-only, name/kind/count checks,
--   * pending_imports: batch id must be the owner's own batch, category hint length,
--   * staging de-dupe: the same row key twice is refused by the unique index
--     (the browser uses insert ... on conflict do nothing),
--   * accept still works for file rows (approve_import unchanged),
--   * undo_import_batch: removes only that batch's still-pending rows, keeps
--     accepted ones, marks the batch undone, refuses other users' batches,
--   * user B never sees or changes A's batches or rows,
--   * anon cannot run undo_import_batch.

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
\i migrations/0016_home_layout.sql
\i migrations/0017_alert_prefs.sql
\i migrations/0020_file_import.sql
-- Running the migration twice must be harmless.
\i migrations/0020_file_import.sql

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

do $$ begin
    assert has_function_privilege('authenticated', 'public.undo_import_batch(uuid)', 'execute'),
        'authenticated cannot execute undo_import_batch';
    assert not has_function_privilege('anon', 'public.undo_import_batch(uuid)', 'execute'),
        'anon can execute undo_import_batch';
    assert (select relrowsecurity from pg_class where oid = 'public.import_batches'::regclass),
        'RLS is off on import_batches';
end $$;

-- ---------------------------------------------------------------- user A ----
set role app_user;
set request.jwt.claim.sub = :'A';

insert into public.import_batches (file_name, file_kind, row_count) values ('october.xlsx', 'xlsx', 3);
select id as batch from public.import_batches where file_name = 'october.xlsx' \gset

-- Checks on the batch row.
do $$ begin
    begin
        insert into public.import_batches (file_name, file_kind) values ('   ', 'xlsx');
        raise exception 'blank file name allowed';
    exception when check_violation then null;
    end;
    begin
        insert into public.import_batches (file_name, file_kind) values ('x.exe', 'exe');
        raise exception 'unknown file kind allowed';
    exception when check_violation then null;
    end;
    begin
        insert into public.import_batches (file_name, file_kind, row_count) values ('big.csv', 'csv', 20000);
        raise exception 'row count over the limit allowed';
    exception when check_violation then null;
    end;
    begin
        insert into public.import_batches (file_name, file_kind) values (repeat('a', 121), 'csv');
        raise exception 'file name over 120 chars allowed';
    exception when check_violation then null;
    end;
end $$;

-- Three staged rows, two identical except for the occurrence counter.
insert into public.pending_imports
    (source, direction, amount, occurred_on, note, merchant, source_message_id, import_batch_id, category_hint, account_hint)
values
    ('file', 'expense', 2300.00, '2026-10-30', '2 Pints - Dinner', '2 Pints - Dinner', 'file:aaa#1', :'batch', 'Food / Eating out', 'KOTAK Credit Card'),
    ('file', 'expense', 20.00,   '2026-10-20', 'Tea', 'Tea', 'file:bbb#1', :'batch', 'Food / Beverages', 'HDFC Card'),
    ('file', 'expense', 20.00,   '2026-10-20', 'Tea', 'Tea', 'file:bbb#2', :'batch', 'Food / Beverages', 'HDFC Card');

select count(*) = 3 as ok from public.pending_imports where import_batch_id = :'batch' \gset
\if :ok \else \echo 'FAIL: staged rows not saved' \quit 1 \endif

-- Re-importing the same file: the browser sends ON CONFLICT DO NOTHING, so no new rows.
insert into public.pending_imports
    (source, direction, amount, occurred_on, note, source_message_id, import_batch_id)
values ('file', 'expense', 2300.00, '2026-10-30', '2 Pints - Dinner', 'file:aaa#1', :'batch')
on conflict (user_id, source_message_id) do nothing;
select count(*) = 3 as ok from public.pending_imports where import_batch_id = :'batch' \gset
\if :ok \else \echo 'FAIL: re-import created a duplicate row' \quit 1 \endif

-- Category hint is length-checked.
do $$ begin
    begin
        insert into public.pending_imports (source, direction, amount, occurred_on, category_hint)
        values ('file', 'expense', 1, current_date, repeat('c', 81));
        raise exception 'long category hint allowed';
    exception when check_violation then null;
    end;
end $$;

-- Accept one row: approve_import works unchanged for file rows.
select public.approve_import(
    (select id from public.pending_imports where source_message_id = 'file:aaa#1'),
    (select id from public.accounts limit 1),
    (select id from public.categories where name = 'Food' and kind = 'expense')) as tx \gset
select (select source from public.transactions where id = :'tx') = 'file' as ok \gset
\if :ok \else \echo 'FAIL: accepted file row is not a file transaction' \quit 1 \endif

-- ---------------------------------------------------------------- user B ----
set request.jwt.claim.sub = :'B';

select (select count(*) from public.import_batches) = 0
   and (select count(*) from public.pending_imports) = 0 as ok \gset
\if :ok \else \echo 'FAIL: B can see A''s batches or rows' \quit 1 \endif

-- B cannot undo A's batch, rename it, or attach rows to it.
set my.batch = :'batch';
do $$ begin
    begin
        perform public.undo_import_batch(current_setting('my.batch')::uuid);
        raise exception 'B undid A''s batch';
    exception when sqlstate 'P0002' then null;
    end;
    begin
        insert into public.pending_imports (source, direction, amount, occurred_on, import_batch_id)
        values ('file', 'expense', 1, current_date, current_setting('my.batch')::uuid);
        raise exception 'B attached a row to A''s batch';
    exception when sqlstate 'P0002' then null;
    end;
end $$;
update public.import_batches set file_name = 'pwned' where id = :'batch';
delete from public.import_batches where id = :'batch';

-- B's own batch can't take A's rows either: B's update sees none of them.
update public.pending_imports set import_batch_id = null where import_batch_id = :'batch';

-- ---------------------------------------------------------------- user A ----
set request.jwt.claim.sub = :'A';
select (select file_name from public.import_batches where id = :'batch') = 'october.xlsx'
   and (select count(*) from public.pending_imports where import_batch_id = :'batch') = 3 as ok \gset
\if :ok \else \echo 'FAIL: B changed A''s batch or rows' \quit 1 \endif

-- Undo the whole import: the 2 pending rows go, the accepted one stays.
select public.undo_import_batch(:'batch') as removed \gset
select :removed = 2
   and (select count(*) from public.pending_imports where import_batch_id = :'batch') = 1
   and (select status from public.pending_imports where source_message_id = 'file:aaa#1') = 'approved'
   and (select count(*) from public.transactions where source_message_id = 'file:aaa#1') = 1
   and (select undone_at is not null from public.import_batches where id = :'batch') as ok \gset
\if :ok \else \echo 'FAIL: undo_import_batch removed the wrong rows' \quit 1 \endif

-- Undoing again is harmless (0 rows).
select public.undo_import_batch(:'batch') = 0 as ok \gset
\if :ok \else \echo 'FAIL: second undo was not a no-op' \quit 1 \endif

-- Deleting the batch keeps the accepted row (batch id set null).
delete from public.import_batches where id = :'batch';
select (select count(*) from public.pending_imports where source_message_id = 'file:aaa#1' and import_batch_id is null) = 1 as ok \gset
\if :ok \else \echo 'FAIL: deleting a batch lost an accepted row' \quit 1 \endif

-- Signed out: refused.
reset request.jwt.claim.sub;
do $$ begin
    begin
        perform public.undo_import_batch(gen_random_uuid());
        raise exception 'signed-out undo allowed';
    exception when sqlstate '42501' then null;
    end;
end $$;

\echo 'file import (0020): all checks passed'
