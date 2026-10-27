-- Money Tracker — complete database setup.
-- Paste this whole file into the Supabase SQL Editor and click Run, once.
-- It is all migrations in supabase/migrations/, combined in order.


-- ============================================================
-- 0001_schema.sql
-- ============================================================
-- Money Tracker — schema
-- Run this in the Supabase SQL editor (or via `supabase db push`).
-- All money is stored as exact decimals (numeric), never floating point.

create extension if not exists "pgcrypto";   -- for gen_random_uuid()

-- Accounts: cash, bank, card, wallet -----------------------------------------
create table if not exists public.accounts (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name            text not null,
    type            text not null check (type in ('cash', 'bank', 'card', 'wallet')),
    opening_balance numeric(14, 2) not null default 0,
    created_at      timestamptz not null default now()
);
create index if not exists accounts_user_idx on public.accounts (user_id);

-- Categories: each belongs to a user and is income or expense ----------------
create table if not exists public.categories (
    id      uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name    text not null,
    kind    text not null check (kind in ('income', 'expense')),
    unique (user_id, name, kind)
);
create index if not exists categories_user_idx on public.categories (user_id);

-- Transactions ---------------------------------------------------------------
-- income  : money into account_id, needs a category
-- expense : money out of account_id, needs a category
-- transfer: account_id -> to_account_id, no category (not counted as spending)
create table if not exists public.transactions (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
    occurred_on    date not null default current_date,
    amount         numeric(14, 2) not null check (amount > 0),
    type           text not null check (type in ('income', 'expense', 'transfer')),
    account_id     uuid not null references public.accounts (id) on delete cascade,
    to_account_id  uuid references public.accounts (id) on delete cascade,
    category_id    uuid references public.categories (id) on delete set null,
    note           text not null default '',
    created_at     timestamptz not null default now(),

    -- a transfer needs a destination account and no category;
    -- income/expense needs a category and no destination account
    constraint transaction_shape check (
        (type = 'transfer'
            and to_account_id is not null
            and to_account_id <> account_id
            and category_id is null)
        or
        (type in ('income', 'expense')
            and to_account_id is null
            and category_id is not null)
    )
);
create index if not exists transactions_user_idx  on public.transactions (user_id);
create index if not exists transactions_month_idx on public.transactions (user_id, occurred_on);

-- Budgets: one monthly limit per expense category, repeats every month -------
create table if not exists public.budgets (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null default auth.uid() references auth.users (id) on delete cascade,
    category_id   uuid not null references public.categories (id) on delete cascade,
    monthly_limit numeric(14, 2) not null check (monthly_limit > 0),
    unique (user_id, category_id)
);
create index if not exists budgets_user_idx on public.budgets (user_id);


-- ============================================================
-- 0002_rls.sql
-- ============================================================
-- Money Tracker — Row Level Security
-- Every table is locked down so each signed-in user can only read and write
-- their own rows. The database enforces this, not just the app.

alter table public.accounts     enable row level security;
alter table public.categories   enable row level security;
alter table public.transactions enable row level security;
alter table public.budgets      enable row level security;

-- Helper: one policy set per table. "auth.uid()" is the id of the signed-in
-- user; it is null for anonymous requests, so those match nothing.

-- accounts -------------------------------------------------------------------
create policy "own accounts - select" on public.accounts
    for select using (auth.uid() = user_id);
create policy "own accounts - insert" on public.accounts
    for insert with check (auth.uid() = user_id);
create policy "own accounts - update" on public.accounts
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own accounts - delete" on public.accounts
    for delete using (auth.uid() = user_id);

-- categories -----------------------------------------------------------------
create policy "own categories - select" on public.categories
    for select using (auth.uid() = user_id);
create policy "own categories - insert" on public.categories
    for insert with check (auth.uid() = user_id);
create policy "own categories - update" on public.categories
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own categories - delete" on public.categories
    for delete using (auth.uid() = user_id);

-- transactions ---------------------------------------------------------------
create policy "own transactions - select" on public.transactions
    for select using (auth.uid() = user_id);
