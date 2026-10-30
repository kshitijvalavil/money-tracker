-- Money Tracker — investments (Sprint 6)
-- A simple, manual portfolio: one row per holding (a stock, a mutual fund,
-- a fixed deposit, gold, PPF/EPF, crypto, ...), with what the user put in and
-- what it is worth now. No live price feed: the user updates "current value"
-- when they check their broker or passbook, which keeps it free and private.
--
-- Per-user and locked down by Row Level Security, same as every other table.
-- Gain / loss and allocation are worked out in the browser from these rows.

create table if not exists public.investments (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name            text not null check (length(btrim(name)) between 1 and 80),
    kind            text not null default 'other'
                    check (kind in ('stocks', 'mutual_fund', 'fixed_deposit', 'gold',
                                    'retirement', 'crypto', 'other')),
    invested_amount numeric(14, 2) not null check (invested_amount >= 0),
    current_value   numeric(14, 2) not null check (current_value >= 0),
    as_of           date not null default current_date,
    note            text not null default '' check (length(note) <= 200),
    created_at      timestamptz not null default now()
);
create index if not exists investments_user_idx on public.investments (user_id);

alter table public.investments enable row level security;

create policy "own investments - select" on public.investments
    for select using (auth.uid() = user_id);
create policy "own investments - insert" on public.investments
    for insert with check (auth.uid() = user_id);
create policy "own investments - update" on public.investments
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own investments - delete" on public.investments
    for delete using (auth.uid() = user_id);

-- The browser connects as "authenticated"; anon gets nothing.
revoke all on table public.investments from anon;
grant select, insert, update, delete on table public.investments to authenticated;
