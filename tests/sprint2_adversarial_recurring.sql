-- Sprint 2 release gate (Sherlock Bugs): recurring bills, adversarial.
--
-- Written against docs/sprint-2-contract.md (incl. Manager decisions 1-14)
-- before the build landed. Run by
-- tests/sprint2_adversarial.sh (never on its own: it needs the psql variables
-- date_cases, detection and random_spend, the shared fixtures in
-- tests/fixtures/sprint2/).
--
-- Every case records PASS/FAIL in qa.results instead of stopping, so one run
-- reports every bug. The runner compares the recorded count with the number
-- of qa.pass cases in this file, so a case that never ran is a failure too.
--
-- Unlike rls_and_logic_test.sql, the test role gets function rights only
-- through membership of `authenticated`, so a missing GRANT in 0007/0008 is
-- caught here instead of masked by a blanket grant.

\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'

-- ---- Supabase mocks ----------------------------------------------------------
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'app_user') then create role app_user nologin; end if;
end $$;
grant authenticated to app_user;
grant usage on schema public, auth to authenticated, anon;
grant select on auth.users to authenticated;

-- ---- Result recording --------------------------------------------------------
create schema qa;
create table qa.results (n serial, name text, ok boolean, detail text);
create function qa.pass(p_name text) returns void language sql security definer
    as $$ insert into qa.results (name, ok) values (p_name, true) $$;
create function qa.fail(p_name text, p_detail text) returns void language sql security definer
    as $$ insert into qa.results (name, ok, detail) values (p_name, false, p_detail) $$;
-- Expect an error: true when p_sql raises (any SQLSTATE in p_states, or any
-- error when p_states is null).
create function qa.raises(p_sql text, p_states text[] default null) returns text
language plpgsql as $$
begin
    execute p_sql;
    return 'no error';
exception when others then
    if p_states is null or sqlstate = any (p_states) then return null; end if;
    return 'wrong error ' || sqlstate || ': ' || sqlerrm;
end $$;
grant usage on schema qa to public;
grant execute on all functions in schema qa to public;

-- ---- Migrations (missing ones show as an error and failed cases below) --------
\i supabase/migrations/0001_schema.sql
\i supabase/migrations/0002_rls.sql
\i supabase/migrations/0003_functions.sql
\i supabase/migrations/0004_seed_trigger.sql
\i supabase/migrations/0005_imports.sql
\i supabase/migrations/0006_approve_import.sql
\i supabase/migrations/0007_recurring.sql
\i supabase/migrations/0008_suggest_recurring.sql

-- Supabase grants table rights to authenticated; functions follow the migrations.
grant select, insert, update, delete on all tables in schema public to authenticated;
insert into auth.users (id, email) values (:'A', 'a@x.com'), (:'B', 'b@x.com');

-- Ids we need from both sides, stashed as settings (read as the superuser).
set role app_user;
set request.jwt.claim.sub = :'A';
insert into public.accounts (name, type, opening_balance) values ('HDFC', 'bank', 100000);
set request.jwt.claim.sub = :'B';
insert into public.accounts (name, type, opening_balance) values ('HDFC', 'bank', 100000);
reset role;
create table qa.ids as
select u.who, a.id as hdfc, c.id as cash,
       (select id from public.categories where user_id = u.uid and name = 'Bills') as bills,
       (select id from public.categories where user_id = u.uid and name = 'Salary') as salary,
       (select id from public.categories where user_id = u.uid and name = 'Food') as food
  from (values ('A', :'A'::uuid), ('B', :'B'::uuid)) u(who, uid)
  left join public.accounts a on a.user_id = u.uid and a.name = 'HDFC'
  left join public.accounts c on c.user_id = u.uid and c.name = 'Cash';
grant select on qa.ids to public;
create function qa.id(p_who text, p_what text) returns uuid language sql stable as $$
    select case p_what when 'hdfc' then hdfc when 'cash' then cash when 'bills' then bills
                       when 'salary' then salary when 'food' then food end
      from qa.ids where who = p_who $$;
grant execute on function qa.id(text, text) to public;

-- Helper (as the caller): make a bill and return its id.
create function qa.bill(p_name text, p_freq text, p_start date, p_auto boolean default false,
                        p_amount numeric default 100, p_type text default 'expense')
returns uuid language plpgsql as $$
declare v uuid; who text := case when auth.uid() = '11111111-1111-1111-1111-111111111111' then 'A' else 'B' end;
begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency,
                                  start_date, next_due, auto_post)
    values (p_name, p_type, p_amount, qa.id(who, case when p_type = 'income' then 'cash' else 'hdfc' end),
            qa.id(who, case when p_type = 'income' then 'salary' else 'bills' end), p_freq,
            p_start, p_start, p_auto)
    returning id into v;
    return v;
end $$;
grant execute on function qa.bill(text, text, date, boolean, numeric, text) to public;