create policy "own transactions - insert" on public.transactions
    for insert with check (auth.uid() = user_id);
create policy "own transactions - update" on public.transactions
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own transactions - delete" on public.transactions
    for delete using (auth.uid() = user_id);

-- budgets --------------------------------------------------------------------
create policy "own budgets - select" on public.budgets
    for select using (auth.uid() = user_id);
create policy "own budgets - insert" on public.budgets
    for insert with check (auth.uid() = user_id);
create policy "own budgets - update" on public.budgets
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own budgets - delete" on public.budgets
    for delete using (auth.uid() = user_id);


-- ============================================================
-- 0003_functions.sql
-- ============================================================
-- Money Tracker — reporting functions
-- These run with the caller's permissions (security invoker), so Row Level
-- Security still applies: each function only ever sees the caller's own rows.
-- The browser calls these with supabase.rpc('name', { ... }).

-- Current balance of every account:
--   opening balance
--   + income into it            + transfers into it
--   - expense out of it         - transfers out of it
create or replace function public.account_balances()
returns table (id uuid, name text, type text, balance numeric)
language sql stable security invoker
as $$
    select a.id, a.name, a.type,
        a.opening_balance
        + coalesce((select sum(t.amount) from public.transactions t
                    where t.account_id = a.id and t.type = 'income'), 0)
        - coalesce((select sum(t.amount) from public.transactions t
                    where t.account_id = a.id and t.type = 'expense'), 0)
        - coalesce((select sum(t.amount) from public.transactions t
                    where t.account_id = a.id and t.type = 'transfer'), 0)
        + coalesce((select sum(t.amount) from public.transactions t
                    where t.to_account_id = a.id and t.type = 'transfer'), 0)
        as balance
    from public.accounts a
    order by a.name;
$$;

-- Totals for the dashboard for a given month ('YYYY-MM').
create or replace function public.dashboard_summary(p_month text)
returns table (total_balance numeric, month_income numeric, month_expense numeric)
language sql stable security invoker
as $$
    select
        coalesce((select sum(b.balance) from public.account_balances() b), 0),
        coalesce((select sum(amount) from public.transactions
                  where type = 'income'  and to_char(occurred_on, 'YYYY-MM') = p_month), 0),
        coalesce((select sum(amount) from public.transactions
                  where type = 'expense' and to_char(occurred_on, 'YYYY-MM') = p_month), 0);
$$;

-- Expense total per category for a month, biggest first.
create or replace function public.spending_by_category(p_month text)
returns table (category text, total numeric)
language sql stable security invoker
as $$
    select c.name, sum(t.amount) as total
    from public.transactions t
    join public.categories c on c.id = t.category_id
    where t.type = 'expense' and to_char(t.occurred_on, 'YYYY-MM') = p_month
    group by c.name
    order by total desc;
$$;

-- Budget vs. actual spending for a month.
create or replace function public.budget_status(p_month text)
returns table (category text, monthly_limit numeric, spent numeric)
language sql stable security invoker
as $$
    select c.name, b.monthly_limit,
        coalesce((select sum(t.amount) from public.transactions t
                  where t.category_id = b.category_id
                    and t.type = 'expense'
                    and to_char(t.occurred_on, 'YYYY-MM') = p_month), 0) as spent
    from public.budgets b
    join public.categories c on c.id = b.category_id
    order by c.name;
$$;

-- Income and expense totals for the last 12 months (oldest first).
create or replace function public.monthly_totals()
returns table (month text, income numeric, expense numeric)
language sql stable security invoker
as $$
    with months as (
        select to_char(date_trunc('month', current_date) - (n || ' months')::interval,
                       'YYYY-MM') as m
        from generate_series(0, 11) as n
    )
    select m,
        coalesce((select sum(amount) from public.transactions
                  where type = 'income'  and to_char(occurred_on, 'YYYY-MM') = m), 0),
        coalesce((select sum(amount) from public.transactions
                  where type = 'expense' and to_char(occurred_on, 'YYYY-MM') = m), 0)
    from months
    order by m;
