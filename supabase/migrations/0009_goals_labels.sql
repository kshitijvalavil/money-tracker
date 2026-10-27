-- Money Tracker — savings goals, labels and insights (Sprint 3)
-- Three new things, all per-user and locked down by Row Level Security:
--
--   * Savings goals: a target amount (and optional target date) plus a
--     ledger of contributions. Progress = contributions / target.
--   * Labels: free-form, reusable tags the user can attach to any number
--     of their transactions (many labels per transaction, many
--     transactions per label).
--   * Insight functions that only read existing data: spending by label,
--     top merchants, this-month-vs-last-month, and "left to spend".
--
-- Every function runs as the caller (security invoker) with an empty
-- search_path, so RLS always applies and each call only sees the caller's
-- own rows. The browser calls these with supabase.rpc('name', { ... }).
--
--   goal_progress()                          -> one row per goal, with saved/pct
--   set_transaction_labels(p_tx, p_labels)   -> replaces a transaction's labels
--   spending_by_label(p_month)               -> expense total per label
--   top_merchants(p_month, p_limit)          -> biggest merchants that month
--   month_over_month(p_month)                -> this month vs the month before
--   left_to_spend(p_month)                   -> budgeted / spent / remaining
--
-- "Month" is always the caller's local 'YYYY-MM', passed in by the client,
-- so a user near midnight or in another timezone sees their own month, not
-- the server's (decision 4).
--
-- Errors (SQLSTATE, so the app can map them to messages):
--   P0002  transaction / goal not found (or not the caller's)
--   22023  bad input (blank name, non-positive amount, bad month, bad limit)
--   42501  not signed in

-- ===== Tables ================================================================

-- Savings goals ---------------------------------------------------------------
create table if not exists public.goals (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name          text not null check (length(btrim(name)) between 1 and 80),
    target_amount numeric(14, 2) not null check (target_amount > 0),
    target_date   date,
    created_at    timestamptz not null default now()
);
create index if not exists goals_user_idx on public.goals (user_id);

-- Money set aside toward a goal. Amounts are positive; a correction is a
-- negative-less world, so to "take money back out" the user deletes a row.
create table if not exists public.goal_contributions (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    goal_id     uuid not null references public.goals (id) on delete cascade,
    amount      numeric(14, 2) not null check (amount > 0),
    occurred_on date not null default current_date,
    note        text not null default '',
    created_at  timestamptz not null default now()
);
create index if not exists goal_contributions_goal_idx on public.goal_contributions (goal_id);

-- Labels: free-form and reusable. Names are unique per user, case-insensitive,
-- so "Work" and "work" are the same label.
create table if not exists public.labels (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
    name       text not null check (length(btrim(name)) between 1 and 40),
    created_at timestamptz not null default now()
);
create unique index if not exists labels_user_name_idx
    on public.labels (user_id, lower(btrim(name)));

-- Which labels are on which transactions (many-to-many).
create table if not exists public.transaction_labels (
    user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
    transaction_id uuid not null references public.transactions (id) on delete cascade,
    label_id       uuid not null references public.labels (id) on delete cascade,
    primary key (transaction_id, label_id)
);
create index if not exists transaction_labels_label_idx on public.transaction_labels (label_id);

-- ===== Row Level Security ====================================================

alter table public.goals               enable row level security;
alter table public.goal_contributions  enable row level security;
alter table public.labels              enable row level security;
alter table public.transaction_labels  enable row level security;

-- goals -----------------------------------------------------------------------
create policy "own goals - select" on public.goals
    for select using (auth.uid() = user_id);
create policy "own goals - insert" on public.goals
    for insert with check (auth.uid() = user_id);
create policy "own goals - update" on public.goals
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own goals - delete" on public.goals
    for delete using (auth.uid() = user_id);

-- goal_contributions: the row is the caller's AND the goal it points at is too
create policy "own contributions - select" on public.goal_contributions
    for select using (auth.uid() = user_id);
create policy "own contributions - insert" on public.goal_contributions
    for insert with check (
        auth.uid() = user_id
        and exists (select 1 from public.goals g
                     where g.id = goal_id and g.user_id = auth.uid()));
create policy "own contributions - update" on public.goal_contributions
    for update using (auth.uid() = user_id)
    with check (
        auth.uid() = user_id
        and exists (select 1 from public.goals g
                     where g.id = goal_id and g.user_id = auth.uid()));
create policy "own contributions - delete" on public.goal_contributions
    for delete using (auth.uid() = user_id);

-- labels ----------------------------------------------------------------------
create policy "own labels - select" on public.labels
    for select using (auth.uid() = user_id);
create policy "own labels - insert" on public.labels
    for insert with check (auth.uid() = user_id);
create policy "own labels - update" on public.labels
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own labels - delete" on public.labels
    for delete using (auth.uid() = user_id);

-- transaction_labels: both the transaction and the label must be the caller's,
-- so nobody can tag a transaction or a label they can't see.
create policy "own tx labels - select" on public.transaction_labels
    for select using (auth.uid() = user_id);
create policy "own tx labels - insert" on public.transaction_labels
    for insert with check (
        auth.uid() = user_id
        and exists (select 1 from public.transactions t
                     where t.id = transaction_id and t.user_id = auth.uid())
        and exists (select 1 from public.labels l
                     where l.id = label_id and l.user_id = auth.uid()));
create policy "own tx labels - delete" on public.transaction_labels
    for delete using (auth.uid() = user_id);

-- ===== Functions =============================================================

-- One row per goal, with how much is saved and how far along it is. pct is
-- capped at 100 so a fully-funded goal's ring never overfills; saved and
-- remaining are the true (uncapped) figures.
create or replace function public.goal_progress()
returns table (
    id            uuid,
    name          text,
    target_amount numeric,
    target_date   date,
    saved         numeric,
    remaining     numeric,
    pct           numeric
)
language sql
stable
security invoker
set search_path = ''
as $$
    select g.id, g.name, g.target_amount, g.target_date,
           coalesce(s.saved, 0) as saved,
           greatest(g.target_amount - coalesce(s.saved, 0), 0) as remaining,
           least(round(coalesce(s.saved, 0) / g.target_amount * 100, 1), 100) as pct
      from public.goals g
      left join (
            select goal_id, sum(amount) as saved
              from public.goal_contributions
             group by goal_id
      ) s on s.goal_id = g.id
     order by g.created_at;
$$;

-- Replace the set of labels on one of the caller's transactions. Label names
-- are matched case-insensitively and created on first use, so the same tag is
-- reused rather than duplicated. Passing an empty array clears the labels.
create or replace function public.set_transaction_labels(p_transaction_id uuid, p_labels text[])
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_uid   uuid := auth.uid();
    v_name  text;
    v_clean text;
    v_id    uuid;
    v_ids   uuid[] := '{}';
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if not exists (select 1 from public.transactions t
                    where t.id = p_transaction_id and t.user_id = v_uid) then
        raise exception 'transaction not found' using errcode = 'P0002';
    end if;

    -- Upsert each distinct, non-blank label and collect its id.
    foreach v_name in array coalesce(p_labels, '{}') loop
        v_clean := btrim(v_name);
        continue when v_clean = '';
        if length(v_clean) > 40 then
            raise exception 'label too long: %', v_clean using errcode = '22023';
        end if;
        select l.id into v_id
          from public.labels l
         where l.user_id = v_uid and lower(btrim(l.name)) = lower(v_clean);
        if v_id is null then
            insert into public.labels (user_id, name) values (v_uid, v_clean)
            returning id into v_id;
        end if;
        if not (v_id = any (v_ids)) then
            v_ids := v_ids || v_id;
        end if;
    end loop;

    -- Replace the join rows: drop any no longer wanted, add any new ones.
    delete from public.transaction_labels
     where transaction_id = p_transaction_id
       and not (label_id = any (v_ids));
    insert into public.transaction_labels (user_id, transaction_id, label_id)
    select v_uid, p_transaction_id, x
      from unnest(v_ids) as x
    on conflict (transaction_id, label_id) do nothing;
end;
$$;

-- Expense total per label for a month ('YYYY-MM'), biggest first. A
-- transaction with several labels counts toward each of them.
create or replace function public.spending_by_label(p_month text)
returns table (label text, total numeric)
language sql
stable
security invoker
set search_path = ''
as $$
    select l.name, sum(t.amount) as total
      from public.transaction_labels tl
      join public.labels l       on l.id = tl.label_id
      join public.transactions t on t.id = tl.transaction_id
     where t.type = 'expense'
       and to_char(t.occurred_on, 'YYYY-MM') = p_month
     group by l.name
     order by total desc, l.name;
$$;

-- The caller's biggest merchants for a month, by total expense. Merchants are
-- grouped on the note, normalised the same way the subscription detector does
-- (lowercased, punctuation to spaces, whitespace collapsed); blank notes are
-- ignored. The displayed name is the most recent original note in the group.
create or replace function public.top_merchants(p_month text, p_limit int default 5)
returns table (merchant text, total numeric, txns int)
language sql
stable
security invoker
set search_path = ''
as $$
    with e as (
        select t.id, t.amount, t.occurred_on, t.note,
               btrim(regexp_replace(
                   regexp_replace(lower(t.note), '[^[:alpha:][:space:]]', ' ', 'g'),
                   '[[:space:]]+', ' ', 'g')) as k
          from public.transactions t
         where t.type = 'expense'
           and to_char(t.occurred_on, 'YYYY-MM') = p_month
    ), latest as (
        select distinct on (k) k, note
          from e where k <> ''
         order by k, occurred_on desc, id::text collate "C" desc
    )
    select latest.note, sum(e.amount) as total, count(*)::int as txns
      from e join latest on latest.k = e.k
     where e.k <> ''
     group by latest.k, latest.note
     order by total desc, latest.note
     limit greatest(p_limit, 1);
$$;

-- This month vs the month before, as three rows (income, expense, net) with
-- the current and previous totals. The UI shows the delta and arrow.
create or replace function public.month_over_month(p_month text)
returns table (metric text, current numeric, previous numeric)
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
    v_prev text;
begin
    if p_month !~ '^\d{4}-\d{2}$' then
        raise exception 'month must be YYYY-MM, got %', p_month using errcode = '22023';
    end if;
    v_prev := to_char((to_date(p_month, 'YYYY-MM') - interval '1 month'), 'YYYY-MM');

    return query
    with m as (
        select type,
               sum(amount) filter (where to_char(occurred_on, 'YYYY-MM') = p_month) as cur,
               sum(amount) filter (where to_char(occurred_on, 'YYYY-MM') = v_prev) as prv
          from public.transactions
         where type in ('income', 'expense')
           and to_char(occurred_on, 'YYYY-MM') in (p_month, v_prev)
         group by type
    )
    select x.metric, x.cur, x.prv
      from (
        select 'income'::text as metric,
               coalesce((select cur from m where type = 'income'), 0) as cur,
               coalesce((select prv from m where type = 'income'), 0) as prv
        union all
        select 'expense',
               coalesce((select cur from m where type = 'expense'), 0),
               coalesce((select prv from m where type = 'expense'), 0)
        union all
        select 'net',
               coalesce((select cur from m where type = 'income'), 0)
                 - coalesce((select cur from m where type = 'expense'), 0),
               coalesce((select prv from m where type = 'income'), 0)
                 - coalesce((select prv from m where type = 'expense'), 0)
      ) x (metric, cur, prv)
     order by case x.metric when 'income' then 1 when 'expense' then 2 else 3 end;
end;
$$;

-- "Left to spend this month": the sum of every budget limit, minus what has
-- already been spent in budgeted categories this month. The month is the
-- caller's local 'YYYY-MM'. remaining can go negative (overspent).
create or replace function public.left_to_spend(p_month text)
returns table (budgeted numeric, spent numeric, remaining numeric)
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
    select coalesce((select sum(b.monthly_limit) from public.budgets b), 0) as budgeted,
           coalesce((select sum(t.amount) from public.transactions t
                      where t.type = 'expense'
                        and to_char(t.occurred_on, 'YYYY-MM') = p_month
                        and exists (select 1 from public.budgets b
                                     where b.category_id = t.category_id)), 0) as spent,
           coalesce((select sum(b.monthly_limit) from public.budgets b), 0)
             - coalesce((select sum(t.amount) from public.transactions t
                          where t.type = 'expense'
                            and to_char(t.occurred_on, 'YYYY-MM') = p_month
                            and exists (select 1 from public.budgets b
                                         where b.category_id = t.category_id)), 0) as remaining;
end;
$$;

-- ===== Grants ================================================================
-- The browser connects as the "authenticated" role; nothing is exposed to anon.
revoke execute on function public.goal_progress()                         from public, anon;
revoke execute on function public.set_transaction_labels(uuid, text[])    from public, anon;
revoke execute on function public.spending_by_label(text)                 from public, anon;
revoke execute on function public.top_merchants(text, int)                from public, anon;
revoke execute on function public.month_over_month(text)                  from public, anon;
revoke execute on function public.left_to_spend(text)                     from public, anon;

grant execute on function public.goal_progress()                         to authenticated;
grant execute on function public.set_transaction_labels(uuid, text[])    to authenticated;
grant execute on function public.spending_by_label(text)                 to authenticated;
grant execute on function public.top_merchants(text, int)                to authenticated;
grant execute on function public.month_over_month(text)                  to authenticated;
grant execute on function public.left_to_spend(text)                     to authenticated;
