-- Money Tracker — daily review inbox (Sprint 9)
--
-- 1. pending_imports learns the shop name, a suggested category (from the
--    user's rules), and which other emails were merged into it (one Swiggy
--    order can arrive as a Swiggy receipt, a Google Pay email and a bank alert).
-- 2. category_rules: "when the shop / UPI id / note contains X, use category Y".
--    Owner-only, and a rule may only point at the owner's own category.
-- 3. sync_status: when the last email sync ran and what it found (one row per user).
-- 4. undo_approve(): reverses an accept within the app's undo window by
--    deleting the transaction that accept created and re-opening the import.
--
-- Everything is row-level-secured to auth.uid(); no new SECURITY DEFINER code.

-- 1. pending_imports -----------------------------------------------------------
alter table public.pending_imports
    add column if not exists merchant text
        check (merchant is null or length(btrim(merchant)) between 1 and 80),
    add column if not exists suggested_category_id uuid
        references public.categories (id) on delete set null,
    add column if not exists merged_message_ids text[] not null default '{}',
    add column if not exists merged_sources text[] not null default '{}'
        check (cardinality(merged_sources) <= 5);

-- A suggestion may only name one of the owner's own categories.
create or replace function public.pending_imports_check_suggestion()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if new.suggested_category_id is not null and not exists (
        select 1 from public.categories c
         where c.id = new.suggested_category_id and c.user_id = new.user_id
    ) then
        raise exception 'suggested category not found' using errcode = 'P0002';
    end if;
    return new;
end;
$$;

drop trigger if exists pending_imports_check_suggestion on public.pending_imports;
create trigger pending_imports_check_suggestion
    before insert or update of suggested_category_id on public.pending_imports
    for each row execute function public.pending_imports_check_suggestion();

-- 2. category_rules ------------------------------------------------------------
create table if not exists public.category_rules (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    match_text  text not null check (length(btrim(match_text)) between 2 and 60),
    category_id uuid not null references public.categories (id) on delete cascade,
    label       text check (label is null or length(btrim(label)) between 1 and 40),
    hits        int  not null default 0 check (hits >= 0),
    created_at  timestamptz not null default now()
);
create unique index if not exists category_rules_user_match_uniq
    on public.category_rules (user_id, lower(btrim(match_text)));

alter table public.category_rules enable row level security;

create policy "own rules - select" on public.category_rules
    for select using (auth.uid() = user_id);
create policy "own rules - insert" on public.category_rules
    for insert with check (
        auth.uid() = user_id
        and exists (select 1 from public.categories c
                     where c.id = category_id and c.user_id = auth.uid()));
create policy "own rules - update" on public.category_rules
    for update using (auth.uid() = user_id)
    with check (
        auth.uid() = user_id
        and exists (select 1 from public.categories c
                     where c.id = category_id and c.user_id = auth.uid()));
create policy "own rules - delete" on public.category_rules
    for delete using (auth.uid() = user_id);

-- 3. sync_status ---------------------------------------------------------------
create table if not exists public.sync_status (
    user_id        uuid primary key default auth.uid() references auth.users (id) on delete cascade,
    last_synced_at timestamptz,
    last_found     int not null default 0 check (last_found >= 0),
    last_added     int not null default 0 check (last_added >= 0)
);

alter table public.sync_status enable row level security;

create policy "own sync - select" on public.sync_status
    for select using (auth.uid() = user_id);
create policy "own sync - insert" on public.sync_status
    for insert with check (auth.uid() = user_id);
create policy "own sync - update" on public.sync_status
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 4. undo_approve --------------------------------------------------------------
-- Deletes the transaction an accept created (matched by the import's email id)
-- and puts the import back to 'pending'. Only for imports approved in the last
-- 10 minutes, so it can't be used to rewrite old history.
create or replace function public.undo_approve(p_import_id uuid)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_imp public.pending_imports%rowtype;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;

    select * into v_imp
      from public.pending_imports
     where id = p_import_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'import % not found', p_import_id using errcode = 'P0002';
    end if;
    if v_imp.status <> 'approved' then
        raise exception 'import % is %, nothing to undo', p_import_id, v_imp.status
            using errcode = '55000';
    end if;
    if v_imp.source_message_id is null then
        raise exception 'import % has no source email', p_import_id using errcode = '55000';
    end if;

    delete from public.transactions
     where user_id = v_uid
       and source_message_id = v_imp.source_message_id
       and created_at > now() - interval '10 minutes';
    if not found then
        raise exception 'too late to undo' using errcode = '55000';
    end if;

    update public.pending_imports set status = 'pending'
     where id = p_import_id and user_id = v_uid;
end;
$$;

revoke all on function public.undo_approve(uuid) from public, anon;
grant execute on function public.undo_approve(uuid) to authenticated;