$$;


-- ============================================================
-- 0004_seed_trigger.sql
-- ============================================================
-- Money Tracker — seed defaults for each new user
-- When someone signs up, give them a starter set of categories and a Cash
-- account so the app is usable immediately. Runs as a trigger on auth.users.
-- SECURITY DEFINER so it can write rows on the new user's behalf at signup.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.categories (user_id, name, kind) values
        (new.id, 'Salary',        'income'),
        (new.id, 'Freelance',     'income'),
        (new.id, 'Gift',          'income'),
        (new.id, 'Investment',    'income'),
        (new.id, 'Food',          'expense'),
        (new.id, 'Rent',          'expense'),
        (new.id, 'Transport',     'expense'),
        (new.id, 'Bills',         'expense'),
        (new.id, 'Shopping',      'expense'),
        (new.id, 'Health',        'expense'),
        (new.id, 'Entertainment', 'expense'),
        (new.id, 'Other',         'expense');

    insert into public.accounts (user_id, name, type, opening_balance) values
        (new.id, 'Cash', 'cash', 0);

    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();


-- ============================================================
-- 0005_imports.sql
-- ============================================================
-- Money Tracker — Gmail import support (Phase 3)
-- A staging table for transactions detected from email. The scanner writes
-- rows here; the user reviews them in the app and approves, which creates the
-- real transaction. De-duplication is by Gmail message id.

-- Tag real transactions with where they came from, and keep the source email
-- id so the same email is never imported twice.
alter table public.transactions
    add column if not exists source text not null default 'manual',
    add column if not exists source_message_id text;

-- One transaction per source email (manual rows have null id and are exempt).
create unique index if not exists transactions_source_msg_uniq
    on public.transactions (user_id, source_message_id)
    where source_message_id is not null;

-- Staging area for detected-but-not-yet-confirmed transactions.
create table if not exists public.pending_imports (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
    source            text not null,                 -- 'sbi_alert', 'statement', 'receipt', …
    direction         text not null check (direction in ('income', 'expense')),
    amount            numeric(14, 2) not null check (amount > 0),
    occurred_on       date not null,
    account_hint      text,                           -- masked account tail from the email
    note              text not null default '',
    source_message_id text,                           -- Gmail message id, for de-dup
    status            text not null default 'pending'
                      check (status in ('pending', 'approved', 'rejected')),
    created_at        timestamptz not null default now(),
    unique (user_id, source_message_id)
);
create index if not exists pending_imports_user_status_idx
    on public.pending_imports (user_id, status);

alter table public.pending_imports enable row level security;

create policy "own pending - select" on public.pending_imports
    for select using (auth.uid() = user_id);
create policy "own pending - insert" on public.pending_imports
    for insert with check (auth.uid() = user_id);
create policy "own pending - update" on public.pending_imports
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own pending - delete" on public.pending_imports
    for delete using (auth.uid() = user_id);


