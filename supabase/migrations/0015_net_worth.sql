-- Money Tracker — net worth, snapshots and goal forecast (Sprint 11)
--
-- The big picture: everything you own minus everything you owe, tracked over
-- time, plus goals that say whether you are on track.
--
--   * Assets      = positive account balances + investments' current value.
--   * Liabilities = the money you owe (an account whose balance is negative,
--                   e.g. a credit card you have spent on, counts here).
--   * Net worth   = assets - liabilities.
--
-- Account balances are derived from transactions, never stored, so net worth
-- is always consistent with the ledger. A small net_worth_snapshots table
-- locks in one figure per month, so the trend line is real history that does
-- not shift when you later re-value an investment.
--
-- All reporting functions are SECURITY INVOKER with a pinned empty
-- search_path, so Row Level Security scopes every read to the caller's own
-- rows (same rule as the Sprint 6 hardening and the Sprint 10 flex functions).
--
-- Errors (SQLSTATE):
--   22023  bad input (p_months out of range)
--   42501  not signed in (capture)

-- ===== Monthly snapshots =====================================================
create table if not exists public.net_worth_snapshots (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
    as_of_month  date not null,                      -- always the first of the month
    assets       numeric(14, 2) not null,
    liabilities  numeric(14, 2) not null,
    net          numeric(14, 2) not null,
    captured_at  timestamptz not null default now(),
    unique (user_id, as_of_month)
);
create index if not exists net_worth_snapshots_user_idx
    on public.net_worth_snapshots (user_id, as_of_month);

alter table public.net_worth_snapshots enable row level security;

create policy "own net worth snapshots - select" on public.net_worth_snapshots
    for select using (auth.uid() = user_id);
create policy "own net worth snapshots - insert" on public.net_worth_snapshots
    for insert with check (auth.uid() = user_id);
create policy "own net worth snapshots - update" on public.net_worth_snapshots
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own net worth snapshots - delete" on public.net_worth_snapshots
    for delete using (auth.uid() = user_id);

revoke all on table public.net_worth_snapshots from anon;
grant select, insert, update, delete on table public.net_worth_snapshots to authenticated;

-- ===== Breakdown (now) =======================================================
-- One row per account (its balance, tagged asset or liability by sign) plus a
-- single row for investments. The browser sums these into the Assets and
-- Liabilities cards and the net worth hero. source is the account type
-- ('cash','bank','card','wallet') or 'investments'; amount is always >= 0.
create or replace function public.net_worth_breakdown()
returns table (source text, label text, kind text, amount numeric)
language sql
stable
security invoker
set search_path = ''
as $$
    with bal as (
        select a.id, a.name, a.type,
            a.opening_balance
            + coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'income'), 0)
            - coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'expense'), 0)
            + coalesce((select sum(t.amount) from public.transactions t
                         where t.to_account_id = a.id and t.type = 'transfer'), 0)
            - coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'transfer'), 0) as balance
        from public.accounts a
    )
    select b.type, b.name,
           case when b.balance < 0 then 'liability' else 'asset' end,
           abs(b.balance)
      from bal b
    union all
    select 'investments', 'Investments', 'asset', coalesce(sum(i.current_value), 0)
      from public.investments i
     having coalesce(sum(i.current_value), 0) > 0;
$$;

-- ===== Trend (series) ========================================================
-- Net worth at the end of each of the last p_months months. Each account's
-- balance is replayed from its transactions up to that month end; investments
-- are valued at their current value from the month they were added onward. A
-- stored snapshot, when present for a month, wins over the recomputed figure,
-- so past months stay fixed once captured.
create or replace function public.net_worth_series(p_months int default 12)
returns table (month text, assets numeric, liabilities numeric, net numeric)
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
    if p_months < 1 or p_months > 60 then
        raise exception 'p_months must be between 1 and 60, got %', p_months using errcode = '22023';
    end if;
    return query
    with months as (
        select to_char(d, 'YYYY-MM') as ym,
               (date_trunc('month', d) + interval '1 month' - interval '1 day')::date as month_end
          from generate_series(
                   date_trunc('month', current_date) - ((p_months - 1) || ' months')::interval,
                   date_trunc('month', current_date),
                   interval '1 month') d
    ),
    bal as (
        select m.ym, a.id,
            a.opening_balance
            + coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'income'
                           and t.occurred_on <= m.month_end), 0)
            - coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'expense'
                           and t.occurred_on <= m.month_end), 0)
            + coalesce((select sum(t.amount) from public.transactions t
                         where t.to_account_id = a.id and t.type = 'transfer'
                           and t.occurred_on <= m.month_end), 0)
            - coalesce((select sum(t.amount) from public.transactions t
                         where t.account_id = a.id and t.type = 'transfer'
                           and t.occurred_on <= m.month_end), 0) as balance
        from months m cross join public.accounts a
    ),
    computed as (
        select m.ym, m.month_end,
            coalesce((select sum(case when b.balance > 0 then b.balance else 0 end)
                        from bal b where b.ym = m.ym), 0)
            + coalesce((select sum(i.current_value) from public.investments i
                         where i.as_of <= m.month_end), 0) as assets,
            coalesce((select sum(case when b.balance < 0 then -b.balance else 0 end)
                        from bal b where b.ym = m.ym), 0) as liabilities
        from months m
    )
    select c.ym,
           coalesce(s.assets, c.assets),
           coalesce(s.liabilities, c.liabilities),
           coalesce(s.net, c.assets - c.liabilities)
      from computed c
      left join public.net_worth_snapshots s
             on to_char(s.as_of_month, 'YYYY-MM') = c.ym
     order by c.ym;
