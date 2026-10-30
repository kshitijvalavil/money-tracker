-- Money Tracker — import channel labels test (Sprint 6, migration 0011).
--
-- Real migrations 0001-0011 against a throwaway PostgreSQL, same Supabase
-- mock as the other SQL tests. Checks:
--   * channel column: optional, 1-40 chars when present,
--   * approve_import with a channel creates the label and tags the transaction,
--   * an existing label is reused case-insensitively (no duplicate label),
--   * no channel -> no label,
--   * re-approving the same email (existing transaction) adds the label and
--     keeps the labels the user already put on it,
--   * the old behaviour still holds: errors, already-approved 55000,
--   * user B never sees A's labels or tags.

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

grant select, insert, update, delete on all tables in schema public to app_user;
grant authenticated to app_user;

insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

do $$ begin
    assert has_function_privilege('authenticated', 'public.approve_import(uuid,uuid,uuid)', 'execute'),
        'authenticated lost execute on approve_import';
    assert not has_function_privilege('anon', 'public.approve_import(uuid,uuid,uuid)', 'execute'),
        'anon can execute approve_import';
end $$;

set role app_user;
set request.jwt.claim.sub = :'A';

-- Channel column constraint.
do $$ begin
    begin
        insert into public.pending_imports (source, direction, amount, occurred_on, source_message_id, channel)
        values ('x', 'expense', 1, '2026-10-01', 'bad-1', '   ');
        raise exception 'blank channel accepted';
    exception when check_violation then null;
    end;
    begin
        insert into public.pending_imports (source, direction, amount, occurred_on, source_message_id, channel)
        values ('x', 'expense', 1, '2026-10-01', 'bad-2', repeat('a', 41));
        raise exception 'long channel accepted';
    exception when check_violation then null;
    end;
end $$;

insert into public.labels (name) values ('google pay');   -- A already has this label, lower case
insert into public.pending_imports (source, direction, amount, occurred_on, note, source_message_id, channel) values
    ('hdfc_alert', 'expense', 250, '2026-10-29', 'UPI to ravi@okicici', 'm-upi', 'Google Pay'),
    ('sbi_alert',  'income', 2000, '2026-10-28', 'Deposit by transfer', 'm-sbi', 'SBI'),
    ('receipt',    'expense', 412, '2026-10-27', 'Zomato order',        'm-rcpt', null),
    ('sbi_alert',  'expense',  75, '2026-10-26', 'UPI/DR/CAFE',         'm-dup', 'Google Pay');

do $$
declare
    acc uuid := (select id from public.accounts limit 1);
    exp uuid := (select id from public.categories where kind = 'expense' limit 1);
    inc uuid := (select id from public.categories where kind = 'income' limit 1);
    tx uuid; tx2 uuid; n int;
begin
    -- UPI import -> reuses the existing 'google pay' label, no duplicate.
    tx := public.approve_import((select id from public.pending_imports where source_message_id = 'm-upi'), acc, exp);
    assert (select count(*) from public.labels where lower(name) = 'google pay') = 1, 'duplicate Google Pay label';
    assert exists (select 1 from public.transaction_labels tl join public.labels l on l.id = tl.label_id
                    where tl.transaction_id = tx and lower(l.name) = 'google pay'), 'UPI tx not labelled Google Pay';

    -- Bank import -> new 'SBI' label created and attached.
    tx := public.approve_import((select id from public.pending_imports where source_message_id = 'm-sbi'), acc, inc);
    assert (select count(*) from public.labels where name = 'SBI') = 1, 'SBI label not created';
    assert (select count(*) from public.transaction_labels where transaction_id = tx) = 1, 'SBI tx should have 1 label';

    -- Receipt -> no channel, no label.
    tx := public.approve_import((select id from public.pending_imports where source_message_id = 'm-rcpt'), acc, exp);
    assert (select count(*) from public.transaction_labels where transaction_id = tx) = 0, 'receipt got a label';

    -- Same email already in the tracker with a label the user chose: approving
    -- reuses that transaction, keeps 'Coffee' and adds 'Google Pay'.
    insert into public.transactions (type, amount, occurred_on, account_id, category_id, note, source, source_message_id)
    values ('expense', 75, '2026-10-26', acc, exp, 'cafe', 'manual', 'm-dup') returning id into tx2;
    perform public.set_transaction_labels(tx2, array['Coffee']);
    tx := public.approve_import((select id from public.pending_imports where source_message_id = 'm-dup'), acc, exp);
    assert tx = tx2, 'should reuse the existing transaction';
    select count(*) into n from public.transaction_labels tl join public.labels l on l.id = tl.label_id
     where tl.transaction_id = tx and l.name in ('Coffee', 'google pay');
    assert n = 2, format('existing tx should keep Coffee and gain Google Pay (got %s)', n);

    -- Approving again is still refused.
    begin
        perform public.approve_import((select id from public.pending_imports where source_message_id = 'm-upi'), acc, exp);
        raise exception 'second approve allowed';
    exception when sqlstate '55000' then null;
    end;

    -- Kind mismatch still refused, and nothing is labelled by a failed approve.
    insert into public.pending_imports (source, direction, amount, occurred_on, source_message_id, channel)
    values ('axis_alert', 'income', 10, '2026-10-20', 'm-bad-kind', 'Axis Bank');
    begin
        perform public.approve_import((select id from public.pending_imports where source_message_id = 'm-bad-kind'), acc, exp);
        raise exception 'kind mismatch allowed';
    exception when sqlstate '22023' then null;
    end;
    assert not exists (select 1 from public.labels where name = 'Axis Bank'), 'failed approve left a label behind';
end $$;

-- User B sees none of A's labels or tags, and cannot approve A's imports.
set request.jwt.claim.sub = :'B';
do $$
declare acc uuid := (select id from public.accounts limit 1);
        exp uuid := (select id from public.categories where kind = 'expense' limit 1);
begin
    assert (select count(*) from public.labels) = 0, 'B sees A''s labels';
    assert (select count(*) from public.transaction_labels) = 0, 'B sees A''s tags';
    begin
        perform public.approve_import(
            (select id from public.pending_imports where source_message_id = 'm-bad-kind'), acc, exp);
        raise exception 'B approved A''s import';
    exception when sqlstate 'P0002' then null;
    end;
end $$;

\echo '  import channel (0011): labels on approve, reuse, keep existing, RLS - all passed'
