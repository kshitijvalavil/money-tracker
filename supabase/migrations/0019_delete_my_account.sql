-- Money Tracker — Sprint 14: delete my account.
--
-- Lets a signed-in person permanently delete their own login and ALL of their
-- data in one step. Every user-data table references auth.users(id) with
-- ON DELETE CASCADE (see 0001..0017), so removing the auth user removes every
-- row they own, across all 15 tables, and nothing else.
--
-- SECURITY DEFINER because ordinary users cannot touch the auth schema. It is
-- safe because it only ever deletes the caller's own auth row (auth.uid()),
-- takes no arguments, and refuses callers who are not signed in. search_path is
-- pinned so a caller cannot redirect what it reads.
--
-- Run it in the Supabase SQL Editor (it needs the real auth schema).

create or replace function public.delete_my_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    delete from auth.users where id = v_uid;
end;
$$;

revoke execute on function public.delete_my_account() from public, anon;
grant  execute on function public.delete_my_account() to authenticated;