-- ==== 1. Contract surface ======================================================
do $$
declare f text; missing text := '';
begin
    foreach f in array array[
        'public.recurring_occurrence(date, text, integer)',
        'public.mark_recurring_paid(uuid, date)',
        'public.skip_recurring(uuid)',
        'public.post_due_recurring(date)',
        'public.upcoming_recurring(integer, date)',   -- SB-11: p_as_of added
        'public.suggest_recurring(integer)',
        'public.reschedule_recurring(uuid, date, text)']          -- decision 12
    loop
        if to_regprocedure(f) is null then missing := missing || f || ' '; continue; end if;
        if (select prosecdef from pg_proc where oid = to_regprocedure(f)) then
            missing := missing || f || ' is SECURITY DEFINER; ';
        end if;
        if not coalesce((select proconfig @> array['search_path=""'] from pg_proc where oid = to_regprocedure(f)), false) then
            missing := missing || f || ' lacks search_path = ''''; ';
        end if;
        if has_function_privilege('anon', f, 'execute') then missing := missing || f || ' executable by anon; '; end if;
        if not has_function_privilege('authenticated', f, 'execute') then missing := missing || f || ' not granted to authenticated; '; end if;
    end loop;
    if missing = '' then perform qa.pass('contract: 7 functions exist, invoker, search_path, grants');
    else perform qa.fail('contract: 7 functions exist, invoker, search_path, grants', missing); end if;
exception when others then perform qa.fail('contract: functions', sqlerrm);
end $$;

do $$ begin
    assert (select provolatile from pg_proc where oid = 'public.recurring_occurrence(date, text, integer)'::regprocedure) = 'i',
        'recurring_occurrence must be immutable';
    assert (select provolatile from pg_proc where oid = 'public.suggest_recurring(integer)'::regprocedure) in ('s', 'i'),
        'suggest_recurring must be read-only (stable)';
    assert (select relrowsecurity from pg_class where oid = 'public.recurring'::regclass), 'RLS not enabled on recurring';
    assert exists (select 1 from pg_indexes where indexname = 'transactions_recurring_seq_uniq'),
        'missing unique index transactions_recurring_seq_uniq';
    perform qa.pass('contract: immutable/stable, RLS on, unique (recurring_id, recurring_seq)');
exception when others then perform qa.fail('contract: volatility/RLS/index', sqlerrm);
end $$;

-- ==== 2. Date math =============================================================
create table qa.date_cases as
    select * from json_to_recordset(:'date_cases'::json)
        as x(start date, freq text, n int, expected date, why text);
grant select on qa.date_cases to public;
do $$
declare r record; got date; bad text := '';
begin
    for r in select * from qa.date_cases loop
        begin
            got := public.recurring_occurrence(r.start, r.freq, r.n);
        exception when others then got := null;
        end;
        if got is distinct from r.expected then
            bad := bad || format('[%s %s n=%s want %s got %s: %s] ', r.start, r.freq, r.n, r.expected, got, r.why);
        end if;
    end loop;
    if bad = '' then perform qa.pass('dates: all fixture cases (29/30/31, leap/non-leap Feb, yearly 29 Feb, weekly over year end)');
    else perform qa.fail('dates: fixture cases', bad); end if;
end $$;

do $$
declare n int; d date; bad text := '';
begin
    for n in 0..119 loop            -- 10 years from 31 Jan 2024: always month end, never drifts
        d := public.recurring_occurrence('2024-01-31', 'monthly', n);
        if extract(day from d) <> 31 and extract(day from d + 1) <> 1 then bad := bad || d || ' '; end if;
        if d <> (date_trunc('month', date '2024-01-31' + make_interval(months => n)) + interval '1 month - 1 day')::date
           and extract(day from d) <> 31 then bad := bad || d || ' '; end if;
    end loop;
    if bad = '' then perform qa.pass('dates: 120 monthly occurrences of the 31st are all month ends (no drift)');
    else perform qa.fail('dates: monthly 31st drift', bad); end if;
exception when others then perform qa.fail('dates: monthly 31st drift', sqlerrm);
end $$;

do $$
declare e text;
begin
    e := qa.raises($q$ select public.recurring_occurrence('2024-01-31', 'daily', 1) $q$, array['22023']);
    if e is null then e := qa.raises($q$ select public.recurring_occurrence('2024-01-31', 'MONTHLY', 1) $q$, array['22023']); end if;
    if e is null then perform qa.pass('dates: bad frequency raises 22023');
    else perform qa.fail('dates: bad frequency raises 22023', e); end if;
end $$;

-- Table-level checks on bad input.
set role app_user;
set request.jwt.claim.sub = :'A';
do $$
declare cases text[] := array[
    $q$ select qa.bill('', 'monthly', '2026-01-01') $q$,
    $q$ select qa.bill('   ', 'monthly', '2026-01-01') $q$,
    $q$ select qa.bill(repeat('x', 81), 'monthly', '2026-01-01') $q$,
    $q$ select qa.bill('Zero', 'monthly', '2026-01-01', false, 0) $q$,
    $q$ select qa.bill('Neg', 'monthly', '2026-01-01', false, -5) $q$,
    $q$ select qa.bill('Huge', 'monthly', '2026-01-01', false, 1000000000000) $q$,
    $q$ select qa.bill('Daily', 'daily', '2026-01-01') $q$,
    -- expense bill with an income category
    $q$ insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('Kind', 'expense', 10, qa.id('A','hdfc'), qa.id('A','salary'), 'monthly', '2026-01-01', '2026-01-01') $q$,
    $q$ insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('Kind2', 'income', 10, qa.id('A','hdfc'), qa.id('A','food'), 'monthly', '2026-01-01', '2026-01-01') $q$];
    c text; bad text := ''; e text;
begin
    foreach c in array cases loop
        e := qa.raises(c);
        if e is not null then bad := bad || '[' || left(btrim(c), 70) || ' -> ' || e || '] '; end if;
    end loop;
    if (select count(*) from public.recurring where name in ('Zero','Neg','Huge','Daily','Kind','Kind2')) > 0 then
        bad := bad || 'bad rows were stored';
    end if;
    if bad = '' then perform qa.pass('input: empty/blank/81-char name, 0/negative/oversized amount, bad frequency, kind mismatch all refused');
    else perform qa.fail('input validation', bad); end if;
end $$;

do $$
declare v uuid; e text;
begin
    v := qa.bill('Kind flip', 'monthly', '2026-01-01');
    e := qa.raises(format($q$ update public.recurring set category_id = qa.id('A','salary') where id = %L $q$, v));
    if e is null then perform qa.pass('input: update to a wrong-kind category refused');
    else perform qa.fail('input: update to a wrong-kind category refused', e); end if;
    delete from public.recurring where id = v;
exception when others then perform qa.fail('input: update to a wrong-kind category refused', sqlerrm);
end $$;

-- ==== 3. Posting =============================================================
-- Auto-post catches up every missed occurrence, each dated on its due date.
do $$
declare v uuid; n int; r record; dates date[];
begin
    v := qa.bill('Rent 31st', 'monthly', '2026-01-31', true, 18000);
    n := public.post_due_recurring('2026-04-15');
    assert n = 3, format('expected 3 posted (31 Jan, 28 Feb, 31 Mar), got %s', n);
    select array_agg(occurred_on order by recurring_seq) into dates
      from public.transactions where recurring_id = v;
    assert dates = array['2026-01-31','2026-02-28','2026-03-31']::date[], format('dates %s', dates);
    select * into r from public.recurring where id = v;
    assert r.next_seq = 3 and r.next_due = '2026-04-30', format('next %s / %s', r.next_seq, r.next_due);
    assert (select bool_and(amount = 18000 and type = 'expense' and note = 'Rent 31st' and source = 'recurring'
                             and account_id = r.account_id and category_id = r.category_id)
              from public.transactions where recurring_id = v), 'posted rows have wrong fields';
    assert (select array_agg(recurring_seq order by recurring_seq) from public.transactions where recurring_id = v)
           = array[0,1,2], 'seqs not 0,1,2';
    n := public.post_due_recurring('2026-04-15');
    assert n = 0, format('second call posted %s', n);
    n := public.post_due_recurring('2026-04-30');
    assert n = 1 and (select next_due from public.recurring where id = v) = '2026-05-31', 'due-today not posted / drifted';
    perform qa.pass('post_due: catches up 3 missed months on their due dates, idempotent, posts on the due day, no drift');
exception when others then perform qa.fail('post_due: catch-up and idempotency', sqlerrm);
end $$;

do $$
declare v_manual uuid; v_paused uuid; v_future uuid; n int; before int;
begin
    v_manual := qa.bill('Manual bill', 'monthly', '2026-01-10', false);
    v_paused := qa.bill('Paused bill', 'monthly', '2026-01-10', true);
    update public.recurring set active = false where id = v_paused;
    v_future := qa.bill('Future bill', 'monthly', '2026-09-10', true);
    select count(*) into before from public.transactions;
    n := public.post_due_recurring('2026-04-15');
    assert (select count(*) from public.transactions where recurring_id in (v_manual, v_paused, v_future)) = 0,
        'posted a manual / paused / future bill';
    perform qa.pass('post_due: skips auto_post=false, paused and not-yet-due bills');
exception when others then perform qa.fail('post_due: skips manual/paused/future', sqlerrm);
end $$;

do $$
declare v uuid; n int; t int;
begin
    v := qa.bill('Old weekly', 'weekly', '2020-01-06', true, 1);
    n := public.post_due_recurring('2026-01-05');      -- 313 weeks due
    select count(*) into t from public.transactions where recurring_id = v;
    assert t = 60, format('cap: expected 60 posted for this bill, got %s', t);
    perform public.post_due_recurring('2026-01-05');
    select count(*) into t from public.transactions where recurring_id = v;
    assert t = 120, format('cap: second call should post 60 more, total %s', t);
    assert (select count(distinct recurring_seq) from public.transactions where recurring_id = v) = 120, 'dup seqs';
    assert (select next_seq from public.recurring where id = v) = 120, 'next_seq not 120';
    perform qa.pass('post_due: caps at 60 per bill per call and resumes correctly');
exception when others then perform qa.fail('post_due: 60 cap', sqlerrm);
end $$;

-- Mark paid / skip.
do $$
declare v uuid; tx uuid; r record; t public.transactions%rowtype; bal0 numeric; bal1 numeric;
begin
    v := qa.bill('Netflix', 'monthly', '2026-01-31', false, 649);
    select balance into bal0 from public.account_balances() where name = 'HDFC';
    tx := public.mark_recurring_paid(v, '2026-02-02');
    select * into t from public.transactions where id = tx;
    assert t.occurred_on = '2026-02-02' and t.recurring_seq = 0 and t.recurring_id = v
       and t.amount = 649 and t.note = 'Netflix' and t.type = 'expense' and t.source = 'recurring',
        format('tx %s', row_to_json(t));
    select * into r from public.recurring where id = v;
    assert r.next_seq = 1 and r.next_due = '2026-02-28', format('after pay: %s %s', r.next_seq, r.next_due);
    select balance into bal1 from public.account_balances() where name = 'HDFC';
    assert bal0 - bal1 = 649, format('balance moved %s', bal0 - bal1);
    perform public.skip_recurring(v);
    select * into r from public.recurring where id = v;
    assert r.next_seq = 2 and r.next_due = '2026-03-31', format('after skip: %s %s', r.next_seq, r.next_due);
    assert (select count(*) from public.transactions where recurring_id = v) = 1, 'skip posted a transaction';
    tx := public.mark_recurring_paid(v);        -- default p_paid_on = current_date
    assert (select occurred_on from public.transactions where id = tx) = current_date, 'default paid date not today';
    assert (select recurring_seq from public.transactions where id = tx) = 2, 'paid seq after skip should be 2';
    perform qa.pass('mark paid / skip: right fields, balance, advance with clamping, skip posts nothing');
exception when others then perform qa.fail('mark paid / skip', sqlerrm);
end $$;

-- Mark paid then post_due for the same occurrence: no double post.
-- (post_due's return value counts every bill, so check this bill's rows.)
do $$
declare v uuid; c int;
begin
    v := qa.bill('Gym', 'monthly', '2026-03-05', true, 1500);
    perform public.mark_recurring_paid(v, '2026-03-04');   -- user pays seq 0 early
    perform public.post_due_recurring('2026-03-31');
    select count(*) into c from public.transactions where recurring_id = v;
    assert c = 1, format('post_due re-posted the paid occurrence (%s rows)', c);
    perform public.post_due_recurring('2026-04-05');
    assert (select array_agg(recurring_seq order by recurring_seq) from public.transactions where recurring_id = v) = array[0,1],
        'after 5 Apr expected seqs 0,1';
    assert (select occurred_on from public.transactions where recurring_id = v and recurring_seq = 1) = '2026-04-05',
        'auto-posted seq 1 should be dated on its due date';
    perform qa.pass('no double post: Mark paid then post_due on the same occurrence');
exception when others then perform qa.fail('no double post: Mark paid then post_due', sqlerrm);
end $$;

-- Seq already posted (e.g. other session got there first): advance and return it.
do $$
declare v uuid; existing uuid; got uuid;
begin
    v := qa.bill('Already', 'monthly', '2026-01-15', false, 10);
    insert into public.transactions (amount, type, account_id, category_id, note, recurring_id, recurring_seq, occurred_on)
    values (10, 'expense', qa.id('A','hdfc'), qa.id('A','bills'), 'Already', v, 0, '2026-01-15')
    returning id into existing;
    got := public.mark_recurring_paid(v, '2026-01-16');
    assert got = existing, 'should return the existing transaction id';
    assert (select count(*) from public.transactions where recurring_id = v) = 1, 'duplicated the posted seq';
    assert (select next_seq from public.recurring where id = v) = 1, 'did not advance';
    perform qa.pass('mark paid on an already-posted seq advances and returns the existing id');
exception when others then perform qa.fail('mark paid on already-posted seq', sqlerrm);
end $$;

-- Deleting / editing a bill keeps history sane.
do $$
declare v uuid; e text;
begin
    v := qa.bill('Delete me', 'monthly', '2026-01-01', false, 10);
    perform public.mark_recurring_paid(v, '2026-01-01');
    delete from public.recurring where id = v;
    assert (select count(*) from public.transactions where note = 'Delete me' and recurring_id is null) = 1,
        'deleting a bill should keep its transactions (recurring_id set null)';
    e := qa.raises(format($q$ delete from public.categories where id = %L $q$, qa.id('A', 'bills')));
    assert e is null, 'deleting a category used by a bill should be refused (on delete restrict): ' || coalesce(e, '');
    perform qa.pass('delete: bill delete keeps history; category in use cannot be deleted');
exception when others then perform qa.fail('delete behaviour', sqlerrm);
end $$;

-- Decision 7: next_due is computed by the database, never trusted from the client.
do $$
declare v uuid; r record; bad text := '';
begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
    values ('No next_due', 'expense', 10, qa.id('A','hdfc'), qa.id('A','bills'), 'monthly', '2026-01-31')
    returning id into v;
    select * into r from public.recurring where id = v;
    if r.next_due is distinct from date '2026-01-31' or r.next_seq <> 0 then bad := bad || format('insert without next_due -> %s/%s; ', r.next_seq, r.next_due); end if;
    -- hostile client value: refusing it or overwriting it are both fine; keeping it is not
    if qa.raises(format($q$ update public.recurring set next_due = '1999-01-01' where id = %L $q$, v)) = 'no error' then
        select * into r from public.recurring where id = v;
        if r.next_due <> public.recurring_occurrence(r.start_date, r.frequency, r.next_seq) then bad := bad || format('client next_due kept: %s; ', r.next_due); end if;
    end if;
    update public.recurring set next_seq = 1 where id = v;
    select * into r from public.recurring where id = v;
    if r.next_due is distinct from date '2026-02-28' then bad := bad || format('next_seq=1 -> %s; ', r.next_due); end if;
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
    values ('Bad next_due', 'expense', 10, qa.id('A','hdfc'), qa.id('A','bills'), 'yearly', '2024-02-29', '2000-01-01')
    returning id into v;
    if (select next_due from public.recurring where id = v) <> '2024-02-29' then bad := bad || 'insert kept the client next_due; '; end if;
    delete from public.recurring where name in ('No next_due', 'Bad next_due');
    if bad = '' then perform qa.pass('decision 7: next_due always = occurrence(start, freq, next_seq), client values ignored');
    else perform qa.fail('decision 7: next_due computed by the database', bad); end if;
exception when others then perform qa.fail('decision 7: next_due computed by the database', sqlerrm);
end $$;

-- Decision 10: paused bills can be paid / skipped by hand.
do $$
declare v uuid; tx uuid;
begin
    v := qa.bill('Paused manual', 'monthly', '2026-02-10', true, 50);
    update public.recurring set active = false where id = v;
    tx := public.mark_recurring_paid(v, '2026-02-11');
    perform public.skip_recurring(v);
    assert tx is not null and (select next_seq from public.recurring where id = v) = 2, 'pay/skip on a paused bill did not advance';
    assert (select count(*) from public.transactions where recurring_id = v) = 1, 'paused bill: wrong number of rows';
    perform public.post_due_recurring('2026-12-31');
    assert (select count(*) from public.transactions where recurring_id = v) = 1, 'paused auto-post bill was auto-posted';
    perform qa.pass('decision 10: paused bill can be paid and skipped by hand, never auto-posted');
exception when others then perform qa.fail('decision 10: paused bill pay/skip by hand', sqlerrm);
end $$;

-- Decision 14: resume skips the dates missed while paused (the app advances
-- next_seq in the same update that sets active); nothing is back-posted.
do $$
declare v uuid; k int; r record;
begin
    v := qa.bill('Resume me', 'weekly', current_date - 70, true, 20);
    update public.recurring set active = false where id = v;
    -- what the front end does on Resume: first occurrence on/after today, same update
    select min(n) into k from generate_series(0, 1000) n
     where public.recurring_occurrence(current_date - 70, 'weekly', n) >= current_date;
    update public.recurring set active = true, next_seq = k where id = v;
    select * into r from public.recurring where id = v;
    assert r.next_due >= current_date and r.next_due < current_date + 7, format('next_due after resume %s', r.next_due);
    perform public.post_due_recurring(current_date - 1);
    assert (select count(*) from public.transactions where recurring_id = v) = 0, 'backlog posted after resume';
    perform public.post_due_recurring(r.next_due);
    assert (select array_agg(recurring_seq) from public.transactions where recurring_id = v) = array[k],
        'resume: first post should be seq k on its due date';
    perform qa.pass('decision 14: resume skips missed dates, no backlog, next post is the first future occurrence');
exception when others then perform qa.fail('decision 14: resume without backlog', sqlerrm);
end $$;

-- Decision 12: reschedule_recurring renumbers history negative and starts over.
do $$
declare v uuid; tx uuid; r record; seqs int[]; bal0 numeric; bal1 numeric; e text;
begin
    v := qa.bill('Resched', 'monthly', '2026-01-31', false, 300);
    perform public.mark_recurring_paid(v, '2026-01-31');
    perform public.mark_recurring_paid(v, '2026-02-28');
    perform public.mark_recurring_paid(v, '2026-03-31');            -- seqs 0,1,2; next_seq 3
    perform public.reschedule_recurring(v, '2026-06-01', 'weekly');
    select * into r from public.recurring where id = v;
    assert r.start_date = '2026-06-01' and r.frequency = 'weekly' and r.next_seq = 0 and r.next_due = '2026-06-01',
        format('after reschedule: %s', row_to_json(r));
    select array_agg(recurring_seq order by recurring_seq) into seqs from public.transactions where recurring_id = v;
    assert seqs = array[-3,-2,-1], format('history seqs after reschedule: %s (want -3,-2,-1)', seqs);
    -- the bug decision 12 fixes: paying the new seq 0 must post a NEW transaction
    select balance into bal0 from public.account_balances() where name = 'HDFC';
    tx := public.mark_recurring_paid(v, '2026-06-01');
    select balance into bal1 from public.account_balances() where name = 'HDFC';
    assert (select occurred_on from public.transactions where id = tx) = '2026-06-01', 'mark paid after reschedule returned an old transaction';
    assert bal0 - bal1 = 300, format('mark paid after reschedule moved balance by %s', bal0 - bal1);
    update public.recurring set auto_post = true where id = v;
    perform public.post_due_recurring('2026-06-15');
    select array_agg(recurring_seq order by recurring_seq) into seqs from public.transactions where recurring_id = v;
    assert seqs = array[-3,-2,-1,0,1,2], format('after post_due: %s', seqs);
    -- reschedule again: everything shifts by next_seq (3) and stays unique
    perform public.reschedule_recurring(v, '2026-07-01', 'monthly');
    select array_agg(recurring_seq order by recurring_seq) into seqs from public.transactions where recurring_id = v;
    assert seqs = array[-6,-5,-4,-3,-2,-1], format('second reschedule: %s (want -6..-1)', seqs);
    assert (select count(*) from public.transactions where recurring_id = v) = 6, 'history rows lost or added';
    e := qa.raises(format($q$ select public.reschedule_recurring(%L, '2026-07-01', 'daily') $q$, v), array['22023']);
    assert e is null, 'bad frequency: ' || coalesce(e, '');
    perform qa.pass('decision 12: reschedule keeps history (negative seqs), new seq 0 posts a new transaction, twice in a row');
exception when others then perform qa.fail('decision 12: reschedule_recurring', sqlerrm);
end $$;

-- Upcoming list (uses current_date).
do $$
declare v_over uuid; v_today uuid; v_3 uuid; v_8 uuid; v_paused uuid; r record; ids uuid[];
begin
    v_over := qa.bill('Overdue 2', 'monthly', current_date - 2);
    v_today := qa.bill('Due today', 'monthly', current_date);
    v_3 := qa.bill('In 3', 'weekly', current_date + 3);
    v_8 := qa.bill('In 8', 'yearly', current_date + 8);
    v_paused := qa.bill('Paused soon', 'monthly', current_date + 1);
    update public.recurring set active = false where id = v_paused;
    select array_agg(id order by next_due) into ids from public.upcoming_recurring(7)
     where id in (v_over, v_today, v_3, v_8, v_paused);
    assert ids = array[v_over, v_today, v_3], format('upcoming(7) wrong set/order: %s', ids);
    select * into r from public.upcoming_recurring(7) where id = v_over;
    assert r.days_left = -2 and r.overdue, format('overdue row %s', row_to_json(r));
    select * into r from public.upcoming_recurring(7) where id = v_today;
    assert r.days_left = 0 and not r.overdue, format('due-today row %s', row_to_json(r));
    select * into r from public.upcoming_recurring(7) where id = v_3;
    assert r.days_left = 3, format('in-3 row %s', row_to_json(r));
    assert exists (select 1 from public.upcoming_recurring(0) where id = v_over), 'overdue must show even with p_days = 0';
    assert exists (select 1 from public.upcoming_recurring(8) where id = v_8), 'p_days = 8 should include a bill due in 8 days';
    perform qa.pass('upcoming: overdue always, due today = 0 days not overdue, window, paused hidden, ordered');
exception when others then perform qa.fail('upcoming_recurring', sqlerrm);
end $$;

-- ==== 4. Cross-user (RLS) =====================================================
reset role;
create table qa.a_bill as select id from public.recurring
  where user_id = :'A' and name = 'Netflix';
grant select on qa.a_bill to public;
set role app_user;
set request.jwt.claim.sub = :'B';
do $$
declare v uuid := (select id from qa.a_bill); n int; e text; bad text := '';
begin
    if v is null then raise exception 'setup: A''s Netflix bill missing (earlier case failed)'; end if;
    select count(*) into n from public.recurring;
    if n <> 0 then bad := bad || format('B sees %s of A''s bills; ', n); end if;
    update public.recurring set amount = 1 where id = v; get diagnostics n = row_count;
    if n <> 0 then bad := bad || 'B updated A''s bill; '; end if;
    delete from public.recurring where id = v; get diagnostics n = row_count;
    if n <> 0 then bad := bad || 'B deleted A''s bill; '; end if;
    e := qa.raises(format('select public.mark_recurring_paid(%L)', v), array['P0002']);
    if e is not null then bad := bad || 'mark paid A''s bill: ' || e || '; '; end if;
    e := qa.raises(format('select public.skip_recurring(%L)', v), array['P0002']);
    if e is not null then bad := bad || 'skip A''s bill: ' || e || '; '; end if;
    e := qa.raises(format($q$ select public.reschedule_recurring(%L, '2026-01-01', 'weekly') $q$, v), array['P0002']);
    if e is not null then bad := bad || 'reschedule A''s bill: ' || e || '; '; end if;
    if exists (select 1 from public.upcoming_recurring(100000) where id = v) then bad := bad || 'upcoming shows A''s bill; '; end if;
    perform public.post_due_recurring('2030-01-01');
    if bad = '' then perform qa.pass('RLS: B cannot see, edit, delete, pay, skip, reschedule or list A''s bill');
    else perform qa.fail('RLS: B vs A''s bill', bad); end if;
exception when others then perform qa.fail('RLS: B vs A''s bill', sqlerrm);
end $$;

do $$
declare cases text[] := array[
    -- A's account, own category
    $q$ insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('x', 'expense', 1, qa.id('A','hdfc'), qa.id('B','bills'), 'monthly', '2026-01-01', '2026-01-01') $q$,
    -- own account, A's category
    $q$ insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('x', 'expense', 1, qa.id('B','cash'), qa.id('A','bills'), 'monthly', '2026-01-01', '2026-01-01') $q$,
    -- row owned by A
    $q$ insert into public.recurring (user_id, name, type, amount, account_id, category_id, frequency, start_date, next_due)
        values ('11111111-1111-1111-1111-111111111111', 'x', 'expense', 1, qa.id('A','hdfc'), qa.id('A','bills'), 'monthly', '2026-01-01', '2026-01-01') $q$];
    c text; e text; bad text := '';
begin
    foreach c in array cases loop
        e := qa.raises(c);
        if e is not null then bad := bad || '[' || left(btrim(c), 60) || ' -> ' || e || '] '; end if;
    end loop;
    if bad = '' then perform qa.pass('RLS: B cannot create a bill on A''s account, with A''s category, or owned by A');
    else perform qa.fail('RLS: B attaching A''s account/category', bad); end if;
end $$;

do $$
declare v uuid; e text; bad text := '';
begin
    v := qa.bill('B own', 'monthly', '2026-01-01');
    e := qa.raises(format($q$ update public.recurring set account_id = qa.id('A','hdfc') where id = %L $q$, v));
    if e is not null then bad := bad || 'move to A''s account: ' || e || '; '; end if;
    e := qa.raises(format($q$ update public.recurring set category_id = qa.id('A','bills') where id = %L $q$, v));
    if e is not null then bad := bad || 'move to A''s category: ' || e || '; '; end if;
    e := qa.raises(format($q$ update public.recurring set user_id = '11111111-1111-1111-1111-111111111111' where id = %L $q$, v));
    if e is not null then bad := bad || 'give to A: ' || e || '; '; end if;
    if bad = '' then perform qa.pass('RLS: B cannot move own bill onto A''s account/category or hand it to A');
    else perform qa.fail('RLS: B editing own bill to point at A', bad); end if;
exception when others then perform qa.fail('RLS: B editing own bill', sqlerrm);
end $$;

-- B squats A's future occurrences by attaching own transactions to A's bill id.
-- If allowed, A's next posting hits the unique index and fails (denial of service).
do $$
declare v uuid := (select id from qa.a_bill); e text;
begin
    e := qa.raises(format($q$ insert into public.transactions (amount, type, account_id, category_id, recurring_id, recurring_seq)
        select 1, 'expense', qa.id('B','cash'), qa.id('B','food'), %L, s from generate_series(100, 150) s $q$, v));   -- future seqs, so no clash with A's own rows
    if e is null then perform qa.pass('RLS: B cannot attach transactions to A''s bill (no seq squatting)');
    else perform qa.fail('RLS: B attaches transactions to A''s bill id (seq squatting DoS)', e); end if;
end $$;

set request.jwt.claim.sub = :'A';
do $$
declare v uuid := (select id from qa.a_bill); before int;
begin
    before := (select next_seq from public.recurring where id = v);
    perform public.mark_recurring_paid(v, '2026-04-01');
    assert (select next_seq from public.recurring where id = v) = before + 1, 'did not advance';
    perform qa.pass('RLS: A can still pay own bill after B''s attempts');
exception when others then perform qa.fail('RLS: A pays own bill after B''s attempts', sqlerrm);
end $$;

-- Not signed in.
set request.jwt.claim.sub = '';
do $$
declare e text; bad text := '';
begin
    e := qa.raises('select public.post_due_recurring()', array['42501']);
    if e is not null then bad := bad || 'post_due: ' || e || '; '; end if;
    e := qa.raises(format('select public.mark_recurring_paid(%L)', (select id from qa.a_bill)), array['42501']);
    if e is not null then bad := bad || 'mark paid: ' || e || '; '; end if;
    e := qa.raises(format('select public.skip_recurring(%L)', (select id from qa.a_bill)), array['42501']);
    if e is not null then bad := bad || 'skip: ' || e || '; '; end if;
    e := qa.raises(format($q$ select public.reschedule_recurring(%L, '2026-01-01', 'weekly') $q$, (select id from qa.a_bill)), array['42501']);
    if e is not null then bad := bad || 'reschedule: ' || e || '; '; end if;
    if bad = '' then perform qa.pass('anonymous: post/pay/skip/reschedule raise 42501');
    else perform qa.fail('anonymous calls', bad); end if;
end $$;

-- ==== 5. Detection: suggest_recurring on the shared fixture ======================
-- User C gets the fixture transactions (A's data is polluted by the tests above).
reset role;
insert into auth.users (id, email) values ('33333333-3333-3333-3333-333333333333', 'c@x.com');
create table qa.det as select :'detection'::json as j;
grant select on qa.det to public;
set role app_user;
set request.jwt.claim.sub = '33333333-3333-3333-3333-333333333333';
insert into public.accounts (name, type, opening_balance) values ('HDFC', 'bank', 0);
insert into public.transactions (occurred_on, type, amount, note, account_id, to_account_id, category_id)
select t.occurred_on, t.type, t.amount, t.note,
       (select id from public.accounts where name = t.account),
       (select id from public.accounts where name = t.to_account),
       (select id from public.categories where name = t.category and kind = t.type)
  from qa.det, json_to_recordset(j -> 'transactions')
       as t(occurred_on date, type text, amount numeric, note text, category text, account text, to_account text);
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, next_due)
select b.name, b.type, b.amount, (select id from public.accounts where name = 'HDFC'),
       (select id from public.categories where name = b.category and kind = b.type), b.frequency, b.start_date, b.start_date
  from qa.det, json_to_recordset(j -> 'existing_bills')
       as b(name text, type text, amount numeric, frequency text, start_date date, category text);

create function pg_temp.norm(s text) returns text language sql immutable as
$$ select btrim(regexp_replace(regexp_replace(lower(coalesce(s, '')), '[^a-z ]', ' ', 'g'), '\s+', ' ', 'g')) $$;
-- Decision 4: a paused bill still covers its group.
update public.recurring set active = false where name = 'RENT!';
create temp table got as select * from public.suggest_recurring(3);

do $$
declare e record; g record; bad text := '';
begin
    for e in select * from qa.det, json_to_recordset(j -> 'expected')
             as x(name text, type text, frequency text, occurrences int, last_seen date, next_guess date, amount numeric)
    loop
        select * into g from got where pg_temp.norm(got.name) = e.name and got.type = e.type;
        if not found then bad := bad || format('[missing %s] ', e.name); continue; end if;
        if g.frequency <> e.frequency then bad := bad || format('[%s freq %s want %s] ', e.name, g.frequency, e.frequency); end if;
        if g.occurrences <> e.occurrences then bad := bad || format('[%s occ %s want %s] ', e.name, g.occurrences, e.occurrences); end if;
        if g.last_seen <> e.last_seen then bad := bad || format('[%s last_seen %s want %s] ', e.name, g.last_seen, e.last_seen); end if;
        if e.next_guess is not null and g.next_guess is distinct from e.next_guess then
            bad := bad || format('[%s next_guess %s want %s] ', e.name, g.next_guess, e.next_guess); end if;
        if e.amount is not null and g.amount <> e.amount then bad := bad || format('[%s amount %s want %s] ', e.name, g.amount, e.amount); end if;
        -- decision 2: (gaps in band / gaps) x (kept / rows); every fixture TP is clean -> 1
        if g.confidence is null or abs(g.confidence - 1) > 0.005 then bad := bad || format('[%s confidence %s want 1] ', e.name, g.confidence); end if;
        if g.account_id is distinct from (select id from public.accounts where name = 'HDFC') then bad := bad || format('[%s account] ', e.name); end if;
        if g.category_id is null or not exists (select 1 from public.categories c where c.id = g.category_id and c.kind = g.type) then
            bad := bad || format('[%s category missing/wrong kind] ', e.name); end if;
    end loop;
    if bad = '' then perform qa.pass('detection: 6 true positives (weekly, monthly, quarterly, yearly, income, varying amount) with right fields');
    else perform qa.fail('detection: true positives', bad); end if;
end $$;

do $$
declare bad text;
begin
    select string_agg(format('%s/%s/%s', name, type, frequency), ', ') into bad
      from got where pg_temp.norm(name) in (select json_array_elements_text(j -> 'must_not_suggest') from qa.det)
         or pg_temp.norm(name) not in (select x.name from qa.det, json_to_recordset(j -> 'expected') as x(name text));
    if bad is null then perform qa.pass('detection: no false positives (irregular, wild amounts, <3, blank/digit notes, split type, paused covering bill, transfers)');
    else perform qa.fail('detection: false positives', bad); end if;
end $$;

do $$
declare n int;
begin
    select count(*) into n from public.suggest_recurring(4) where pg_temp.norm(name) = 'lic premium';
    assert n = 0, 'min_occurrences 4 should drop LIC (3 payments)';
    assert exists (select 1 from public.suggest_recurring(4) where pg_temp.norm(name) = 'netflix'), 'netflix lost at 4';
    perform qa.pass('detection: p_min_occurrences honoured');
exception when others then perform qa.fail('detection: p_min_occurrences', sqlerrm);
end $$;

-- Decision 13: ordinary random spend produces no suggestions at all.
reset role;
insert into auth.users (id, email) values ('44444444-4444-4444-4444-444444444444', 'd@x.com');
create table qa.rnd as select :'random_spend'::json as j;
grant select on qa.rnd to public;
set role app_user;
set request.jwt.claim.sub = '44444444-4444-4444-4444-444444444444';
do $$
declare k int; bad text := ''; got text;
begin
    for k in 0 .. json_array_length((select j -> 'sets' from qa.rnd)) - 1 loop
        delete from public.transactions;
        insert into public.transactions (occurred_on, type, amount, note, account_id, category_id)
        select t.occurred_on, 'expense', t.amount, t.note, (select id from public.accounts where name = 'Cash'),
               (select id from public.categories where name = 'Other' and kind = 'expense')
          from qa.rnd, json_to_recordset(j -> 'sets' -> k) as t(occurred_on date, amount numeric, note text);
        select string_agg(format('%s %s x%s conf %s', name, frequency, occurrences, confidence), '; ') into got
          from public.suggest_recurring(3);
        if got is not null then bad := bad || format('[set %s: %s] ', k, got); end if;
    end loop;
    if bad = '' then perform qa.pass('decision 13: 3 sets of 300 random expenses give no suggestions');
    else perform qa.fail('decision 13: random spend suggested', bad); end if;
exception when others then perform qa.fail('decision 13: random spend', sqlerrm);
end $$;

-- Covered-by-bill exclusion is per user; and B never sees C's patterns.
set request.jwt.claim.sub = :'B';
do $$ begin
    assert not exists (select 1 from public.suggest_recurring(3) where pg_temp.norm(name) in ('netflix', 'cult gym')),
        'B sees suggestions built from C''s transactions';
    perform qa.pass('detection: suggestions are per user (RLS)');
exception when others then perform qa.fail('detection: per user', sqlerrm);
end $$;

-- ==== Summary =================================================================
reset role;
\echo ''
select case when ok then 'PASS' else 'FAIL' end as result, name, coalesce(detail, '') as detail
  from qa.results order by n;
select count(*) filter (where ok) as passed, count(*) filter (where not ok) as failed, count(*) as total
  from qa.results \gset
\echo 'SPRINT2 SQL GATE:' :passed 'passed,' :failed 'failed,' :total 'recorded'
