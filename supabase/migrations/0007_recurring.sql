-- Money Tracker — recurring bills and subscriptions (Sprint 2)
-- A bill is a schedule anchored to start_date: occurrence n is
-- start_date + n × period, with the day clamped to the month's length
-- (31 Jan -> 29 Feb -> 31 Mar, never drifting to the 28th/29th).
-- next_seq is the index of the next unpaid occurrence and next_due is its date;
-- a trigger keeps next_due = recurring_occurrence(start_date, frequency, next_seq).
--
-- The app calls these with supabase.rpc(...). All run as the caller
-- (security invoker), so Row Level Security applies.
--
--   recurring_occurrence(p_start, p_freq, p_n)      -> date   (pure)
--   mark_recurring_paid(p_id, p_paid_on)            -> transaction id
--   skip_recurring(p_id)                            -> void
--   reschedule_recurring(p_id, p_start, p_frequency) -> void (keeps history linked)
--   post_due_recurring(p_as_of)                     -> number of transactions posted
--   upcoming_recurring(p_days)                      -> due-soon and overdue bills
--
-- Posting is idempotent: transactions carry (recurring_id, recurring_seq),
-- unique per bill, so the same occurrence can never be posted twice, even by
-- concurrent calls or a manual "Mark paid" racing an auto-post.
--
-- Errors (SQLSTATE, so the app can map them to messages):
--   P0002  bill / account / category not found (or not the caller's)
--   22023  bad input (category kind <> type, bad frequency, null/negative args)
--   42501  not signed in

-- ---- Date math ---------------------------------------------------------------
create or replace function public.recurring_occurrence(p_start date, p_freq text, p_n int)
returns date
language plpgsql
immutable
security invoker
set search_path = ''
as $$
declare
    v_months int;
begin
    if p_start is null or p_freq is null or p_n is null then
        raise exception 'start date, frequency and occurrence number are required'
            using errcode = '22023';
    end if;
    if p_n < 0 then
        raise exception 'occurrence number must be >= 0, got %', p_n using errcode = '22023';
    end if;

    if p_freq = 'weekly' then
        return p_start + 7 * p_n;
    end if;

    v_months := case p_freq
                    when 'monthly'   then 1
                    when 'quarterly' then 3
                    when 'yearly'    then 12
                end;
    if v_months is null then
        raise exception 'unknown frequency %', p_freq using errcode = '22023';
    end if;
    -- Adding months to a date clamps the day to the target month's length,
    -- and because we always add from start_date there is no drift.
    return (p_start + make_interval(months => v_months * p_n))::date;
end;
$$;

-- ---- Table ---------------------------------------------------------------------
create table if not exists public.recurring (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name         text not null check (length(btrim(name)) between 1 and 80),
    type         text not null check (type in ('income', 'expense')),
    amount       numeric(14, 2) not null check (amount > 0),
    account_id   uuid not null references public.accounts (id) on delete cascade,
    category_id  uuid not null references public.categories (id) on delete restrict,
    frequency    text not null check (frequency in ('weekly', 'monthly', 'quarterly', 'yearly')),
    start_date   date not null,
    next_seq     int  not null default 0 check (next_seq >= 0),
    next_due     date not null,
    auto_post    boolean not null default false,
    active       boolean not null default true,
    source       text not null default 'manual' check (source in ('manual', 'detected')),
    created_at   timestamptz not null default now()
);
create index if not exists recurring_user_due_idx on public.recurring (user_id, next_due);

-- Keep next_due in sync and check the account/category belong to the bill's
-- owner and the category kind matches the bill type. Runs before RLS checks,
-- so a bad reference gets a clear error instead of a generic policy violation.
create or replace function public.recurring_before_write()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_kind text;
begin
    perform 1 from public.accounts
     where id = new.account_id and user_id = new.user_id;
    if not found then
        raise exception 'account % not found', new.account_id using errcode = 'P0002';
    end if;

    select kind into v_kind from public.categories
     where id = new.category_id and user_id = new.user_id;
    if not found then
        raise exception 'category % not found', new.category_id using errcode = 'P0002';
    end if;
    if v_kind <> new.type then
        raise exception 'category kind % does not match bill type %', v_kind, new.type
            using errcode = '22023';
    end if;

    new.next_due := public.recurring_occurrence(new.start_date, new.frequency, new.next_seq);
    return new;
end;
$$;

drop trigger if exists recurring_before_write on public.recurring;
create trigger recurring_before_write
    before insert or update on public.recurring
    for each row execute function public.recurring_before_write();

alter table public.recurring enable row level security;

create policy "own recurring - select" on public.recurring
    for select using (auth.uid() = user_id);
create policy "own recurring - insert" on public.recurring
    for insert with check (
        auth.uid() = user_id
        and exists (select 1 from public.accounts a
                     where a.id = account_id and a.user_id = auth.uid())
        and exists (select 1 from public.categories c
                     where c.id = category_id and c.user_id = auth.uid()));
create policy "own recurring - update" on public.recurring
    for update using (auth.uid() = user_id)
    with check (
        auth.uid() = user_id
        and exists (select 1 from public.accounts a
                     where a.id = account_id and a.user_id = auth.uid())
        and exists (select 1 from public.categories c
                     where c.id = category_id and c.user_id = auth.uid()));
create policy "own recurring - delete" on public.recurring
    for delete using (auth.uid() = user_id);

-- ---- Link transactions to the occurrence they paid -----------------------------
alter table public.transactions
    add column if not exists recurring_id uuid references public.recurring (id) on delete set null,
    add column if not exists recurring_seq int;
create unique index if not exists transactions_recurring_seq_uniq
    on public.transactions (recurring_id, recurring_seq) where recurring_id is not null;

-- Foreign keys ignore RLS, so without this a user could tag their own
-- transaction with someone else's bill id and block that occurrence.
create policy "own recurring link" on public.transactions
    as restrictive for insert
    with check (recurring_id is null
                or exists (select 1 from public.recurring r
                            where r.id = recurring_id and r.user_id = auth.uid()));
create policy "own recurring link - update" on public.transactions
    as restrictive for update
    using (true)
    with check (recurring_id is null
                or exists (select 1 from public.recurring r
                            where r.id = recurring_id and r.user_id = auth.uid()));

-- ---- Functions -----------------------------------------------------------------

create or replace function public.mark_recurring_paid(p_id uuid, p_paid_on date default current_date)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_r   public.recurring%rowtype;
    v_tx  uuid;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_paid_on is null then
        raise exception 'paid-on date is required' using errcode = '22023';
    end if;

    select * into v_r from public.recurring
     where id = p_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'bill % not found', p_id using errcode = 'P0002';
    end if;

    -- Post occurrence next_seq; if it is already posted, reuse that row.
    insert into public.transactions
        (user_id, type, amount, occurred_on, account_id, category_id,
         note, source, recurring_id, recurring_seq)
    values
        (v_uid, v_r.type, v_r.amount, p_paid_on, v_r.account_id, v_r.category_id,
         v_r.name, 'recurring', v_r.id, v_r.next_seq)
    on conflict (recurring_id, recurring_seq) where recurring_id is not null
        do nothing
    returning id into v_tx;
    if v_tx is null then
        select t.id into strict v_tx
          from public.transactions t
         where t.recurring_id = v_r.id and t.recurring_seq = v_r.next_seq;
    end if;

    update public.recurring
       set next_seq = v_r.next_seq + 1,
           next_due = public.recurring_occurrence(v_r.start_date, v_r.frequency, v_r.next_seq + 1)
     where id = v_r.id;
    return v_tx;
end;
$$;

create or replace function public.skip_recurring(p_id uuid)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_r   public.recurring%rowtype;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;

    select * into v_r from public.recurring
     where id = p_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'bill % not found', p_id using errcode = 'P0002';
    end if;

    update public.recurring
       set next_seq = v_r.next_seq + 1,
           next_due = public.recurring_occurrence(v_r.start_date, v_r.frequency, v_r.next_seq + 1)
     where id = v_r.id;
end;
$$;

-- Change a bill's schedule (start date and/or frequency) without breaking
-- its history. Already-posted transactions keep their link to the bill but are
-- renumbered to negative seqs, so the new schedule can start again at seq 0
-- without colliding with them. Normally that is seq - next_seq; if a row was
-- ever posted at or past next_seq the shift is widened so every old row still
-- ends up negative. Calling it again is safe: old rows just move further down.
create or replace function public.reschedule_recurring(p_id uuid, p_start date, p_frequency text)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid   uuid := auth.uid();
    v_r     public.recurring%rowtype;
    v_shift int;
    v_tx    record;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_start is null then
        raise exception 'start date is required' using errcode = '22023';
    end if;
    if p_frequency is null or p_frequency not in ('weekly', 'monthly', 'quarterly', 'yearly') then
        raise exception 'unknown frequency %', p_frequency using errcode = '22023';
    end if;

    -- Lock the bill: no Mark paid / auto-post can run until we commit.
    select * into v_r from public.recurring
     where id = p_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'bill % not found', p_id using errcode = 'P0002';
    end if;

    select greatest(v_r.next_seq, coalesce(max(t.recurring_seq) + 1, 0)) into v_shift
      from public.transactions t
     where t.recurring_id = v_r.id;

    -- Renumber lowest seq first: each row moves below every row still to move,
    -- so the (non-deferrable) unique index never sees a transient duplicate.
    if v_shift > 0 then
        for v_tx in
            select t.id from public.transactions t
             where t.recurring_id = v_r.id
             order by t.recurring_seq
               for update
        loop
            update public.transactions
               set recurring_seq = recurring_seq - v_shift
             where id = v_tx.id;
        end loop;
    end if;

    update public.recurring
       set start_date = p_start,
           frequency  = p_frequency,
           next_seq   = 0,
           next_due   = public.recurring_occurrence(p_start, p_frequency, 0)
     where id = v_r.id;
end;
$$;

-- Post every missed occurrence of the caller's active auto-post bills, up to
-- p_as_of, at most 60 per bill per call (the next call continues). Rows are
-- locked, so a concurrent call waits, then re-checks next_due and skips bills
-- the first call already brought up to date.
create or replace function public.post_due_recurring(p_as_of date default current_date)
returns int
language plpgsql
security invoker
set search_path = ''
as $$
declare
    c_cap    constant int := 60;
    v_uid    uuid := auth.uid();
    v_r      public.recurring%rowtype;
    v_seq    int;
    v_due    date;
    v_n      int;
    v_tx     uuid;
    v_posted int := 0;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_as_of is null then
        raise exception 'as-of date is required' using errcode = '22023';
    end if;

    for v_r in
        select * from public.recurring
         where user_id = v_uid and active and auto_post and next_due <= p_as_of
         order by next_due, id
           for update
    loop
        v_seq := v_r.next_seq;
        v_due := v_r.next_due;
        v_n   := 0;
        while v_due <= p_as_of and v_n < c_cap loop
            -- Already-posted occurrences (e.g. a racing Mark paid) are skipped.
            insert into public.transactions
                (user_id, type, amount, occurred_on, account_id, category_id,
                 note, source, recurring_id, recurring_seq)
            values
                (v_uid, v_r.type, v_r.amount, v_due, v_r.account_id, v_r.category_id,
                 v_r.name, 'recurring', v_r.id, v_seq)
            on conflict (recurring_id, recurring_seq) where recurring_id is not null
                do nothing
            returning id into v_tx;
            if v_tx is not null then
                v_posted := v_posted + 1;
            end if;
            v_seq := v_seq + 1;
            v_n   := v_n + 1;
            v_due := public.recurring_occurrence(v_r.start_date, v_r.frequency, v_seq);
        end loop;

        update public.recurring set next_seq = v_seq, next_due = v_due where id = v_r.id;
    end loop;

    return v_posted;
end;
$$;

create or replace function public.upcoming_recurring(p_days int default 30)
returns table (id uuid, name text, type text, amount numeric, next_due date,
               days_left int, overdue boolean, auto_post boolean)
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
    if auth.uid() is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_days is null or p_days < 0 then
        raise exception 'days must be >= 0' using errcode = '22023';
    end if;

    return query
        select r.id, r.name, r.type, r.amount, r.next_due,
               (r.next_due - current_date)::int,
               r.next_due < current_date,
               r.auto_post
          from public.recurring r
         where r.user_id = auth.uid()
           and r.active
           and r.next_due <= current_date + p_days
         order by r.next_due, r.name;
end;
$$;

-- ---- Grants (same as 0006) -----------------------------------------------------
revoke execute on function public.recurring_occurrence(date, text, int) from public, anon;
revoke execute on function public.recurring_before_write() from public, anon;
revoke execute on function public.mark_recurring_paid(uuid, date) from public, anon;
revoke execute on function public.skip_recurring(uuid) from public, anon;
revoke execute on function public.reschedule_recurring(uuid, date, text) from public, anon;
revoke execute on function public.post_due_recurring(date) from public, anon;
revoke execute on function public.upcoming_recurring(int) from public, anon;
grant execute on function public.recurring_occurrence(date, text, int) to authenticated;
grant execute on function public.mark_recurring_paid(uuid, date) to authenticated;
grant execute on function public.skip_recurring(uuid) to authenticated;
grant execute on function public.reschedule_recurring(uuid, date, text) to authenticated;
grant execute on function public.post_due_recurring(date) to authenticated;
grant execute on function public.upcoming_recurring(int) to authenticated;
