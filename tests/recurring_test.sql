-- Money Tracker — recurring bills test (Sprint 2, migration 0007).
--
-- Runs the real migrations against a local PostgreSQL with the same Supabase
-- mock as rls_and_logic_test.sql, then checks:
--   * recurring_occurrence date math: month-end clamping (29th/30th/31st),
--     Feb in leap and non-leap years, yearly on 29 Feb, weekly across year end,
--     plus a property check over thousands of (start, frequency, n) cases,
--   * the table: next_due kept in sync, kind check, ownership checks,
--   * mark_recurring_paid / skip_recurring: anchored advance, idempotency,
--   * post_due_recurring: catch-up, the 60-per-call cap, idempotency, and no
--     double posting after a manual Mark paid,
--   * upcoming_recurring, and RLS between two users.
--
-- The app role here gets function access only through membership of
-- `authenticated`, so the migration's grants are exercised for real.
-- tests/run.sh then drives a two-session post_due_recurring race against the
-- bill created for user R at the end of this file.

\set ON_ERROR_STOP on
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'
\set C '33333333-3333-3333-3333-333333333333'
\set R '44444444-4444-4444-4444-444444444444'

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
grant usage on schema public, auth to app_user, authenticated;
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

grant select, insert, update, delete on all tables in schema public to app_user;
-- Function access exactly as in production: only via `authenticated`.
grant authenticated to app_user;

insert into auth.users (id, email) values
    (:'A', 'a@x.com'), (:'B', 'b@x.com'), (:'C', 'c@x.com'), (:'R', 'r@x.com');

-- ---- Grants: anon and public cannot call the new functions -----------------
do $$
declare f text;
begin
    foreach f in array array[
        'public.recurring_occurrence(date,text,integer)',
        'public.mark_recurring_paid(uuid,date)',
        'public.skip_recurring(uuid)',
        'public.post_due_recurring(date)',
        'public.upcoming_recurring(integer)']
    loop
        assert not has_function_privilege('anon', f, 'execute'), format('anon can execute %s', f);
        assert has_function_privilege('authenticated', f, 'execute'), format('authenticated cannot execute %s', f);
    end loop;
end $$;

-- ============================================================================
-- recurring_occurrence: date math
-- ============================================================================
set role app_user;
set request.jwt.claim.sub = :'A';

do $$
declare
    cases constant text[][] := array[
        -- start,        freq,        n,  expected
        -- n = 0 is the start date
        ['2024-01-31', 'monthly',    '0', '2024-01-31'],
        -- 31st: clamps in short months, returns to the 31st (no drift)
        ['2024-01-31', 'monthly',    '1', '2024-02-29'],   -- leap Feb
        ['2024-01-31', 'monthly',    '2', '2024-03-31'],
        ['2024-01-31', 'monthly',    '3', '2024-04-30'],
        ['2024-01-31', 'monthly',    '4', '2024-05-31'],
        ['2024-01-31', 'monthly',   '13', '2025-02-28'],   -- non-leap Feb
        ['2023-01-31', 'monthly',    '1', '2023-02-28'],
        ['2023-01-31', 'monthly',    '2', '2023-03-31'],
        ['2024-08-31', 'monthly',    '1', '2024-09-30'],
        ['2024-12-31', 'monthly',    '2', '2025-02-28'],
        -- 30th
        ['2023-01-30', 'monthly',    '1', '2023-02-28'],
        ['2023-01-30', 'monthly',    '2', '2023-03-30'],
        ['2024-01-30', 'monthly',    '1', '2024-02-29'],
        ['2024-01-30', 'monthly',   '13', '2025-02-28'],
        -- 29th
        ['2023-01-29', 'monthly',    '1', '2023-02-28'],
        ['2023-01-29', 'monthly',    '2', '2023-03-29'],
        ['2024-01-29', 'monthly',    '1', '2024-02-29'],
        ['2024-01-29', 'monthly',   '13', '2025-02-28'],
        ['2024-01-29', 'monthly',   '14', '2025-03-29'],
        -- quarterly
        ['2023-11-30', 'quarterly',  '1', '2024-02-29'],
        ['2023-11-30', 'quarterly',  '2', '2024-05-30'],
        ['2024-08-31', 'quarterly',  '2', '2025-02-28'],
        ['2024-08-31', 'quarterly',  '3', '2025-05-31'],
        -- yearly on 29 Feb: 28 Feb in non-leap years, back to 29 Feb in leap years
        ['2024-02-29', 'yearly',     '1', '2025-02-28'],
        ['2024-02-29', 'yearly',     '3', '2027-02-28'],
        ['2024-02-29', 'yearly',     '4', '2028-02-29'],
        ['2024-02-29', 'yearly',   '100', '2124-02-29'],
        ['2096-02-29', 'yearly',     '4', '2100-02-28'],   -- 2100 is not a leap year
        -- weekly across year end
        ['2024-12-28', 'weekly',     '1', '2025-01-04'],
        ['2025-12-31', 'weekly',     '1', '2026-01-07'],
        ['2025-12-25', 'weekly',     '2', '2026-01-08'],
        ['2024-01-01', 'weekly',    '52', '2024-12-30'],
        ['2024-02-29', 'weekly',     '1', '2024-03-07']
    ];
    i int; got date;
