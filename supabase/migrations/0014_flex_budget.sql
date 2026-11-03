-- Money Tracker — flexible budget, rollovers, monthly recap (Sprint 10)
--
-- The old model was one flat list of per-category monthly limits. Sprint 10
-- groups spending into three plain-English buckets and answers the one
-- question that matters day to day: how much is still free to spend this month.
--
--   * categories.bucket   — 'fixed' (rent, EMIs, subscriptions), 'flexible'
--                           (food, shopping, fun — the money you steer) or
--                           'non_monthly' (insurance, travel, gifts — set aside).
--   * budgets.rollover    — when on, what you don't spend in a category this
--                           month is added to next month's room (a sinking
--                           fund, e.g. a travel pot).
--
-- Three new reporting functions, all SECURITY INVOKER so Row Level Security
-- still scopes every read to the caller's own rows, and all with a pinned
-- empty search_path like the Sprint 6 hardening (migration 0012).

-- 1. Bucket on each category. Existing categories default to 'flexible'; the
--    user re-buckets them on the Budgets page. Rent and Bills are the obvious
--    fixed ones, so move those for anyone who hasn't changed them yet.
alter table public.categories
    add column if not exists bucket text not null default 'flexible'
    check (bucket in ('fixed', 'flexible', 'non_monthly'));

update public.categories
   set bucket = 'fixed'
 where kind = 'expense' and bucket = 'flexible'
   and name in ('Rent', 'Bills');

-- 2. Rollover flag on each budget. Off by default, so nothing changes for
--    existing budgets until the user turns it on.
alter table public.budgets
    add column if not exists rollover boolean not null default false;

-- 3. New users get sensible buckets from the start. Same starter set as before
--    (migration 0004), now with a bucket each. SECURITY DEFINER trigger.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.categories (user_id, name, kind, bucket) values
        (new.id, 'Salary',        'income',  'flexible'),
        (new.id, 'Freelance',     'income',  'flexible'),
        (new.id, 'Gift',          'income',  'flexible'),
        (new.id, 'Investment',    'income',  'flexible'),
        (new.id, 'Food',          'expense', 'flexible'),
        (new.id, 'Rent',          'expense', 'fixed'),
        (new.id, 'Transport',     'expense', 'flexible'),
        (new.id, 'Bills',         'expense', 'fixed'),
        (new.id, 'Shopping',      'expense', 'flexible'),
        (new.id, 'Health',        'expense', 'flexible'),
        (new.id, 'Entertainment', 'expense', 'flexible'),
        (new.id, 'Other',         'expense', 'flexible');

    insert into public.accounts (user_id, name, type, opening_balance) values
        (new.id, 'Cash', 'cash', 0);

    return new;
end;
$$;

-- 4. Per-bucket totals for a month ('YYYY-MM'): how much is budgeted in each
--    bucket and how much has been spent against those budgets. "Spent" counts
--    only categories that have a budget in the bucket, so the hero and the
--    Budgets page always agree; spending in a category with no limit shows in
--    the "Spent this month" total, not here. The browser turns this into the
--    "Your money this month" card and the "Left to spend freely" hero.
create or replace function public.flex_overview(p_month text)
returns table (bucket text, budgeted numeric, spent numeric)
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
    if p_month !~ '^\d{4}-\d{2}$' then
        raise exception 'month must be YYYY-MM, got %', p_month using errcode = '22023';
    end if;
    return query
    with buckets (bucket) as (values ('fixed'), ('flexible'), ('non_monthly'))
    select bk.bucket,
        coalesce((select sum(b.monthly_limit)
                    from public.budgets b
                    join public.categories c on c.id = b.category_id
                   where c.bucket = bk.bucket), 0) as budgeted,
        coalesce((select sum(t.amount)
                    from public.transactions t
                    join public.categories c on c.id = t.category_id
                    join public.budgets b on b.category_id = c.id
                   where t.type = 'expense'
                     and c.bucket = bk.bucket
                     and to_char(t.occurred_on, 'YYYY-MM') = p_month), 0) as spent
    from buckets bk;
end;
$$;

-- 5. One row per budgeted category for a month: its bucket, limit, whether it
--    rolls over, what's been spent, and the rollover pot carried in. The pot is
--    what went unspent in this category over the prior 12 months, never below
--    zero — a month you overspend just eats into the pot, it can't make the pot
--    negative. Only months the user was actually active (had any transaction)
--    count, so a new budget doesn't invent savings from months before you
--    started. Drives the Budgets page (bucket grouping, rollover switches,
--    per-category "left").
create or replace function public.category_rollovers(p_month text)
returns table (category_id uuid, name text, bucket text,
               monthly_limit numeric, rollover boolean, spent numeric, carried numeric)
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
    if p_month !~ '^\d{4}-\d{2}$' then
        raise exception 'month must be YYYY-MM, got %', p_month using errcode = '22023';
    end if;
    return query
    select b.category_id, c.name, c.bucket, b.monthly_limit, b.rollover,
        coalesce((select sum(t.amount) from public.transactions t
                   where t.category_id = b.category_id
                     and t.type = 'expense'
                     and to_char(t.occurred_on, 'YYYY-MM') = p_month), 0) as spent,
        case when b.rollover then
            greatest(0, (
                select coalesce(sum(
                    b.monthly_limit
                    - coalesce((select sum(t.amount) from public.transactions t
                                 where t.category_id = b.category_id
                                   and t.type = 'expense'
                                   and to_char(t.occurred_on, 'YYYY-MM') = mm.m), 0)
                ), 0)
                from (
                    select to_char(
                        (to_date(p_month || '-01', 'YYYY-MM-DD') - (g || ' months')::interval),
                        'YYYY-MM') as m
                    from generate_series(1, 12) as g
                ) mm
                where exists (select 1 from public.transactions t
                               where to_char(t.occurred_on, 'YYYY-MM') = mm.m)
            ))
        else 0 end as carried
    from public.budgets b
    join public.categories c on c.id = b.category_id
    order by c.bucket, c.name;