end;
$$;

-- ===== Capture this month's snapshot =========================================
-- Upserts the current month's net worth from the live breakdown. The browser
-- calls this once per load, so history fills in on its own month by month.
-- #variable_conflict use_column: the RETURNS TABLE columns (as_of_month, ...)
-- are also PL/pgSQL variables, so the ON CONFLICT target would otherwise be
-- ambiguous; this pins bare names to the table column. The INSERT values and
-- the final RETURN QUERY use the v_* locals, so resolution stays correct.
create or replace function public.capture_net_worth_snapshot()
returns table (as_of_month date, assets numeric, liabilities numeric, net numeric)
language plpgsql
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_uid    uuid := auth.uid();
    v_month  date := date_trunc('month', current_date)::date;
    v_assets numeric;
    v_liab   numeric;
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    select coalesce(sum(case when b.kind = 'asset'     then b.amount else 0 end), 0),
           coalesce(sum(case when b.kind = 'liability' then b.amount else 0 end), 0)
      into v_assets, v_liab
      from public.net_worth_breakdown() b;
    insert into public.net_worth_snapshots (user_id, as_of_month, assets, liabilities, net)
    values (v_uid, v_month, v_assets, v_liab, v_assets - v_liab)
    on conflict (user_id, as_of_month) do update
        set assets = excluded.assets, liabilities = excluded.liabilities,
            net = excluded.net, captured_at = now();
    return query select v_month, v_assets, v_liab, v_assets - v_liab;
end;
$$;

-- ===== Goal forecast =========================================================
-- goal_progress() plus pace: the average monthly contribution (over the months
-- the goal actually received money), how many such months there were, the
-- projected finish date at that pace, and what would be needed each month to
-- hit the target date. The browser turns these into an on-track / behind pill;
-- a goal with no contributions has monthly_rate 0 and a null projected_date.
create or replace function public.goal_forecast()
returns table (
    id             uuid,
    name           text,
    target_amount  numeric,
    target_date    date,
    saved          numeric,
    remaining      numeric,
    pct            numeric,
    monthly_rate   numeric,
    months_active  int,
    projected_date date,
    needed_monthly numeric
)
language sql
stable
security invoker
set search_path = ''
as $$
    with agg as (
        select g.id, g.name, g.target_amount, g.target_date, g.created_at,
            coalesce(sum(gc.amount), 0) as saved,
            count(distinct to_char(gc.occurred_on, 'YYYY-MM'))::int as months_active,
            case when count(gc.id) > 0
                 then coalesce(sum(gc.amount), 0)
                      / greatest(count(distinct to_char(gc.occurred_on, 'YYYY-MM')), 1)
                 else 0 end as monthly_rate
          from public.goals g
          left join public.goal_contributions gc on gc.goal_id = g.id
         group by g.id, g.name, g.target_amount, g.target_date, g.created_at
    )
    select a.id, a.name, a.target_amount, a.target_date, a.saved,
           greatest(a.target_amount - a.saved, 0) as remaining,
           least(round(a.saved / a.target_amount * 100, 1), 100) as pct,
           round(a.monthly_rate, 2) as monthly_rate,
           a.months_active,
           case when a.saved >= a.target_amount then current_date
                when a.monthly_rate > 0
                    then (date_trunc('month', current_date)
                          + (ceil((a.target_amount - a.saved) / a.monthly_rate) || ' months')::interval)::date
                else null end as projected_date,
           case when a.target_date is not null and a.saved < a.target_amount then
                    round((a.target_amount - a.saved) / greatest(
                        ( (extract(year  from age(date_trunc('month', a.target_date),
                                                  date_trunc('month', current_date))) * 12
                         + extract(month from age(date_trunc('month', a.target_date),
                                                  date_trunc('month', current_date))) )::numeric), 1), 2)
                else null end as needed_monthly
      from agg a
     order by a.created_at;
$$;

-- ===== Grants ================================================================
revoke execute on function public.net_worth_breakdown()        from public, anon;
revoke execute on function public.net_worth_series(int)        from public, anon;
revoke execute on function public.capture_net_worth_snapshot() from public, anon;
revoke execute on function public.goal_forecast()              from public, anon;

grant execute on function public.net_worth_breakdown()        to authenticated;
grant execute on function public.net_worth_series(int)        to authenticated;
grant execute on function public.capture_net_worth_snapshot() to authenticated;
grant execute on function public.goal_forecast()              to authenticated;
