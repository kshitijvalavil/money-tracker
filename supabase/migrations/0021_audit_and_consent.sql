-- Money Tracker — Sprint 14: security activity log + consent record.
--
-- audit_log: a per-person, append-only list of security events (signed in,
--   signed out everywhere, password changed, two-step sign-in turned on/off,
--   data downloaded, notice accepted, idle lock). A person can read and add to
--   their OWN list; nobody can edit or delete a row (no UPDATE or DELETE grant,
--   no policy for either). Rows older than 12 months are removed automatically
--   whenever that person adds a new row, so there is no scheduler to maintain.
--   The list is removed with the account (deleting an account deletes its data).
--
-- user_consents: which version of the privacy notice a person accepted, and when.
--
-- Add-only and safe to re-run. Run it in the Supabase SQL Editor.

-- ===== audit_log ============================================================
create table if not exists public.audit_log (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    event       text not null check (event in (
                    'sign_in', 'sign_out_all', 'password_changed', 'mfa_enabled',
                    'mfa_removed', 'data_exported', 'consent_accepted', 'idle_lock')),
    detail      jsonb not null default '{}'::jsonb
                check (jsonb_typeof(detail) = 'object' and pg_column_size(detail) <= 2048),
    created_at  timestamptz not null default now()
);
create index if not exists audit_log_user_time_idx on public.audit_log (user_id, created_at desc);

alter table public.audit_log enable row level security;

create policy "own audit - select" on public.audit_log
    for select using (auth.uid() = user_id);
create policy "own audit - insert" on public.audit_log
    for insert with check (auth.uid() = user_id);
-- No update or delete policy: rows can only be added, never changed or removed.

revoke all on public.audit_log from public, anon, authenticated;
grant select, insert on public.audit_log to authenticated;

-- Keep 12 months. Runs as the table owner (so it may delete), only ever touches
-- the inserting person's own old rows, and cannot be called directly.
create or replace function public.audit_log_prune()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    delete from public.audit_log
     where user_id = new.user_id
       and created_at < now() - interval '12 months';
    return new;
end;
$$;
revoke execute on function public.audit_log_prune() from public, anon, authenticated;

drop trigger if exists audit_log_prune_trg on public.audit_log;
create trigger audit_log_prune_trg
    before insert on public.audit_log
    for each row execute function public.audit_log_prune();

-- ===== user_consents ========================================================
create table if not exists public.user_consents (
    user_id         uuid primary key default auth.uid() references auth.users (id) on delete cascade,
    notice_version  text not null check (length(notice_version) between 1 and 32),
    accepted_at     timestamptz not null default now()
);

alter table public.user_consents enable row level security;

create policy "own consent - select" on public.user_consents
    for select using (auth.uid() = user_id);
create policy "own consent - insert" on public.user_consents
    for insert with check (auth.uid() = user_id);
create policy "own consent - update" on public.user_consents
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

revoke all on public.user_consents from public, anon, authenticated;
grant select, insert, update on public.user_consents to authenticated;
