-- Money Tracker — audit log + consent test (Sprint 14, migration 0021).
--   * a person reads and adds to their OWN audit list only,
--   * nobody can change or delete a row (even their own),
--   * invalid events and oversized details are refused,
--   * rows older than 12 months disappear when that person adds a new row,
--     and only THEIR old rows,
--   * anon has no access to either table,
--   * consent: own row only, upsert works, other people's rows are invisible,
--   * deleting an account removes both (cascade).
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
grant usage on schema public, auth to app_user, authenticated, anon;
grant select on auth.users to app_user;
grant execute on function auth.uid() to public;

\i migrations/0001_schema.sql
\i migrations/0002_rls.sql
\i migrations/0004_seed_trigger.sql
\i migrations/0021_audit_and_consent.sql

grant authenticated to app_user;
insert into auth.users (id, email) values (:'A', 'a@example.com'), (:'B', 'b@example.com');

-- ---- table privileges ------------------------------------------------------
do $$ begin
    assert not has_table_privilege('anon', 'public.audit_log', 'select'), 'anon can read audit_log';
    assert not has_table_privilege('anon', 'public.audit_log', 'insert'), 'anon can write audit_log';
    assert has_table_privilege('authenticated', 'public.audit_log', 'select'), 'signed-in cannot read audit_log';
    assert has_table_privilege('authenticated', 'public.audit_log', 'insert'), 'signed-in cannot add to audit_log';
    assert not has_table_privilege('authenticated', 'public.audit_log', 'update'), 'signed-in can UPDATE audit_log';
    assert not has_table_privilege('authenticated', 'public.audit_log', 'delete'), 'signed-in can DELETE audit_log';
    assert not has_table_privilege('authenticated', 'public.audit_log', 'truncate'), 'signed-in can TRUNCATE audit_log';
    assert not has_table_privilege('anon', 'public.user_consents', 'select'), 'anon can read user_consents';
    assert not has_function_privilege('authenticated', 'public.audit_log_prune()', 'execute'), 'prune function is callable';
end $$;

-- ---- A adds events; B cannot see them --------------------------------------
set role app_user;
set request.jwt.claim.sub = :'A';
insert into public.audit_log (event, detail) values ('sign_in', '{"device":"test"}'), ('data_exported', '{}');
do $$ begin
    assert (select count(*) from public.audit_log) = 2, 'A should see their 2 events';
end $$;

set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.audit_log) = 0, 'B can see A''s audit events';
end $$;

-- B cannot plant an event for A
do $$ begin
    begin
        insert into public.audit_log (user_id, event) values ('11111111-1111-1111-1111-111111111111', 'sign_in');
        raise exception 'B planted an audit event for A';
    exception when insufficient_privilege then null; end;
end $$;

-- ---- append-only: A cannot edit or delete (privilege error, not just 0 rows)
set request.jwt.claim.sub = :'A';
do $$ begin
    begin update public.audit_log set event = 'idle_lock'; raise exception 'A edited an audit row';
    exception when insufficient_privilege then null; end;
    begin delete from public.audit_log; raise exception 'A deleted audit rows';
    exception when insufficient_privilege then null; end;
    begin
        insert into public.audit_log (event) values ('made_up_event');
        raise exception 'unknown event accepted';
    exception when check_violation then null; end;
    begin
        insert into public.audit_log (event, detail) values ('sign_in', jsonb_build_object('x', repeat('a', 5000)));
        raise exception 'oversized detail accepted';
    exception when check_violation then null; end;
    begin
        insert into public.audit_log (event, detail) values ('sign_in', '[1,2]'::jsonb);
        raise exception 'non-object detail accepted';
    exception when check_violation then null; end;
end $$;

-- ---- 12-month retention: old rows go when THAT person adds a new row -------
reset role;
insert into public.audit_log (user_id, event, created_at) values
    (:'A', 'sign_in', now() - interval '13 months'),
    (:'A', 'sign_in', now() - interval '11 months'),
    (:'B', 'sign_in', now() - interval '13 months');
set role app_user;
set request.jwt.claim.sub = :'A';
insert into public.audit_log (event) values ('sign_out_all');
reset role;
do $$ begin
    assert (select count(*) from public.audit_log where user_id = '11111111-1111-1111-1111-111111111111' and created_at < now() - interval '12 months') = 0,
        'A''s 13-month-old row was not pruned';
    assert (select count(*) from public.audit_log where user_id = '11111111-1111-1111-1111-111111111111' and created_at > now() - interval '12 months') >= 3,
        'A lost a recent row';
    assert (select count(*) from public.audit_log where user_id = '22222222-2222-2222-2222-222222222222') = 1,
        'A adding a row pruned B''s old row (it must only touch A''s)';
end $$;

-- ---- consent ---------------------------------------------------------------
set role app_user;
set request.jwt.claim.sub = :'A';
insert into public.user_consents (notice_version) values ('2026-11-07');
update public.user_consents set notice_version = '2027-01-01', accepted_at = now();
do $$ begin
    assert (select notice_version from public.user_consents) = '2027-01-01', 'A cannot update their consent';
    begin
        insert into public.user_consents (notice_version) values ('again');
        raise exception 'second consent row for same person accepted';
    exception when unique_violation then null; end;
    begin delete from public.user_consents; raise exception 'A deleted a consent row';
    exception when insufficient_privilege then null; end;
end $$;
set request.jwt.claim.sub = :'B';
do $$ begin
    assert (select count(*) from public.user_consents) = 0, 'B can see A''s consent';
    begin
        insert into public.user_consents (user_id, notice_version) values ('11111111-1111-1111-1111-111111111111', 'x');
        raise exception 'B wrote consent for A';
    exception when insufficient_privilege or unique_violation then null; end;
end $$;
update public.user_consents set notice_version = 'hacked';
do $$ begin
    assert (select count(*) from public.user_consents) = 0, 'B can touch A''s consent';
end $$;

-- ---- deleting the account removes both ------------------------------------
reset role;
delete from auth.users where id = :'A';
do $$ begin
    assert (select count(*) from public.audit_log where user_id = '11111111-1111-1111-1111-111111111111') = 0, 'audit rows survived account deletion';
    assert (select count(*) from public.user_consents where user_id = '11111111-1111-1111-1111-111111111111') = 0, 'consent survived account deletion';
end $$;

\echo ''
\echo '========================================'
\echo '  AUDIT LOG + CONSENT TEST: ALL PASSED'
\echo '========================================'
