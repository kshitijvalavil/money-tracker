-- Money Tracker — per-user Home layout (Sprint 12)
--
-- Sprint 12 lets you arrange your Home: show or hide each card and drag them
-- into the order you want. That choice is personal, so it lives in a tiny
-- per-user table. One row per user, holding an ordered list of cards as JSON,
-- e.g. [{"id":"forecast","show":true},{"id":"buckets","show":false}, ...].
--
-- The shape is validated in the browser (and tolerated loosely here): an
-- unknown card id is ignored and any card missing from the list falls back to
-- its default position, so a future release can add a card without a migration.
--
-- Row Level Security scopes every read and write to the signed-in user, the
-- same rule as every other table. Add-only: no existing object is touched.

create table if not exists public.user_prefs (
    user_id      uuid primary key default auth.uid() references auth.users (id) on delete cascade,
    home_layout  jsonb not null default '[]'::jsonb,
    updated_at   timestamptz not null default now()
);

-- Guard against a runaway payload: the Home has a couple of dozen cards, so a
-- few KB is plenty. This is a backstop, not the real validation (the browser
-- only ever writes a small, well-formed array).
alter table public.user_prefs
    add constraint user_prefs_layout_is_array check (jsonb_typeof(home_layout) = 'array') not valid;
alter table public.user_prefs
    add constraint user_prefs_layout_small check (pg_column_size(home_layout) <= 8192) not valid;

alter table public.user_prefs enable row level security;

create policy "own prefs - select" on public.user_prefs
    for select using (auth.uid() = user_id);
create policy "own prefs - insert" on public.user_prefs
    for insert with check (auth.uid() = user_id);
create policy "own prefs - update" on public.user_prefs
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own prefs - delete" on public.user_prefs
    for delete using (auth.uid() = user_id);

revoke all on table public.user_prefs from anon;
grant select, insert, update, delete on table public.user_prefs to authenticated;