begin
    for i in 1 .. array_length(cases, 1) loop
        got := public.recurring_occurrence(cases[i][1]::date, cases[i][2], cases[i][3]::int);
        assert got = cases[i][4]::date,
            format('recurring_occurrence(%s, %s, %s) = %s, expected %s',
                   cases[i][1], cases[i][2], cases[i][3], got, cases[i][4]);
    end loop;
end $$;

-- Property check against an independent formula: for month-based schedules,
-- occurrence n is in month (start month + n*k) on day least(start day, month length);
-- weekly is start + 7n. Every start day 1..31 of 2023 and 2024, n = 0..60.
do $$
declare
    v_start date; v_freq text; v_k int; n int;
    v_month date; v_last int; v_expect date; v_got date; v_checked int := 0;
begin
    for v_start in select d::date from generate_series('2023-01-01'::date, '2024-12-31'::date, '1 day') d
    loop
        foreach v_freq in array array['weekly', 'monthly', 'quarterly', 'yearly'] loop
            for n in 0 .. 60 loop
                if v_freq = 'weekly' then
                    v_expect := v_start + 7 * n;
                else
                    v_k := case v_freq when 'monthly' then 1 when 'quarterly' then 3 else 12 end;
                    v_month := (date_trunc('month', v_start) + make_interval(months => v_k * n))::date;
                    v_last := extract(day from (v_month + interval '1 month' - interval '1 day'))::int;
                    v_expect := v_month + (least(extract(day from v_start)::int, v_last) - 1);
                end if;
                v_got := public.recurring_occurrence(v_start, v_freq, n);
                if v_got <> v_expect then
                    raise exception 'recurring_occurrence(%, %, %) = %, expected %',
                        v_start, v_freq, n, v_got, v_expect;
                end if;
                v_checked := v_checked + 1;
            end loop;
        end loop;
    end loop;
    assert v_checked = 731 * 4 * 61, format('checked %s cases', v_checked);
end $$;

-- Bad input -> 22023
do $$ begin
    perform public.recurring_occurrence('2024-01-01', 'daily', 1);
    raise exception 'unknown frequency should be rejected';
exception when sqlstate '22023' then null; end $$;
do $$ begin
    perform public.recurring_occurrence('2024-01-01', 'monthly', -1);
    raise exception 'negative n should be rejected';
exception when sqlstate '22023' then null; end $$;
do $$ begin
    perform public.recurring_occurrence(null, 'monthly', 1);
    raise exception 'null start should be rejected';
exception when sqlstate '22023' then null; end $$;

-- ============================================================================
-- Table: next_due sync, kind check, ownership checks (as A)
-- ============================================================================
insert into public.accounts (name, type, opening_balance) values ('HDFC', 'bank', 10000);

insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
    select 'Rent', 'expense', 15000.00, (select id from accounts where name = 'HDFC'),
           (select id from categories where name = 'Rent' and kind = 'expense'),
           'monthly', '2024-01-31';

