-- Money Tracker — fix "Database error querying schema" on sign-in.
--
-- Supabase's sign-in service (GoTrue) reads a few text columns of auth.users
-- (confirmation_token, recovery_token, email_change, email_change_token_new...)
-- and expects text, never NULL. Users created from the Dashboard / sign-up API
-- get '' in those columns; users inserted with plain SQL get NULL, and then
-- every sign-in for that user fails with HTTP 500
-- "Database error querying schema".
--
-- This turns those NULLs into '' (what GoTrue itself writes). It only touches
-- rows that actually have a NULL, changes nothing else, and is safe to re-run.
-- Run it in the Supabase SQL Editor (it needs the auth schema, so it is not
-- part of the local test chain, which mocks auth.users).

update auth.users
set confirmation_token         = coalesce(confirmation_token, ''),
    recovery_token             = coalesce(recovery_token, ''),
    email_change_token_new     = coalesce(email_change_token_new, ''),
    email_change               = coalesce(email_change, ''),
    email_change_token_current = coalesce(email_change_token_current, ''),
    phone_change               = coalesce(phone_change, ''),
    phone_change_token         = coalesce(phone_change_token, ''),
    reauthentication_token     = coalesce(reauthentication_token, '')
where confirmation_token is null
   or recovery_token is null
   or email_change_token_new is null
   or email_change is null
   or email_change_token_current is null
   or phone_change is null
   or phone_change_token is null
   or reauthentication_token is null;
