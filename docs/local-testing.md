# Running and testing Money Tracker locally

This is for Boss to try Sprint 15 (file import) and Sprint 16 (voice) on a
laptop and on a phone, with no Supabase account needed. It uses **demo mode**:
a fake in-memory account with sample data that resets when you reload.

## 1. One-time setup

```bash
cd money-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # app + test tools
python3 tools/make_import_samples.py        # builds the sample files to try (needs xlwt, in dev reqs)
```

## 2. Run it on your laptop

```bash
DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099
```

Open <http://127.0.0.1:8099>. You're signed in to the demo automatically.
Go to **Transactions → To review → Import a file** (or straight to
`/import`). Reloading the page clears demo data.

## 3. Try the file import (Sprint 15)

The sample files are in `tests/fixtures/import/` (all made-up data):

| File | What it tests |
|------|----------------|
| `money-manager-style.xlsx` | A phone-app export, same 12-column shape as your own file (two "Account" columns, two amount columns, mixed dates). 31 rows. |
| `hdfc-style.xls` | Old Excel with ~20 junk rows above the header and Withdrawal/Deposit columns. Asks before reading it on the server. |
| `kotak-style.csv` | Debit/Credit columns, Indian number grouping, Dr/Cr markers. |
| `splitwise.csv`, `signed-amounts-semicolon.csv` | Other real-world shapes (totals row skipped; semicolons; month-first dates). |
| `statement.pdf` | A text PDF bank statement (read on the device). |
| `scanned-statement.pdf` | An image-only PDF. Asks before sending page images to Claude (in demo mode it returns a fixed sample, so no key is needed). |
| `bills.docx` | A Word table of bills. |
| `messy.xlsx`, `empty.xlsx`, `corrupt.xlsx`, `hostile.csv`, `big-5000.xlsx` | Edge cases: totals/bad dates, empty, damaged, formula-injection text, 5,000 rows. |

Your own export (`17877072-...xlsx`) works too — drop it in. It isn't committed
to the repo because it's personal data.

Flow: drop a file → check the columns and preview → **Add to review** → the rows
appear under **To review**, marked "From a file", where you accept them like
email imports. **Undo** on the import page (or the done screen) removes a whole
import's still-pending rows. Importing the same file twice adds nothing new.

Try **Download a template** on the import page for the simplest fixed format.

## 4. Open it on your phone (same Wi-Fi)

```bash
DEMO_MODE=1 gunicorn app:app --bind 0.0.0.0:8099
```

Find your laptop's IP (`ipconfig getifaddr en0` on macOS, `hostname -I` on
Linux) and open `http://<laptop-ip>:8099` on the phone. Add it to the home
screen to try it as an installed app.

**For Sprint 16 (voice):** the microphone only works over **HTTPS** (or on
`localhost`). Plain `http://<laptop-ip>` will not get mic access. Two ways to
get HTTPS to the phone for testing:

- **A tunnel** (easiest): run something like `cloudflared tunnel --url
  http://127.0.0.1:8099` and open the `https://…trycloudflare.com` link it
  prints on the phone.
- **A Chrome flag on the test phone**: open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`,
  add `http://<laptop-ip>:8099`, and relaunch. Use this only on a test device.

The typed/dictation fallback works without HTTPS, so you can still try the
parser by typing a sentence.

## 5. Run the automated tests

Captain Shipit's full local gate (every suite, SQL, security, browser, with
pass counts so a branch can be compared with `main`):

```bash
bash tests/gate.sh            # everything
bash tests/gate.sh unit       # Python + Node unit tests only
bash tests/gate.sh sql        # database tests only
bash tests/gate.sh ui         # browser suites only (starts its own demo server)
```

The database tests need PostgreSQL and don't run as root; the gate handles that
by running them as the `postgres` user when needed. Browser suites need the
Playwright Chromium (`python -m playwright install chromium`, or set
`CHROME_PATH`).

Just the new Sprint 15 pieces:

```bash
node tests/importmap_test.mjs          # column mapper: amounts, dates, de-dupe
python3 tests/xlsreader_test.py        # old .xls reader + /api/read-xls
python3 tests/statementreader_test.py  # Claude fallback (no network; stubbed)
# SQL: run via the gate, or directly as the postgres user:
#   su postgres -s /bin/bash -c "cd $PWD && bash tests/run.sh"
```

## 6. Try the voice note (Sprint 16)

Open **Transactions → Add by voice** (or `/voice`). Tap the mic and say something,
or type it in the box and press **Read it** (there are tappable example phrases).
You get a card per item; check the amount, account and category, then:

- **Add** — saves an expense/income straight away.
- **Add as bill** — appears for phrases like "…monthly"; saves a recurring bill.
- **Send to review** — drops it in To review instead.

Try: "paid 450 to Starbucks on UPI", "chai 20 and auto 80 cash", "got my salary
85000", "electricity bill 1840 due on the 15th monthly". The parser understands
rupees/₹, "4.5k", "1.2 lakh", a few Hinglish numbers ("paanch sau"), UPI / card /
cash, and dates like "today" or "on the 15th". Your voice is never stored.

**The microphone needs HTTPS** — see the HTTPS note in section 4. On plain
`http://<laptop-ip>` the mic is disabled, but the typed box (and your phone
keyboard's own mic) still work, so you can try the whole flow.

Run just the voice tests:

```bash
node tests/voiceparse_test.mjs          # JS parser (191 phrase checks)
python3 tests/voiceparse_test.py        # Python twin + JS/Python parity + endpoint
```