do $$
declare r public.recurring%rowtype;
begin
    select * into r from public.recurring where name = 'Rent';
    assert r.user_id = '11111111-1111-1111-1111-111111111111', 'user_id should default to auth.uid()';
    assert r.next_seq = 0 and r.next_due = '2024-01-31', format('next_due not synced: %s', r.next_due);
    assert not r.auto_post and r.active and r.source = 'manual', 'wrong defaults';

    -- next_due is derived; a client cannot set it to something inconsistent.
    update public.recurring set next_due = '2030-01-01' where id = r.id;
    assert (select next_due from public.recurring where id = r.id) = '2024-01-31',
        'next_due should be recomputed from start_date/frequency/next_seq';
end $$;

-- Category kind must match type -> 22023
do $$ begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
        select 'Bad', 'income', 10, (select id from accounts where name = 'HDFC'),
               (select id from categories where name = 'Rent' and kind = 'expense'),
               'monthly', current_date;
    raise exception 'kind mismatch should be rejected';
exception when sqlstate '22023' then null; end $$;
do $$ begin
    update public.recurring set type = 'income' where name = 'Rent';
    raise exception 'kind mismatch on update should be rejected';
exception when sqlstate '22023' then null; end $$;

-- Table constraints
do $$ begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
        select '   ', 'expense', 10, (select id from accounts where name = 'HDFC'),
               (select id from categories where name = 'Rent'), 'monthly', current_date;
    raise exception 'blank name should be rejected';
exception when check_violation then null; end $$;
do $$ begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
        select 'Zero', 'expense', 0, (select id from accounts where name = 'HDFC'),
               (select id from categories where name = 'Rent'), 'monthly', current_date;
    raise exception 'zero amount should be rejected';
exception when check_violation then null; end $$;
do $$ begin
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
        select 'Daily', 'expense', 10, (select id from accounts where name = 'HDFC'),
               (select id from categories where name = 'Rent'), 'daily', current_date;
    raise exception 'bad frequency should be rejected';
exception when check_violation or sqlstate '22023' then null; end $$;

-- ============================================================================
-- mark_recurring_paid / skip_recurring (as A)
-- ============================================================================
do $$
declare
    v_id uuid; v_tx uuid; v_tx2 uuid; v_before numeric; v_after numeric;
    t public.transactions%rowtype; r public.recurring%rowtype;
begin
    select id into v_id from public.recurring where name = 'Rent';
    select balance into v_before from public.account_balances() where name = 'HDFC';

    v_tx := public.mark_recurring_paid(v_id, '2024-02-01');
    select * into t from public.transactions where id = v_tx;
    assert t.type = 'expense' and t.amount = 15000.00, 'wrong type/amount';
    assert t.occurred_on = '2024-02-01', format('occurred_on should be the paid-on date, got %s', t.occurred_on);
    assert t.note = 'Rent', format('note should be the bill name, got %s', t.note);
    assert t.recurring_id = v_id and t.recurring_seq = 0, 'wrong recurring link';
    assert t.category_id = (select id from categories where name = 'Rent' and kind = 'expense'), 'wrong category';
    select balance into v_after from public.account_balances() where name = 'HDFC';
    assert v_before - v_after = 15000.00, format('balance should drop by exactly 15000.00, %s -> %s', v_before, v_after);

    select * into r from public.recurring where id = v_id;
    assert r.next_seq = 1 and r.next_due = '2024-02-29', format('after pay 1: seq %s due %s', r.next_seq, r.next_due);

    v_tx2 := public.mark_recurring_paid(v_id, '2024-02-29');
    assert v_tx2 <> v_tx, 'second payment should be a new transaction';
    select * into r from public.recurring where id = v_id;
    assert r.next_seq = 2 and r.next_due = '2024-03-31', format('anchored: expected 31 Mar, got %s', r.next_due);

    -- Skip: advances without posting.
    perform public.skip_recurring(v_id);
    select * into r from public.recurring where id = v_id;
    assert r.next_seq = 3 and r.next_due = '2024-04-30', format('after skip: seq %s due %s', r.next_seq, r.next_due);
    assert (select count(*) from public.transactions where recurring_id = v_id) = 2, 'skip should not post';

    -- Idempotency: occurrence 3 is already posted (e.g. by a racing auto-post);
    -- Mark paid returns that transaction and just advances.
    insert into public.transactions (amount, type, account_id, category_id, note, recurring_id, recurring_seq)
        select 15000, 'expense', r.account_id, r.category_id, 'Rent', v_id, 3
        returning id into v_tx;
    v_tx2 := public.mark_recurring_paid(v_id, '2024-05-01');
    assert v_tx2 = v_tx, 'already-posted occurrence should return the existing transaction';
    assert (select count(*) from public.transactions where recurring_id = v_id and recurring_seq = 3) = 1,
        'occurrence 3 posted twice';
    select * into r from public.recurring where id = v_id;
    assert r.next_seq = 4 and r.next_due = '2024-05-31', format('after idempotent pay: seq %s due %s', r.next_seq, r.next_due);

    -- The unique index itself refuses a second row for the same occurrence.
    begin
        insert into public.transactions (amount, type, account_id, category_id, recurring_id, recurring_seq)
            select 1, 'expense', r.account_id, r.category_id, v_id, 3;
        raise exception 'duplicate occurrence should violate the unique index';
    exception when unique_violation then null; end;
