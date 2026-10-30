# 💰 Money Tracker

A personal money tracker: accounts, income & expenses, transfers between your
own accounts, monthly budgets, recurring bills, savings goals, labels and
reports and insights — all in Indian Rupees (₹).

**One-click deploy** (opens Render pre-configured from `render.yaml` — you just
paste your two Supabase values and click Apply):

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kshitijvalavil/money-tracker)

- **Frontend:** plain HTML/CSS/JS served by a small Flask app.
- **Backend:** [Supabase](https://supabase.com) for the database **and** sign-in.
- **Security:** the browser talks to Supabase directly. Every table has Row
  Level Security, so each signed-in user can only ever read and write their own
  rows — enforced by the database, not just the app. The Flask server never
  holds your database password or sees your data.

---

## What's in here

```
app.py                     Flask app (serves the pages)
templates/                 the pages (dashboard, transactions, accounts, budgets, goals, bills, reports, insights, login)
static/                    CSS and JavaScript
supabase/migrations/       the SQL to set up your database (run in order)
tests/                     a SQL test of the schema, security rules and money logic
```

---

## 1. Set up Supabase (one time)

1. Create a free project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run each file in `supabase/migrations/` **in order**:
   `0001_schema.sql`, `0002_rls.sql`, `0003_functions.sql`, `0004_seed_trigger.sql`,
   `0005_imports.sql`, `0006_approve_import.sql`, `0007_recurring.sql`, `0008_suggest_recurring.sql`,
   `0009_goals_labels.sql`, `0010_investments.sql`, `0011_import_channel.sql`, `0012_security_hardening.sql`.
   (Paste the contents of each, run, then the next.)
3. Create your own account: **Authentication → Users → Add user** (set an email
   and password). The seed trigger gives you starter categories and a Cash
   account automatically.
4. Lock it down so nobody else can register: **Authentication → Providers →
   Email**, turn **off** "Enable sign-ups". (You can leave "Confirm email" off
   for a personal app.)
5. Get your keys from **Settings → API**: the **Project URL** and the
   **anon public** key. The anon key is meant to be used in browsers and is
   safe to expose. **Never** use the `service_role` key or the database
   password in this app.

---

## 2. Run it on your computer

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your URL and anon key
python app.py                 # open http://localhost:5000
```

### Just want to look around first? (demo mode)

Try the whole interface with sample data and **no Supabase and no login**:

```bash
pip install -r requirements.txt
DEMO_MODE=1 python app.py     # open http://localhost:5000
```

Everything is fake and in-memory — nothing is saved, and a reload resets it.
Use this only to explore; turn it off for real use.

---

## 3. Deploy to Render

**Easiest — Blueprint (uses `render.yaml`):**
1. On Render, choose **New → Blueprint** and pick this repo. It reads
   `render.yaml` and configures the web service automatically.
2. When prompted, fill in the two values: `SUPABASE_URL` and
   `SUPABASE_ANON_KEY`.
3. Apply, then open the URL and sign in with the account you created.

**Or set it up by hand — Web Service:**
1. **New → Web Service** from this repo.
2. Build command: `pip install -r requirements.txt`.
   Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
   (binding to `$PORT` is required so Render can reach the app).
3. Add two environment variables:
   - `SUPABASE_URL` — your project URL
   - `SUPABASE_ANON_KEY` — your anon public key
4. Deploy, open the URL, and sign in.

---

## Features

- **Dashboard** — total balance across accounts, this month's income and
  spending, budget progress, and recent transactions.
- **Transactions** — add income, expenses, or transfers between your accounts;
  filter by month and account; delete. Transfers don't count as spending.
- **Accounts** — cash, bank, card or wallet, each with an opening balance and a
  live balance.
- **Budgets** — a monthly limit per category that repeats each month, with
  progress bars that turn red when you go over.
- **Reports** — spending by category (doughnut chart) and income vs. expenses
  over the last 12 months (bar chart), plus **Export CSV** to download all your
  transactions.

---

## Running the tests

**Database** — schema, security rules (one user cannot see another's data) and
money math (balances, transfers, budgets) against a local PostgreSQL:

```bash
bash tests/run.sh
```

**Browser smoke test** — drives the real UI (dashboard, add a transaction/
account/budget, charts) in demo mode, failing on any browser error. Needs
`playwright` and a Chromium:

```bash
DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099 &
BASE_URL=http://127.0.0.1:8099 python tests/smoke_test.py
```

---

## A note on your data

Render's free PostgreSQL and Supabase's free tier have their own limits and
retention policies. Use **Reports → Export CSV** now and then to keep your own
backup of your transactions.

---

## Email import scanner

`bot/scanner.py` turns bank-alert and merchant-receipt emails into rows in
`pending_imports`. You then review them on the **Imports** page and approve
or reject each one. It uses only the Python standard library.

**Dry run on the sample emails.** This prints the rows it would import and
uploads nothing:

```bash
python -m bot.scanner --from-dir tests/fixtures
```

**From Gmail.** You need an OAuth access token with only the
`gmail.readonly` scope. The scanner never changes your mailbox.

```bash
export GMAIL_TOKEN=ya29....                 # read-only Gmail access token
python -m bot.scanner --gmail               # dry run
python -m bot.scanner --gmail --query 'from:(alerts.sbi.bank.in) newer_than:7d'
```

The default query is `from:(alerts.sbi.bank.in) newer_than:30d`. You can
override it with `--query` or the `GMAIL_QUERY` variable.

**Uploading to your account.** Add `--push` and set these variables:

| Variable            | Value                                                      |
|---------------------|------------------------------------------------------------|
| `SUPABASE_URL`      | your project URL                                           |
| `SUPABASE_ANON_KEY` | the public anon key (the same one the app uses)            |
| `SUPABASE_USER_JWT` | **your** access token from a signed-in session             |

The rows are inserted as you, so row-level security keeps them in your own
account. Never use the `service_role` key here. Emails that were already
imported are skipped, and a re-run never creates duplicates: the table is
unique on `(user_id, source_message_id)`.

**Tests:** `python3 tests/test_scanner.py`. The emails in `tests/fixtures/`
are synthetic.
