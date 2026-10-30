-- Money Tracker — label imported transactions by how the money moved (Sprint 6)
--
-- The email scanner now says *how* a payment was made, as a short "channel":
--   * any UPI payment            -> 'Google Pay'
--   * any other bank transaction -> the bank, e.g. 'SBI', 'HDFC Bank'
--   * a merchant receipt         -> no channel
-- It is stored on the pending import, and when the user approves the import
-- the new transaction gets a label with that name (created if the user has
-- no such label yet, reused case-insensitively if they do). Labels from
-- Sprint 3 already power "spending by label" in Insights, so Google Pay vs
-- bank spending shows up there with no extra work.
--
-- approve_import keeps its signature, grants and errors (see 0006); the only
-- change is the labelling step at the end, inside the same transaction.

alter table public.pending_imports
    add column if not exists channel text
    check (channel is null or length(btrim(channel)) between 1 and 40);

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
    v_uid   uuid := auth.uid();
    v_imp   public.pending_imports%rowtype;
    v_kind  text;
    v_tx    uuid;
    v_label uuid;
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

    -- Label it by channel ('Google Pay', 'SBI', ...). Adds to any labels the
    -- transaction already has; never removes one.
    if v_imp.channel is not null and btrim(v_imp.channel) <> '' then
        insert into public.labels (user_id, name)
        values (v_uid, btrim(v_imp.channel))
        on conflict (user_id, lower(btrim(name))) do nothing;

        select id into strict v_label
          from public.labels
         where user_id = v_uid and lower(btrim(name)) = lower(btrim(v_imp.channel));

        insert into public.transaction_labels (user_id, transaction_id, label_id)
        values (v_uid, v_tx, v_label)
        on conflict do nothing;
    end if;

    update public.pending_imports set status = 'approved' where id = v_imp.id;
    return v_tx;
end;
$$;