end $$;

-- Bad input / not found
do $$ begin
    perform public.mark_recurring_paid((select id from public.recurring where name = 'Rent'), null);
    raise exception 'null paid-on should be rejected';
exception when sqlstate '22023' then null; end $$;
do $$ begin
    perform public.mark_recurring_paid(gen_random_uuid());
    raise exception 'unknown bill should be P0002';
exception when sqlstate 'P0002' then null; end $$;
do $$ begin
    perform public.skip_recurring(gen_random_uuid());
    raise exception 'unknown bill should be P0002';
exception when sqlstate 'P0002' then null; end $$;

-- ============================================================================
-- post_due_recurring (as A, fixed as-of dates so the test is deterministic)
-- ============================================================================
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, auto_post)
select v.name, v.type, v.amount, (select id from accounts where name = 'HDFC'),
       (select id from categories where name = v.cat and kind = v.type),
       v.freq, v.start::date, v.auto
  from (values
    ('Gym',       'expense',  499.99, 'Health',        'weekly',  '2025-01-01', true),
    ('Netflix',   'expense',  649.00, 'Entertainment', 'monthly', '2025-01-31', false),  -- manual: never auto-posted
    ('Paused',    'expense',  100.00, 'Other',         'monthly', '2025-01-01', true),
    ('Salary',    'income', 85000.50, 'Salary',        'monthly', '2025-01-31', true),
    ('Old sub',   'expense',    1.00, 'Bills',         'weekly',  '2020-01-01', true),
    ('Insurance', 'expense', 12000.00, 'Bills',        'yearly',  '2024-02-29', true)
  ) as v(name, type, amount, cat, freq, start, auto);
update public.recurring set active = false where name = 'Paused';

-- Cap first: 'Old sub' has hundreds of missed weeks; each call posts at most 60.
do $$
declare
    v_id uuid; v_calls int := 0; v_total int := 0; v_n int; v_expected int;
begin
    select id into v_id from public.recurring where name = 'Old sub';
    v_expected := ('2025-03-31'::date - '2020-01-01'::date) / 7 + 1;   -- occurrences on/before as-of
    loop
        v_n := public.post_due_recurring('2025-03-31');
        v_calls := v_calls + 1;
        exit when v_n = 0;
        assert (select count(*) from public.transactions where recurring_id = v_id) <= 60 * v_calls,
            format('more than 60 posted for one bill in call %s', v_calls);
        v_total := v_total + v_n;
        assert v_calls < 20, 'post_due_recurring never settled';
    end loop;
    assert (select count(*) from public.transactions where recurring_id = v_id) = v_expected,
        format('Old sub: expected %s occurrences, got %s', v_expected,
               (select count(*) from public.transactions where recurring_id = v_id));
    assert v_calls = ceil(v_expected / 60.0)::int + 1,
        format('expected %s calls to catch up, took %s', ceil(v_expected / 60.0)::int + 1, v_calls);
    assert (select next_due from public.recurring where id = v_id) > '2025-03-31', 'Old sub not caught up';
end $$;