end;
$$;

-- 6. Monthly recap, posted on the 1st for the month just gone. Money in and
--    out, what was saved and the savings rate (and last month's, for the
--    change), the category that jumped the most and the one that fell the most
--    versus the previous month, how much rolled into next month, and what was
--    left in the flexible bucket.
create or replace function public.monthly_recap(p_month text)
returns table (
    income numeric, expense numeric, saved numeric,
    savings_rate numeric, prev_savings_rate numeric,
    biggest_jump_cat text, biggest_jump_delta numeric,
    best_saver_cat text, best_saver_delta numeric,
    rolled_over numeric, flexible_left numeric)
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
    v_prev text := to_char(to_date(p_month || '-01', 'YYYY-MM-DD') - interval '1 month', 'YYYY-MM');
begin
    if p_month !~ '^\d{4}-\d{2}$' then
        raise exception 'month must be YYYY-MM, got %', p_month using errcode = '22023';
    end if;
    return query
    with cur as (
        select c.name, sum(t.amount) as amt
        from public.transactions t
        join public.categories c on c.id = t.category_id
        where t.type = 'expense' and to_char(t.occurred_on, 'YYYY-MM') = p_month
        group by c.name),
    prv as (
        select c.name, sum(t.amount) as amt
        from public.transactions t
        join public.categories c on c.id = t.category_id
        where t.type = 'expense' and to_char(t.occurred_on, 'YYYY-MM') = v_prev
        group by c.name),
    diff as (
        select coalesce(cur.name, prv.name) as name,
               coalesce(cur.amt, 0) - coalesce(prv.amt, 0) as delta
        from cur full join prv on cur.name = prv.name),
    inc  as (select coalesce(sum(amount), 0) as v from public.transactions
             where type = 'income'  and to_char(occurred_on, 'YYYY-MM') = p_month),
    exp  as (select coalesce(sum(amount), 0) as v from public.transactions
             where type = 'expense' and to_char(occurred_on, 'YYYY-MM') = p_month),
    pinc as (select coalesce(sum(amount), 0) as v from public.transactions
             where type = 'income'  and to_char(occurred_on, 'YYYY-MM') = v_prev),
    pexp as (select coalesce(sum(amount), 0) as v from public.transactions
             where type = 'expense' and to_char(occurred_on, 'YYYY-MM') = v_prev)
    select
        (select v from inc),
        (select v from exp),
        (select v from inc) - (select v from exp),
        case when (select v from inc) > 0
             then round(((select v from inc) - (select v from exp)) / (select v from inc), 4)
             else null end,
        case when (select v from pinc) > 0
             then round(((select v from pinc) - (select v from pexp)) / (select v from pinc), 4)
             else null end,
        (select name  from diff order by delta desc nulls last, name limit 1),
        (select delta from diff order by delta desc nulls last, name limit 1),
        (select name  from diff order by delta asc  nulls last, name limit 1),
        (select delta from diff order by delta asc  nulls last, name limit 1),
        coalesce((select sum(greatest(0, b.monthly_limit
                - coalesce((select sum(t.amount) from public.transactions t
                             where t.category_id = b.category_id and t.type = 'expense'
                               and to_char(t.occurred_on, 'YYYY-MM') = p_month), 0)))
                   from public.budgets b where b.rollover), 0),
        coalesce((select sum(b.monthly_limit) from public.budgets b
                    join public.categories c on c.id = b.category_id
                   where c.bucket = 'flexible'), 0)
        - coalesce((select sum(t.amount) from public.transactions t
                      join public.categories c on c.id = t.category_id
                      join public.budgets b on b.category_id = c.id
                     where t.type = 'expense' and c.bucket = 'flexible'
                       and to_char(t.occurred_on, 'YYYY-MM') = p_month), 0);
end;
$$;

-- Same access rule as the other reporting functions: signed-in users only,
-- never anon.
revoke execute on function public.flex_overview(text)       from public, anon;
revoke execute on function public.category_rollovers(text)  from public, anon;
revoke execute on function public.monthly_recap(text)       from public, anon;
grant  execute on function public.flex_overview(text)       to authenticated;
grant  execute on function public.category_rollovers(text)  to authenticated;
grant  execute on function public.monthly_recap(text)       to authenticated;