-- ============================================================
-- 0006_approve_import.sql
-- ============================================================
-- Money Tracker — approve / reject email imports (Sprint 1)
-- The app calls these with supabase.rpc(...). Both run as the caller
-- (security invoker), so Row Level Security still applies: a user can only
-- ever touch their own pending imports, accounts, categories and transactions.
--
--   approve_import(p_import_id, p_account_id, p_category_id) -> transaction id
--       In one transaction: locks the caller's pending import, checks the
--       account and category are the caller's and the category kind matches
--       the import direction, creates the real transaction, marks the import
--       'approved'. If a transaction for the same source email already exists,
--       the import is marked 'approved' and that existing id is returned.
--   reject_import(p_import_id) -> void
--       Marks the caller's pending import 'rejected'.
--
-- Errors (SQLSTATE, so the app can map them to messages):
--   P0002  import / account / category not found (or not the caller's)
--   55000  import is not pending (already approved or rejected)
--   22023  category kind does not match the import direction
--   42501  not signed in

create or replace function public.approve_import(
    p_import_id   uuid,
    p_account_id  uuid,
    p_category_id uuid
) returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid  uuid := auth.uid();
    v_imp  public.pending_imports%rowtype;
    v_kind text;
    v_tx   uuid;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;

    -- Lock the import row so two concurrent approvals cannot both proceed.
    select * into v_imp
      from public.pending_imports
     where id = p_import_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'import % not found', p_import_id using errcode = 'P0002';
    end if;
    if v_imp.status <> 'pending' then
        raise exception 'import % is already %', p_import_id, v_imp.status
            using errcode = '55000';
    end if;

    perform 1 from public.accounts where id = p_account_id and user_id = v_uid;
    if not found then
        raise exception 'account % not found', p_account_id using errcode = 'P0002';
    end if;

    select kind into v_kind
      from public.categories
     where id = p_category_id and user_id = v_uid;
    if not found then
        raise exception 'category % not found', p_category_id using errcode = 'P0002';
    end if;
    if v_kind <> v_imp.direction then
        raise exception 'category kind % does not match import direction %',
            v_kind, v_imp.direction using errcode = '22023';
    end if;

    -- Already imported this email? Reuse that transaction instead of failing.
    if v_imp.source_message_id is not null then
        select id into v_tx
          from public.transactions
         where user_id = v_uid and source_message_id = v_imp.source_message_id;
    end if;

    if v_tx is null then
        insert into public.transactions
            (user_id, type, amount, occurred_on, account_id, category_id,
             note, source, source_message_id)
        values
            (v_uid, v_imp.direction, v_imp.amount, v_imp.occurred_on,
             p_account_id, p_category_id, v_imp.note, v_imp.source,
             v_imp.source_message_id)
        on conflict (user_id, source_message_id)
            where source_message_id is not null
            do nothing
        returning id into v_tx;

        -- Lost a race with another insert of the same email: use that row.
        if v_tx is null then
            select id into strict v_tx
              from public.transactions
             where user_id = v_uid and source_message_id = v_imp.source_message_id;
        end if;
    end if;

    update public.pending_imports set status = 'approved' where id = v_imp.id;
    return v_tx;
end;
$$;

create or replace function public.reject_import(p_import_id uuid)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid    uuid := auth.uid();
    v_status text;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;

    select status into v_status
      from public.pending_imports
     where id = p_import_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'import % not found', p_import_id using errcode = 'P0002';
    end if;
    if v_status <> 'pending' then
        raise exception 'import % is already %', p_import_id, v_status
            using errcode = '55000';
    end if;

    update public.pending_imports set status = 'rejected' where id = p_import_id;
end;
$$;

revoke execute on function public.approve_import(uuid, uuid, uuid) from public;
revoke execute on function public.reject_import(uuid) from public;
revoke execute on function public.approve_import(uuid, uuid, uuid) from anon;
revoke execute on function public.reject_import(uuid) from anon;
grant execute on function public.approve_import(uuid, uuid, uuid) to authenticated;
grant execute on function public.reject_import(uuid) to authenticated;


-- ============================================================
-- 0007_recurring.sql
-- ============================================================
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


-- ============================================================
-- 0008_suggest_recurring.sql
-- ============================================================
-- Money Tracker — subscription / recurring-bill detection (Sprint 2)
-- Depends on 0007_recurring.sql (public.recurring, used to skip groups that
-- are already bills).
--
--   suggest_recurring(p_min_occurrences int default 3)
--     -> table(name, type, amount, frequency, account_id, category_id,
--              last_seen, next_guess, occurrences, confidence)
--
-- Read-only and security invoker: it only ever sees the caller's own rows.
-- The reference implementation is bot/recurring.py (detect + occurrence).
-- Both must agree, and tests/suggest_recurring_test.sql checks that on the
-- shared fixture rows. The rules, in order:
--   1. Income/expense transactions only. Group by (normalised note, type).
--      To normalise a note: lowercase it, turn every non-letter,
--      non-whitespace character into a space, collapse runs of whitespace
--      and trim. Empty keys are ignored. Groups whose key equals the
--      normalised name of one of the caller's recurring rows are excluded.
--   2. Groups with fewer than p_min_occurrences rows are dropped.
--   3. Keep the rows with |amount - median| <= 0.10 x median, where median
--      is the group's median amount.
--   4. occurrences = number of distinct dates among the kept rows, and it
--      must be >= p_min_occurrences. The kept rows must also be at least half
--      of the group's rows (2 x kept >= group), so a chance cluster of
--      similar amounts inside random spend at one merchant does not count
--      (decision 13).
--   5. Median gap between consecutive distinct dates, in inclusive bands:
--      6-8 weekly, 26-35 monthly, 85-95 quarterly, 355-375 yearly. Any other
--      median gap: not recurring. At least 2/3 of the gaps must lie inside
--      that band (3 x in_band >= 2 x gaps; decision 13).
--   6. name, account_id and category_id come from the latest kept row, by
--      (occurred_on, id). amount is round(median of the kept amounts, 2).
--      next_guess is the first anchored occurrence after last_seen, anchored
--      to the first kept date (same month-end clamping as recurring_occurrence).
--      confidence = round(gaps in band / all gaps x kept rows / group rows, 2).
--      Sorted by confidence desc, occurrences desc, key and type in C order.
--
-- Errors: 42501 not signed in, 22023 p_min_occurrences < 2.

create or replace function public.suggest_recurring(p_min_occurrences int default 3)
returns table (
    name        text,
    type        text,
    amount      numeric,
    frequency   text,
    account_id  uuid,
    category_id uuid,
    last_seen   date,
    next_guess  date,
    occurrences int,
    confidence  numeric
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_uid    uuid := auth.uid();
    g        record;
    v_n      int;
    v_med    numeric;
    v_kept   numeric[];
    v_dates  date[];
    v_gaps   int[];
    v_gmed   numeric;
    v_freq   text;
    v_lo     int;
    v_hi     int;
    v_months int;
    v_inband int;
    v_k      int;
    v_next   date;
    v_latest record;
    -- result columns, collected and sorted at the end
    r_key    text[] := '{}';
    r_name   text[] := '{}';
    r_type   text[] := '{}';
    r_amount numeric[] := '{}';
    r_freq   text[] := '{}';
    r_acct   uuid[] := '{}';
    r_cat    uuid[] := '{}';
    r_last   date[] := '{}';
    r_next   date[] := '{}';
    r_occ    int[] := '{}';
    r_conf   numeric[] := '{}';
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_min_occurrences is null or p_min_occurrences < 2 then
        raise exception 'p_min_occurrences must be at least 2' using errcode = '22023';
    end if;

    for g in
        with t as (
            select tx.id, tx.amount, tx.type,
                   btrim(regexp_replace(
                       regexp_replace(lower(tx.note), '[^[:alpha:][:space:]]', ' ', 'g'),
                       '[[:space:]]+', ' ', 'g')) as k
              from public.transactions tx
             where tx.user_id = v_uid
               and tx.type in ('income', 'expense')
        ), known as (
            select btrim(regexp_replace(
                       regexp_replace(lower(r.name), '[^[:alpha:][:space:]]', ' ', 'g'),
                       '[[:space:]]+', ' ', 'g')) as k
              from public.recurring r
             where r.user_id = v_uid
        )
        select t.k, t.type as typ,
               array_agg(t.amount order by t.amount) as amts,
               array_agg(t.id) as ids,
               count(*)::int as cnt
          from t
         where t.k <> ''
           and not exists (select 1 from known where known.k = t.k)
         group by t.k, t.type
        having count(*) >= p_min_occurrences
    loop
        -- 3. amount filter around the group median
        v_n := g.cnt;
        v_med := (g.amts[(v_n + 1) / 2] + g.amts[v_n / 2 + 1]) / 2;

        select array_agg(tx.amount order by tx.amount),
               array_agg(distinct tx.occurred_on order by tx.occurred_on)
          into v_kept, v_dates
          from public.transactions tx
         where tx.id = any (g.ids)
           and abs(tx.amount - v_med) <= v_med * 0.10;

        -- 4. enough distinct dates, and the kept rows are at least half the group?
        if coalesce(array_length(v_dates, 1), 0) < p_min_occurrences
           or 2 * coalesce(array_length(v_kept, 1), 0) < g.cnt then
            continue;
        end if;

        -- 5. median gap -> frequency band
        select array_agg(d.gap order by d.gap) into v_gaps
          from (select v_dates[i] - v_dates[i - 1] as gap
                  from generate_series(2, array_length(v_dates, 1)) as i) d;
        v_n := array_length(v_gaps, 1);
        v_gmed := (v_gaps[(v_n + 1) / 2] + v_gaps[v_n / 2 + 1]) / 2.0;

        v_freq := case
            when v_gmed between 6   and 8   then 'weekly'
            when v_gmed between 26  and 35  then 'monthly'
            when v_gmed between 85  and 95  then 'quarterly'
            when v_gmed between 355 and 375 then 'yearly'
        end;
        if v_freq is null then
            continue;
        end if;
        v_lo := case v_freq when 'weekly' then 6 when 'monthly' then 26
                            when 'quarterly' then 85 else 355 end;
        v_hi := case v_freq when 'weekly' then 8 when 'monthly' then 35
                            when 'quarterly' then 95 else 375 end;
        select count(*)::int into v_inband from unnest(v_gaps) x where x between v_lo and v_hi;
        if 3 * v_inband < 2 * v_n then          -- need >= 2/3 of gaps in the band
            continue;
        end if;

        -- 6. latest kept row, next anchored occurrence after last_seen
        select tx.note, tx.account_id, tx.category_id into v_latest
          from public.transactions tx
         where tx.id = any (g.ids)
           and abs(tx.amount - v_med) <= v_med * 0.10
         order by tx.occurred_on desc, tx.id::text collate "C" desc
         limit 1;

        v_months := case v_freq when 'monthly' then 1 when 'quarterly' then 3
                                when 'yearly' then 12 else 0 end;
        v_k := 1;
        loop
            v_next := case when v_freq = 'weekly'
                           then v_dates[1] + 7 * v_k
                           else (v_dates[1] + make_interval(months => v_months * v_k))::date
                      end;
            exit when v_next > v_dates[array_length(v_dates, 1)];
            v_k := v_k + 1;
        end loop;

        r_key    := r_key    || g.k;
        r_name   := r_name   || left(btrim(v_latest.note), 80);
        r_type   := r_type   || g.typ;
        r_amount := r_amount || round((v_kept[(array_length(v_kept, 1) + 1) / 2]
                                       + v_kept[array_length(v_kept, 1) / 2 + 1]) / 2, 2);
        r_freq   := r_freq   || v_freq;
        r_acct   := r_acct   || v_latest.account_id;
        r_cat    := r_cat    || v_latest.category_id;
        r_last   := r_last   || v_dates[array_length(v_dates, 1)];
        r_next   := r_next   || v_next;
        r_occ    := r_occ    || array_length(v_dates, 1);
        r_conf   := r_conf   || round((v_inband * array_length(v_kept, 1))::numeric
                                      / (v_n * g.cnt), 2);
    end loop;

    return query
        select u.n, u.t, u.a, u.f, u.ac, u.c, u.l, u.nx, u.o, u.cf
          from unnest(r_key, r_name, r_type, r_amount, r_freq, r_acct, r_cat,
                      r_last, r_next, r_occ, r_conf)
               as u(k, n, t, a, f, ac, c, l, nx, o, cf)
         order by u.cf desc, u.o desc, u.k collate "C", u.t collate "C";
end;
$$;

revoke execute on function public.suggest_recurring(int) from public;
revoke execute on function public.suggest_recurring(int) from anon;
grant execute on function public.suggest_recurring(int) to authenticated;