-- Per-bill outcome of the catch-up as of 2025-03-31.
do $$
declare r public.recurring%rowtype; v_dates date[]; v_sum numeric;
begin
    -- Gym, weekly from Wed 1 Jan 2025: 13 occurrences up to 26 Mar, each dated on its due date.
    select * into r from public.recurring where name = 'Gym';
    select array_agg(occurred_on order by recurring_seq), sum(amount) into v_dates, v_sum
      from public.transactions where recurring_id = r.id;
    assert array_length(v_dates, 1) = 13, format('Gym: %s posted', array_length(v_dates, 1));
    assert v_dates[1] = '2025-01-01' and v_dates[13] = '2025-03-26', format('Gym dates %s', v_dates);
    assert v_sum = 13 * 499.99, format('Gym total should be exactly %s, got %s', 13 * 499.99, v_sum);
    assert r.next_seq = 13 and r.next_due = '2025-04-02', format('Gym next %s %s', r.next_seq, r.next_due);

    -- Salary (income) from 31 Jan: 31 Jan, 28 Feb, 31 Mar (clamped, no drift).
    select * into r from public.recurring where name = 'Salary';
    select array_agg(occurred_on order by recurring_seq) into v_dates
      from public.transactions where recurring_id = r.id;
    assert v_dates = array['2025-01-31', '2025-02-28', '2025-03-31']::date[], format('Salary dates %s', v_dates);
    assert (select bool_and(type = 'income') from public.transactions where recurring_id = r.id), 'Salary type';
    assert r.next_due = '2025-04-30', format('Salary next_due %s', r.next_due);

    -- Insurance yearly on 29 Feb 2024: 2024-02-29 and 2025-02-28; next 2026-02-28.
    select * into r from public.recurring where name = 'Insurance';
    select array_agg(occurred_on order by recurring_seq) into v_dates
      from public.transactions where recurring_id = r.id;
    assert v_dates = array['2024-02-29', '2025-02-28']::date[], format('Insurance dates %s', v_dates);
    assert r.next_due = '2026-02-28', format('Insurance next_due %s', r.next_due);

    -- Manual-only and paused bills are never auto-posted.
    assert not exists (select 1 from public.transactions t join public.recurring x on x.id = t.recurring_id
                        where x.name in ('Netflix', 'Paused')), 'manual/paused bill was auto-posted';
end $$;

-- Idempotent: calling again, or with an earlier as-of date, posts nothing.
do $$
declare n_before int;
begin
    select count(*) into n_before from public.transactions;
    assert public.post_due_recurring('2025-03-31') = 0, 'second call should post nothing';
    assert public.post_due_recurring('2025-01-15') = 0, 'earlier as-of should post nothing';
    assert (select count(*) from public.transactions) = n_before, 'transactions changed';
end $$;

-- Pause 'Old sub' so the counts below only involve Gym (Salary is next due 30 Apr).
update public.recurring set active = false where name = 'Old sub';

-- After a manual Mark paid of the next occurrence, auto-post does not post it again.
do $$
declare r public.recurring%rowtype; v_n int;
begin
    select * into r from public.recurring where name = 'Gym';          -- next: seq 13, 2 Apr
    perform public.mark_recurring_paid(r.id, '2025-04-01');             -- paid a day early
    v_n := public.post_due_recurring('2025-04-09');                     -- seq 14 (9 Apr) only
    assert v_n = 1, format('expected 1 posted after manual pay, got %s', v_n);
    assert (select count(*) from public.transactions where recurring_id = r.id and recurring_seq = 13) = 1,
        'seq 13 double-posted';
    assert (select occurred_on from public.transactions where recurring_id = r.id and recurring_seq = 13) = '2025-04-01',
        'manual payment should keep its paid-on date';
    assert (select occurred_on from public.transactions where recurring_id = r.id and recurring_seq = 14) = '2025-04-09',
        'auto-post should use the due date';
end $$;

