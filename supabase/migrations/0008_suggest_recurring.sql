-- Money Tracker — subscription / recurring-bill detection (Sprint 2)
-- Depends on 0007_recurring.sql (public.recurring, used to skip groups that
-- are already bills).
--
--   suggest_recurring(p_min_occurrences int default 3)
--     -> table(name, type, amount, frequency, account_id, category_id,
--              last_seen, next_guess, occurrences, confidence)
--
-- Read-only and security invoker: it only ever sees the caller's own rows.
-- The reference implementation is bot/recurring.py (detect + occurrence).
-- Both must agree, and tests/suggest_recurring_test.sql checks that on the
-- shared fixture rows. The rules, in order:
--   1. Income/expense transactions only. Group by (normalised note, type).
--      To normalise a note: lowercase it, turn every non-letter,
--      non-whitespace character into a space, collapse runs of whitespace
--      and trim. Empty keys are ignored. Groups whose key equals the
--      normalised name of one of the caller's recurring rows are excluded.
--   2. Groups with fewer than p_min_occurrences rows are dropped.
--   3. Keep the rows with |amount - median| <= 0.10 x median, where median
--      is the group's median amount.
--   4. occurrences = number of distinct dates among the kept rows, and it
--      must be >= p_min_occurrences. The kept rows must also be at least half
--      of the group's rows (2 x kept >= group), so a chance cluster of
--      similar amounts inside random spend at one merchant does not count
--      (decision 13).
--   5. Median gap between consecutive distinct dates, in inclusive bands:
--      6-8 weekly, 26-35 monthly, 85-95 quarterly, 355-375 yearly. Any other
--      median gap: not recurring. At least 2/3 of the gaps must lie inside
--      that band (3 x in_band >= 2 x gaps; decision 13).
--   6. name, account_id and category_id come from the latest kept row, by
--      (occurred_on, id). amount is round(median of the kept amounts, 2).
--      next_guess is the first anchored occurrence after last_seen, anchored
--      to the first kept date (same month-end clamping as recurring_occurrence).
--      confidence = round(gaps in band / all gaps x kept rows / group rows, 2).
--      Sorted by confidence desc, occurrences desc, key and type in C order.
--
-- Errors: 42501 not signed in, 22023 p_min_occurrences < 2.

