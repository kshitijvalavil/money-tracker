# 💰 Money Tracker

A personal money tracker: accounts, income & expenses, transfers between your
own accounts, monthly budgets, and reports — all in Indian Rupees (₹).

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
templates/                 the pages (dashboard, transactions, accounts, budgets, reports, login)
static/                    CSS and JavaScript
supabase/migrations/       the SQL to set up your database (run in order)
tests/                     a SQL test of the schema, security rules and money logic
```

---

## 1. Set up Supabase (one time)

1. Create a free project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run each file in `supabase/migrations/` **in order**:
   `0001_schema.sql`, `0002_rls.sql`, `0003_functions.sql`, `0004_seed_trigger.sql`.
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

The tests check the schema, the security rules (one user cannot see another's
data), and the money math (balances, transfers, budgets) against a local
PostgreSQL. With `psql` and a local Postgres available:

```bash
bash tests/run.sh
```

---

## A note on your data

Render's free PostgreSQL and Supabase's free tier have their own limits and
retention policies. Use **Reports → Export CSV** now and then to keep your own
backup of your transactions.