-- An occurrence posted out of band (next_seq not advanced) is skipped, not duplicated or counted.
do $$
declare r public.recurring%rowtype; v_n int;
begin
    select * into r from public.recurring where name = 'Gym';           -- next: seq 15, 16 Apr
    insert into public.transactions (amount, type, account_id, category_id, note, recurring_id, recurring_seq, occurred_on)
        values (499.99, 'expense', r.account_id, r.category_id, 'Gym', r.id, 15, '2025-04-16');
    v_n := public.post_due_recurring('2025-04-23');                      -- seq 15 exists, seq 16 new
    assert v_n = 1, format('expected 1 newly posted, got %s', v_n);
    assert (select count(*) from public.transactions where recurring_id = r.id and recurring_seq in (15, 16)) = 2,
        'out-of-band occurrence duplicated';
    assert (select next_seq from public.recurring where id = r.id) = 17, 'next_seq not advanced past posted rows';
end $$;

do $$ begin
    perform public.post_due_recurring(null);
    raise exception 'null as-of should be rejected';
exception when sqlstate '22023' then null; end $$;

-- ============================================================================
-- RLS between two users: B cannot see, pay, skip, post or borrow A's things
-- ============================================================================
reset role;
do $$ begin
    perform set_config('test.a_rent',  (select id::text from public.recurring where name = 'Rent'), false);
    perform set_config('test.a_gym',   (select id::text from public.recurring where name = 'Gym'), false);
    perform set_config('test.a_hdfc',  (select id::text from public.accounts
                                         where user_id = '11111111-1111-1111-1111-111111111111' and name = 'HDFC'), false);
    perform set_config('test.a_bills', (select id::text from public.categories
                                         where user_id = '11111111-1111-1111-1111-111111111111' and name = 'Bills'), false);
    perform set_config('test.a_txn_count', (select count(*)::text from public.transactions
                                         where user_id = '11111111-1111-1111-1111-111111111111'), false);
    perform set_config('test.a_rent_seq', (select next_seq::text from public.recurring where name = 'Rent'), false);
end $$;
set role app_user;
set request.jwt.claim.sub = :'B';

do $$
declare n int;
begin
    select count(*) into n from public.recurring;
    assert n = 0, format('RLS leak: B sees %s of A''s bills', n);
    assert (select count(*) from public.upcoming_recurring(100000)) = 0, 'RLS leak in upcoming_recurring';
    update public.recurring set amount = 1;
    get diagnostics n = row_count;
    assert n = 0, format('RLS leak: B updated %s of A''s bills', n);
    delete from public.recurring;
    get diagnostics n = row_count;
    assert n = 0, format('RLS leak: B deleted %s of A''s bills', n);

    begin
        perform public.mark_recurring_paid(current_setting('test.a_rent')::uuid);
        raise exception 'B paid A''s bill';
    exception when sqlstate 'P0002' then null; end;
    begin
        perform public.skip_recurring(current_setting('test.a_rent')::uuid);
        raise exception 'B skipped A''s bill';
    exception when sqlstate 'P0002' then null; end;
    assert public.post_due_recurring('2100-01-01') = 0, 'B posted A''s bills';

    -- B cannot attach A's account or category to a bill of their own.
    begin
        insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
            select 'Steal', 'expense', 1, current_setting('test.a_hdfc')::uuid,
                   (select id from public.categories where name = 'Bills'), 'monthly', current_date;
        raise exception 'B attached A''s account';
    exception when sqlstate 'P0002' or sqlstate '42501' then null; end;
    begin
        insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
            select 'Steal', 'expense', 1, (select id from public.accounts where name = 'Cash'),
                   current_setting('test.a_bills')::uuid, 'monthly', current_date;
        raise exception 'B attached A''s category';
    exception when sqlstate 'P0002' or sqlstate '42501' then null; end;

    -- B's own bill works, but cannot be re-pointed at A's account later.
    insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date)
        select 'Phone', 'expense', 299, (select id from public.accounts where name = 'Cash'),
               (select id from public.categories where name = 'Bills'), 'monthly', current_date;
    begin
        update public.recurring set account_id = current_setting('test.a_hdfc')::uuid where name = 'Phone';
        raise exception 'B re-pointed a bill at A''s account';
    exception when sqlstate 'P0002' or sqlstate '42501' then null; end;

    -- B cannot tag a transaction with A's bill id to block an occurrence.
    begin
        insert into public.transactions (amount, type, account_id, category_id, recurring_id, recurring_seq)
            select 1, 'expense', (select id from public.accounts where name = 'Cash'),
                   (select id from public.categories where name = 'Bills'),
                   current_setting('test.a_gym')::uuid, 999;
        raise exception 'B linked a transaction to A''s bill';
    exception when sqlstate '42501' then null; end;
