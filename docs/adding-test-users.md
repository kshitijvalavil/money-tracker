# Adding test users

Money Tracker is multi-user. Every person signs in with their own email and
password, and each person can only ever see and change their own data. You can
safely hand out logins to testers without any of them seeing your money.

## How isolation works (and why it is safe)

- Sign-in is handled by **Supabase Auth**. Each user has their own email and
  their own password hash — credentials are never shared.
- Every table that stores data has **Row Level Security (RLS)** turned on with a
  per-user policy (`auth.uid() = user_id`). The database itself refuses to
  return, change, or delete a row that belongs to someone else. There is no code
  path, URL, or API trick that gets around it — the rule lives in the database.
- A brand-new user starts with only their own empty account: 12 default
  categories and a Cash account, seeded automatically. They see nothing of
  yours.

This is covered by automated tests (see "Proving it" below), so it cannot
silently regress in a future change.

## Signups are OFF on purpose

There is no public "create account" page. The only way to get a login is for the
admin (you) to create it. That keeps the app locked down to the 10 testers you
choose. Keep "Allow new users to sign up" **disabled** in Supabase →
Authentication → Providers → Email.

## The 10 tester slots

Fill in an email, create the user (below), and share the password with that
person. Keep this table updated as you add people.

| #  | Email                   | Status   | Notes                         |
|----|-------------------------|----------|-------------------------------|
| 1  | bharat.rao90@gmail.com  | Created  | Password delivered privately  |
| 2  |                         | Open     |                               |
| 3  |                         | Open     |                               |
| 4  |                         | Open     |                               |
| 5  |                         | Open     |                               |
| 6  |                         | Open     |                               |
| 7  |                         | Open     |                               |
| 8  |                         | Open     |                               |
| 9  |                         | Open     |                               |
| 10 |                         | Open     |                               |

## How to add a tester

### Option A — Supabase dashboard (simplest, recommended)

1. Open the Supabase project → **Authentication** → **Users**.
2. Click **Add user** → **Create new user**.
3. Enter the tester's **email** and a **password**.
4. Tick **Auto Confirm User** (so they can sign in immediately).
5. Click **Create user**.

That's it. The first time they sign in, their own categories and Cash account
are created automatically. They see only their own data — nothing of yours.

Then send the tester the app link and their email + password:
`https://money-tracker-iabp.onrender.com/`

### Option B — SQL snippet (for adding several at once)

In the Supabase project → **SQL Editor**, run this once per tester (change the
two values at the top). It needs the `pgcrypto` extension, which is already
enabled on this project.

```sql
-- === set these two ===
\set tester_email 'tester2@example.com'
\set tester_password 'ChooseAStrongPasswordHere'

with new_user as (
  insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, created_at, updated_at,
    raw_app_meta_data, raw_user_meta_data
  ) values (
    '00000000-0000-0000-0000-000000000000',
    gen_random_uuid(), 'authenticated', 'authenticated',
    :'tester_email', crypt(:'tester_password', gen_salt('bf')),
    now(), now(), now(),
    '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb
  )
  returning id, email
)
insert into auth.identities (id, user_id, provider_id, identity_data, provider,
                             last_sign_in_at, created_at, updated_at)
select gen_random_uuid(), id, id::text,
       jsonb_build_object('sub', id::text, 'email', email,
                          'email_verified', true, 'phone_verified', false),
       'email', now(), now(), now()
from new_user
returning user_id;
```

(If your SQL client does not support `\set`, replace `:'tester_email'` and
`:'tester_password'` with the literal quoted values.)

## Proving it (the tested feature)

Isolation is verified two ways:

- **In CI, on every push** — `tests/user_isolation_test.sql` (run by
  `tests/run.sh`) seeds two users against the real migrations, fills one user's
  account with a row in *every* table, then — acting as the other user through
  RLS — asserts they see zero of the first user's rows and cannot read, change,
  delete, or plant data in the other account.

- **Against the live site, on demand** — `tests/live_login_isolation_test.py`
  actually signs in over HTTPS as a tester and reads every table with the token
  it gets back, proving the tester sees only their own rows and none of the
  owner's. Run it after adding a tester:

  ```sh
  export SUPABASE_URL="https://<ref>.supabase.co"
  export SUPABASE_ANON_KEY="<anon or publishable key>"
  export MT_TESTER_EMAIL="bharat.rao90@gmail.com"
  export MT_TESTER_PASSWORD="<the tester's password>"
  export MT_OWNER_USER_ID="<your auth user id>"
  python3 tests/live_login_isolation_test.py
  ```

## Removing a tester when you're done

Supabase → Authentication → Users → the user → **Delete user**. Their data is
removed with them and no one else is affected.