create or replace function public.suggest_recurring(p_min_occurrences int default 3)
returns table (
    name        text,
    type        text,
    amount      numeric,
    frequency   text,
    account_id  uuid,
    category_id uuid,
    last_seen   date,
    next_guess  date,
    occurrences int,
    confidence  numeric
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_uid    uuid := auth.uid();
    g        record;
    v_n      int;
    v_med    numeric;
    v_kept   numeric[];
    v_dates  date[];
    v_gaps   int[];
    v_gmed   numeric;
    v_freq   text;
    v_lo     int;
    v_hi     int;
    v_months int;
    v_inband int;
    v_k      int;
    v_next   date;
    v_latest record;
    -- result columns, collected and sorted at the end
    r_key    text[] := '{}';
    r_name   text[] := '{}';
    r_type   text[] := '{}';
    r_amount numeric[] := '{}';
    r_freq   text[] := '{}';
    r_acct   uuid[] := '{}';
    r_cat    uuid[] := '{}';
    r_last   date[] := '{}';
    r_next   date[] := '{}';
    r_occ    int[] := '{}';
    r_conf   numeric[] := '{}';
begin
    if v_uid is null then
        raise exception 'not signed in' using errcode = '42501';
    end if;
    if p_min_occurrences is null or p_min_occurrences < 2 then
        raise exception 'p_min_occurrences must be at least 2' using errcode = '22023';
    end if;

    for g in
        with t as (
            select tx.id, tx.amount, tx.type,
                   btrim(regexp_replace(
                       regexp_replace(lower(tx.note), '[^[:alpha:][:space:]]', ' ', 'g'),
                       '[[:space:]]+', ' ', 'g')) as k
              from public.transactions tx
             where tx.user_id = v_uid
               and tx.type in ('income', 'expense')
        ), known as (
            select btrim(regexp_replace(
                       regexp_replace(lower(r.name), '[^[:alpha:][:space:]]', ' ', 'g'),
                       '[[:space:]]+', ' ', 'g')) as k
              from public.recurring r
             where r.user_id = v_uid
        )
        select t.k, t.type as typ,
               array_agg(t.amount order by t.amount) as amts,
               array_agg(t.id) as ids,
               count(*)::int as cnt
          from t
         where t.k <> ''
           and not exists (select 1 from known where known.k = t.k)
         group by t.k, t.type
        having count(*) >= p_min_occurrences
    loop
        -- 3. amount filter around the group median
        v_n := g.cnt;
        v_med := (g.amts[(v_n + 1) / 2] + g.amts[v_n / 2 + 1]) / 2;

        select array_agg(tx.amount order by tx.amount),
               array_agg(distinct tx.occurred_on order by tx.occurred_on)
          into v_kept, v_dates
          from public.transactions tx
         where tx.id = any (g.ids)
           and abs(tx.amount - v_med) <= v_med * 0.10;

        -- 4. enough distinct dates, and the kept rows are at least half the group?
        if coalesce(array_length(v_dates, 1), 0) < p_min_occurrences
           or 2 * coalesce(array_length(v_kept, 1), 0) < g.cnt then
            continue;
        end if;

        -- 5. median gap -> frequency band
        select array_agg(d.gap order by d.gap) into v_gaps
          from (select v_dates[i] - v_dates[i - 1] as gap
                  from generate_series(2, array_length(v_dates, 1)) as i) d;
        v_n := array_length(v_gaps, 1);
        v_gmed := (v_gaps[(v_n + 1) / 2] + v_gaps[v_n / 2 + 1]) / 2.0;

        v_freq := case
            when v_gmed between 6   and 8   then 'weekly'
            when v_gmed between 26  and 35  then 'monthly'
            when v_gmed between 85  and 95  then 'quarterly'
            when v_gmed between 355 and 375 then 'yearly'
        end;
        if v_freq is null then
            continue;
        end if;
        v_lo := case v_freq when 'weekly' then 6 when 'monthly' then 26
                            when 'quarterly' then 85 else 355 end;
        v_hi := case v_freq when 'weekly' then 8 when 'monthly' then 35
                            when 'quarterly' then 95 else 375 end;
        select count(*)::int into v_inband from unnest(v_gaps) x where x between v_lo and v_hi;
        if 3 * v_inband < 2 * v_n then          -- need >= 2/3 of gaps in the band
            continue;
        end if;

        -- 6. latest kept row, next anchored occurrence after last_seen
        select tx.note, tx.account_id, tx.category_id into v_latest
          from public.transactions tx
         where tx.id = any (g.ids)
           and abs(tx.amount - v_med) <= v_med * 0.10
         order by tx.occurred_on desc, tx.id::text collate "C" desc
         limit 1;

        v_months := case v_freq when 'monthly' then 1 when 'quarterly' then 3
                                when 'yearly' then 12 else 0 end;
        v_k := 1;
        loop
            v_next := case when v_freq = 'weekly'
                           then v_dates[1] + 7 * v_k
                           else (v_dates[1] + make_interval(months => v_months * v_k))::date
                      end;
            exit when v_next > v_dates[array_length(v_dates, 1)];
            v_k := v_k + 1;
        end loop;

        r_key    := r_key    || g.k;
        r_name   := r_name   || left(btrim(v_latest.note), 80);
        r_type   := r_type   || g.typ;
        r_amount := r_amount || round((v_kept[(array_length(v_kept, 1) + 1) / 2]
                                       + v_kept[array_length(v_kept, 1) / 2 + 1]) / 2, 2);
        r_freq   := r_freq   || v_freq;
        r_acct   := r_acct   || v_latest.account_id;
        r_cat    := r_cat    || v_latest.category_id;
        r_last   := r_last   || v_dates[array_length(v_dates, 1)];
        r_next   := r_next   || v_next;
        r_occ    := r_occ    || array_length(v_dates, 1);
        r_conf   := r_conf   || round((v_inband * array_length(v_kept, 1))::numeric
                                      / (v_n * g.cnt), 2);
    end loop;

    return query
        select u.n, u.t, u.a, u.f, u.ac, u.c, u.l, u.nx, u.o, u.cf
          from unnest(r_key, r_name, r_type, r_amount, r_freq, r_acct, r_cat,
                      r_last, r_next, r_occ, r_conf)
               as u(k, n, t, a, f, ac, c, l, nx, o, cf)
         order by u.cf desc, u.o desc, u.k collate "C", u.t collate "C";
end;
$$;

revoke execute on function public.suggest_recurring(int) from public;
revoke execute on function public.suggest_recurring(int) from anon;
grant execute on function public.suggest_recurring(int) to authenticated;
