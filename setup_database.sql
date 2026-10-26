-- Money Tracker — complete database setup.
-- Paste this whole file into the Supabase SQL Editor and click Run, once.
-- It is the four migrations in supabase/migrations/, combined in order.


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

