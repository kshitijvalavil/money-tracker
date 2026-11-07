-- Money Tracker — stop "Database error querying schema" for good.
--
-- 0018 repaired the users that existed at the time. Users added later with
-- plain SQL get NULL in auth.users' token columns again, and Supabase's
-- sign-in service (GoTrue) then fails with HTTP 500. This trigger turns those
-- NULLs into '' (what GoTrue itself writes) on every new user, so it can no
-- longer happen no matter how the user is created.
--
-- Run in the Supabase SQL Editor (needs the auth schema, so it is not part of
-- the local test chain, which mocks auth.users). Safe to re-run.

create or replace function public.fill_auth_token_defaults()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.confirmation_token         := coalesce(new.confirmation_token, '');
    new.recovery_token             := coalesce(new.recovery_token, '');
    new.email_change_token_new     := coalesce(new.email_change_token_new, '');
    new.email_change               := coalesce(new.email_change, '');
    new.email_change_token_current := coalesce(new.email_change_token_current, '');
    new.phone_change               := coalesce(new.phone_change, '');
    new.phone_change_token         := coalesce(new.phone_change_token, '');
    new.reauthentication_token     := coalesce(new.reauthentication_token, '');
    return new;
end;
$$;

drop trigger if exists auth_users_fill_token_defaults on auth.users;
create trigger auth_users_fill_token_defaults
    before insert on auth.users
    for each row execute function public.fill_auth_token_defaults();
