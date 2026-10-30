-- Money Tracker — security hardening from the Supabase security advisor (Sprint 6)
--
-- 1. Pin search_path on the original reporting functions (0003). They already
--    name every table as public.*, so an empty search_path changes nothing for
--    them but stops a caller's search_path from redirecting what they read.
-- 2. handle_new_user() is the sign-up trigger that seeds a new user's starter
--    categories and Cash account. It runs as SECURITY DEFINER, so nobody should
--    be able to call it directly through the API. Triggers still fire: Postgres
--    checks EXECUTE on a trigger function only when the trigger is created.

alter function public.account_balances()           set search_path = '';
alter function public.dashboard_summary(text)      set search_path = '';
alter function public.spending_by_category(text)   set search_path = '';
alter function public.budget_status(text)          set search_path = '';
alter function public.monthly_totals()             set search_path = '';

revoke execute on function public.handle_new_user() from public, anon, authenticated;