end $$;

-- A's data is untouched by everything B tried.
reset role;
do $$ begin
    assert (select count(*) from public.transactions
             where user_id = '11111111-1111-1111-1111-111111111111')::text = current_setting('test.a_txn_count'),
        'B changed A''s transactions';
    assert (select next_seq::text from public.recurring where name = 'Rent') = current_setting('test.a_rent_seq'),
        'B changed A''s bill';
    assert (select amount from public.recurring where name = 'Rent') = 15000.00, 'B changed A''s amount';
end $$;
set role app_user;

-- ============================================================================
-- upcoming_recurring (as C, relative to today)
-- ============================================================================
set request.jwt.claim.sub = :'C';
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, active)
select v.name, 'expense', v.amount, (select id from accounts where name = 'Cash'),
       (select id from categories where name = 'Bills'), 'monthly', current_date + v.offs, v.active
  from (values ('Overdue', 10.00, -2, true), ('Soon', 20.00, 3, true), ('Today', 30.00, 0, true),
               ('Later', 40.00, 40, true), ('Paused', 50.00, 1, false)) as v(name, amount, offs, active);

do $$
declare v_names text[]; v_days int[]; v_over boolean[];
begin
    select array_agg(name order by next_due, name), array_agg(days_left order by next_due, name),
           array_agg(overdue order by next_due, name)
      into v_names, v_days, v_over
      from public.upcoming_recurring(7);
    assert v_names = array['Overdue', 'Today', 'Soon'], format('upcoming(7) = %s', v_names);
    assert v_days = array[-2, 0, 3], format('days_left = %s', v_days);
    assert v_over = array[true, false, false], format('overdue = %s', v_over);
    -- Ordering comes from the function itself.
    assert (select array_agg(name) from public.upcoming_recurring(7)) = array['Overdue', 'Today', 'Soon'],
        'upcoming_recurring is not ordered by next_due';
    assert (select count(*) from public.upcoming_recurring(60)) = 4, 'upcoming(60) should include Later';
    assert (select count(*) from public.upcoming_recurring()) = 3, 'default window is 30 days';
    assert (select amount from public.upcoming_recurring(7) where name = 'Soon') = 20.00, 'amount';
end $$;
do $$ begin
    perform public.upcoming_recurring(-1);
    raise exception 'negative days should be rejected';
exception when sqlstate '22023' then null; end $$;

-- ============================================================================
-- Not signed in -> 42501
-- ============================================================================
set request.jwt.claim.sub = '';
do $$ begin
    perform public.post_due_recurring();
    raise exception 'anonymous post_due_recurring should be refused';
exception when sqlstate '42501' then null; end $$;
do $$ begin
    perform public.mark_recurring_paid(gen_random_uuid());
    raise exception 'anonymous mark_recurring_paid should be refused';
exception when sqlstate '42501' then null; end $$;
do $$ begin
    perform public.skip_recurring(gen_random_uuid());
    raise exception 'anonymous skip_recurring should be refused';
exception when sqlstate '42501' then null; end $$;
do $$ begin
    perform public.upcoming_recurring();
    raise exception 'anonymous upcoming_recurring should be refused';
exception when sqlstate '42501' then null; end $$;

-- ============================================================================
-- Fixture for the concurrent post_due_recurring race in tests/run.sh:
-- user R has one weekly auto-post bill with exactly 5 missed occurrences
-- as of 2025-01-29 (1, 8, 15, 22, 29 Jan).
-- ============================================================================
set request.jwt.claim.sub = :'R';
insert into public.recurring (name, type, amount, account_id, category_id, frequency, start_date, auto_post)
    select 'Race gym', 'expense', 333.33, (select id from accounts where name = 'Cash'),
           (select id from categories where name = 'Health'), 'weekly', '2025-01-01', true;

reset role;
\echo ''
\echo '================================'
\echo '  RECURRING TESTS PASSED'
\echo '================================'
