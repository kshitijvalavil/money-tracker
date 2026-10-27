# Sprint 3 — goals, labels and insights

What Sprint 3 ships and the decisions behind it. The reference is migration
`0009_goals_labels.sql`; the demo client (`static/js/demo.js`) mirrors it and
the two are checked against the same fixtures by `tests/goals_labels_test.sql`
(DB) and `tests/sprint3_ui_test.py` (UI).

## Scope

- **Savings goals** with progress rings (`/goals`).
- **Free-form labels** on transactions, many per transaction (`/transactions`).
- **Insights** that only read existing data (`/insights`): this-month-vs-last,
  top merchants, and spending by label.
- A **"left to spend this month"** card on the dashboard.

## Decisions

1. **Goals are savings targets with a contributions ledger.** A goal has a
   `target_amount` and an optional `target_date`. Money put aside is recorded
   as rows in `goal_contributions`; `saved` is their sum. There is no linked
   account — a goal is a plan, not a balance — so saving toward it is explicit
   and never guessed from transfers.
2. **Progress is clamped for display, honest in the numbers.** `goal_progress()`
   returns the true `saved` and `remaining` (floored at 0), plus `pct` capped at
   100 so a fully-funded ring never overfills. To take money back out, delete a
   contribution (amounts are always positive).
3. **Labels are free-form, reusable and case-insensitive.** `labels` are unique
   per user on `lower(trim(name))`, so "Work" and "work" are one label.
   `transaction_labels` is a many-to-many join: any number of labels per
   transaction, any number of transactions per label.
4. **`set_transaction_labels(tx, names[])` is the only way the UI edits labels.**
   It validates ownership of the transaction (P0002 otherwise), upserts each
   name (reusing an existing label case-insensitively), de-dupes within the
   call, and replaces the transaction's whole label set. An empty array clears
   them. Names over 40 chars are rejected (22023).
5. **A multi-label expense counts toward each of its labels** in
   `spending_by_label(month)`. Totals therefore need not sum to the month's
   spend; the view answers "how much touched this label", not a partition.
6. **Top merchants group on the normalised note**, exactly as the subscription
   detector does (lowercase, punctuation → spaces, whitespace collapsed); blank
   notes are ignored. The displayed name is the most recent original note in the
   group. Expenses only.
7. **`month_over_month(month)` returns three rows** — income, expense and net
   (income − expense) — each with the current and previous month's total. The
   UI derives the arrow and percent, colouring "up" as good for income/net and
   bad for expense.
8. **"Left to spend" = every budget limit minus spend in budgeted categories**,
   for the given month (`left_to_spend(month)`). It can go negative (overspent),
   and the dashboard card only appears once at least one budget exists.
9. **The month is always the client's local `YYYY-MM`** (passed in by the
   browser, consistent with SB-11), so a user near midnight or in another
   timezone sees their own month, not the server's.
10. **Everything stays under RLS.** All functions are `security invoker` with an
    empty `search_path`, granted only to `authenticated`; the new tables carry
    the same own-rows policies as the rest of the schema, and the join tables
    additionally check that the referenced transaction/label/goal is the
    caller's own.

## Not in this sprint

- Recurring/automatic goal contributions, and goal reminders.
- Label colours or a label-management page (labels are created and reused
  inline from the transaction row; an unused label simply stops appearing).
- Budget-category-level "left to spend" (the card is a single monthly figure).
