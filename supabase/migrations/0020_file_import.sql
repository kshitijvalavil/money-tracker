-- Money Tracker — bulk import from a file (Sprint 15)
--
-- A file (Excel, CSV, PDF, Word) is read in the browser and its rows are staged
-- in pending_imports with source = 'file', so they go through the same review
-- inbox as email imports: nothing counts until the user accepts it.
--
-- 1. import_batches: one row per uploaded file (name, kind, counts), so a whole
--    import can be listed and undone. Owner-only.
-- 2. pending_imports learns which batch a row came from and the category name
--    the file used (shown as a hint when it doesn't match one of the user's).
-- 3. undo_import_batch(): removes the rows of one import that are still waiting
--    for review. Rows already accepted stay (they are real transactions now and
--    can be deleted one by one), and the batch is marked undone.
--
-- Everything is row-level-secured to auth.uid(); no SECURITY DEFINER code.

-- 1. import_batches ------------------------------------------------------------
create table if not exists public.import_batches (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    file_name   text not null check (length(btrim(file_name)) between 1 and 120),
    file_kind   text not null check (file_kind in ('xlsx', 'xls', 'csv', 'pdf', 'docx', 'template', 'other')),
    row_count   int  not null default 0 check (row_count between 0 and 10000),
    added       int  not null default 0 check (added >= 0),
    skipped     int  not null default 0 check (skipped >= 0),
    used_claude boolean not null default false,
    created_at  timestamptz not null default now(),
    undone_at   timestamptz
);
create index if not exists import_batches_user_idx on public.import_batches (user_id, created_at desc);

alter table public.import_batches enable row level security;

drop policy if exists "own batches - select" on public.import_batches;
drop policy if exists "own batches - insert" on public.import_batches;
drop policy if exists "own batches - update" on public.import_batches;
drop policy if exists "own batches - delete" on public.import_batches;
create policy "own batches - select" on public.import_batches
    for select using (auth.uid() = user_id);
create policy "own batches - insert" on public.import_batches
    for insert with check (auth.uid() = user_id);
create policy "own batches - update" on public.import_batches
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own batches - delete" on public.import_batches
    for delete using (auth.uid() = user_id);

grant select, insert, update, delete on public.import_batches to authenticated;

-- 2. pending_imports -----------------------------------------------------------
alter table public.pending_imports
    add column if not exists import_batch_id uuid
        references public.import_batches (id) on delete set null,
    add column if not exists category_hint text
        check (category_hint is null or length(btrim(category_hint)) between 1 and 80);

create index if not exists pending_imports_batch_idx
    on public.pending_imports (import_batch_id) where import_batch_id is not null;

-- A row may only point at one of the owner's own batches.
create or replace function public.pending_imports_check_batch()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if new.import_batch_id is not null and not exists (
        select 1 from public.import_batches b
         where b.id = new.import_batch_id and b.user_id = new.user_id
    ) then
        raise exception 'import batch not found' using errcode = 'P0002';
    end if;
    return new;
end;
$$;

drop trigger if exists pending_imports_check_batch on public.pending_imports;
create trigger pending_imports_check_batch
    before insert or update of import_batch_id on public.pending_imports
    for each row execute function public.pending_imports_check_batch();

-- 3. undo_import_batch ---------------------------------------------------------
create or replace function public.undo_import_batch(p_batch_id uuid)
returns int
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_n   int;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    perform 1 from public.import_batches
     where id = p_batch_id and user_id = v_uid
       for update;
    if not found then
        raise exception 'import % not found', p_batch_id using errcode = 'P0002';
    end if;

    delete from public.pending_imports
     where import_batch_id = p_batch_id and user_id = v_uid and status = 'pending';
    get diagnostics v_n = row_count;

    update public.import_batches set undone_at = now()
     where id = p_batch_id and user_id = v_uid;
    return v_n;
end;
$$;

revoke execute on function public.undo_import_batch(uuid) from public;
revoke execute on function public.undo_import_batch(uuid) from anon;
grant execute on function public.undo_import_batch(uuid) to authenticated;
revoke execute on function public.pending_imports_check_batch() from public, anon;
