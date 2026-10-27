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
