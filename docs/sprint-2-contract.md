# Sprint 2 contract: recurring bills and subscriptions

Goal: never miss a bill, and spot forgotten subscriptions.
Owner of this file: Manager. Bots build against it in parallel; change it only through the Manager.

## Decisions

- **No server cron.** The app has no backend worker (Render free plan, browser talks to Supabase).
  Due bills are posted when the user opens the app: the shell calls `post_due_recurring()` once per page load.
  Posting is idempotent, so calling it many times never double-posts.
- **Auto-post is opt-in per bill.** `auto_post = false` (default) means the bill only shows as due and the user taps
  "Mark paid". `auto_post = true` means it is posted automatically on/after its due date.
- **No date drift.** The schedule is anchored to `start_date`. Occurrence *n* is `start_date + n × period`,
  with the day clamped to the month's length (31 Jan → 28/29 Feb → 31 Mar, not 28 Mar).
- Money is `numeric(14,2)`, never float. All new functions are `security invoker`, `set search_path = ''`,
  execute revoked from `public`/`anon`, granted to `authenticated` (same as 0006).

## Database (Tabledore) — `supabase/migrations/0007_recurring.sql`

```sql
public.recurring (
  id           uuid pk default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users on delete cascade,
  name         text not null check (length(btrim(name)) between 1 and 80),
  type         text not null check (type in ('income','expense')),
  amount       numeric(14,2) not null check (amount > 0),
  account_id   uuid not null references public.accounts on delete cascade,
  category_id  uuid not null references public.categories on delete restrict,
  frequency    text not null check (frequency in ('weekly','monthly','quarterly','yearly')),
  start_date   date not null,
  next_seq     int  not null default 0,     -- index of the next unpaid occurrence
  next_due     date not null,               -- = occurrence(next_seq); kept in sync by the functions
  auto_post    boolean not null default false,
  active       boolean not null default true,
  source       text not null default 'manual' check (source in ('manual','detected')),
  created_at   timestamptz not null default now()
)
-- RLS: own rows only (select/insert/update/delete), insert/update also check account and category belong to the caller.
-- Category kind must equal type (trigger or check in functions).

alter table public.transactions
  add column recurring_id uuid references public.recurring on delete set null,
  add column recurring_seq int;
create unique index transactions_recurring_seq_uniq
  on public.transactions (recurring_id, recurring_seq) where recurring_id is not null;
```

Functions:

| Function | Returns | Behaviour |
|---|---|---|
| `recurring_occurrence(p_start date, p_freq text, p_n int)` | `date` | Pure (immutable). Anchored date math with month-end clamping. |
| `mark_recurring_paid(p_id uuid, p_paid_on date default current_date)` | `uuid` (transaction id) | Locks the row, posts occurrence `next_seq` as a transaction (`occurred_on = p_paid_on`, note = name, `recurring_id`, `recurring_seq`), advances `next_seq`/`next_due`. If that seq is already posted, just advances and returns the existing id. |
| `skip_recurring(p_id uuid)` | `void` | Advances to the next occurrence without posting. |
| `post_due_recurring(p_as_of date default current_date)` | `int` (rows posted) | For the caller's active `auto_post` rows with `next_due <= p_as_of`, posts every missed occurrence up to `p_as_of` (each `occurred_on` = its due date). Cap 60 per bill per call. Idempotent and safe under concurrent calls. |
| `upcoming_recurring(p_days int default 30)` | `table(id, name, type, amount, next_due, days_left int, overdue bool, auto_post)` | Active rows due within `p_days` (and all overdue), ordered by `next_due`. |

Errors: `P0002` not found / not yours, `22023` bad input (kind mismatch, bad frequency), `42501` not signed in.
Tests go in `tests/recurring_test.sql`, wired into `tests/run.sh`, and also appended to `setup_database.sql` + README migration list.

## Detection (Byte Vader) — `supabase/migrations/0008_suggest_recurring.sql` + scanner

- `suggest_recurring(p_min_occurrences int default 3)` returns
  `table(name text, type text, amount numeric, frequency text, account_id uuid, category_id uuid, last_seen date, next_guess date, occurrences int, confidence numeric)`.
  Groups the caller's income/expense transactions by normalised note (lowercase, digits/punctuation stripped)
  + type; amount within ±10% of the median; gaps whose median is 6–8 days → weekly, 26–35 → monthly,
  85–95 → quarterly, 355–375 → yearly. Excludes groups already covered by a `recurring` row (same normalised name).
  `security invoker`, read-only, same grants as above.
- Python: `bot/recurring.py` with the same date math (`occurrence(start, freq, n)`) and detection logic as a pure
  function, plus `tests/test_recurring.py` — this is the reference the SQL is checked against.
- `post_due_recurring` is called by the app on load (see FE); no extra job.

## Front end (Sir Pixelot)

- New page **Bills** at `/bills` (`templates/bills.html`, nav item "🧾 Bills" after Budgets, route in `app.py`):
  - List of recurring items: name, amount, frequency, next due ("in 3 days" / "overdue 2 days"), auto-post toggle,
    actions **Mark paid**, **Skip**, Edit, Pause/Resume, Delete. Add/edit form (name, type, amount, account, category
    filtered by type, frequency, start date, auto-post).
  - **Suggestions** section from `suggest_recurring()` with "Add as bill" (pre-fills the form) and "Dismiss"
    (session-only is fine).
  - Monthly total of active expense bills ("Bills ≈ ₹X / month", weekly ×52/12, quarterly ÷3, yearly ÷12).
- Dashboard: **Upcoming bills** card (next 7 days + overdue) from `upcoming_recurring(7)`, each with Mark paid.
- Shell: on every page load call `post_due_recurring()` once; if it posted > 0 show a toast "Posted N bills".
- `demo.js`: in-memory versions of the five RPCs + table, with 4 sample bills (rent monthly, Netflix monthly,
  gym weekly auto-post, insurance yearly) and history that makes `suggest_recurring` return 1–2 suggestions.
- Phone layout cards, like imports. Keyboard and screen-reader friendly (design skills: interaction-design,
  ux-writing, accessibility-audit).

## Testing (Sherlock Bugs) — release gate

- Date edge cases: start on 29/30/31, Feb in leap and non-leap years, yearly on 29 Feb, weekly across year end.
- No double posting: `post_due_recurring` called twice, concurrently, and after a manual Mark paid of the same seq.
- RLS: user B cannot see, pay, skip or post user A's bills; cannot attach A's account/category.
- Detection accuracy on fixtures (true positives, no false positives on random spend).
- UI: Bills page + dashboard card in demo mode at 1440 and 390 px, no horizontal scroll, all actions work.
- Verdict SHIP / NO-SHIP with numbered bugs (SB-9 onward).
