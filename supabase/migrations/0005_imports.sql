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
