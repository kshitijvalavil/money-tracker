# Sprint 15 plan: Bulk import from Excel, CSV, PDF and Word

Status: **plan only, waiting for Boss approval on the live board.** Nothing here is built except an untested draft of the mapper (`static/js/importmap.js`).
Owner of this file: Manager. Bots build against it; change it only through the Manager.

## 1. Problem
Adding expenses and bills one at a time is slow. People already have 15 to 45+ rows in a file: an app export (the sample is a Money Manager export), a bank or card statement, a hand-made sheet, a Word table.

## 2. Decision: read any file, no template required
Auto-find the header row, map columns by header name **and** by what the values look like, show a preview the user can correct, remember the mapping per source. A downloadable template exists only as an optional fallback that always parses at 100%.

Why: every source differs (Money Manager, HDFC/Kotak/SBI/ICICI statements, Splitwise, hand-made). Open-source importers and import-UX guidance agree: exact header match, then keyword rules, then value-shape checks; always show the guess and let the user edit it; save the mapping; de-duplicate re-imports; watch sign and day/month traps. Bank export layouts are not formally published and change, so header-row detection is mandatory (HDFC XLS exports carry ~20 junk rows above the header).

## 3. What the sample file proves we must handle
- Duplicate header "Account" (the second is really a number column).
- Two amount columns (`INR` numeric, `Amount` text) that disagree on one row (Cash: 30 vs 0.4).
- Dates both `dd/MM/yyyy` and `dd/MM/yyyy HH:mm:ss`; several rows share one timestamp.
- Account names that don't match the app's ("HDFC Card" vs "HDFC Credit Card", "KOTAK Credit Card", "Cash").
- Category + subcategory ("Food > Eating out") and custom categories ("Ajjima").
- Identical date+amount+note rows that are legitimately separate (de-dupe needs a per-row counter).

## 4. Scope (chosen by Boss)
All four formats in one release: XLSX/XLS, CSV, PDF (text statements), DOCX (tables). Rows go to the **existing review inbox first** (`pending_imports` -> `/imports` -> `approve_import`); nothing counts until accepted. Browser reads the file; Claude is used for messy or scanned files **only after an explicit yes**, and only text (or a page image) is sent, never stored.

## 5. Work breakdown (bots)
| Bot | Work |
|---|---|
| Tabledore (db) | Migration `0020_file_import.sql`: hint columns + `raw_row` on `pending_imports`, `import_batches` table, undo-whole-import RPC, RLS on every new table, source value `file`. Add to `setup_database.sql`. SQL tests. |
| Byte Vader (be) | `static/js/importmap.js` mapper (finish + test the existing draft), readers in `static/js/fileimport.js` (SheetJS, pdf.js, mammoth/zip, all vendored, lazy-loaded), `statementreader.py` + `POST /api/read-statement` (modelled on `/api/read-bill`: signed-in, rate limited, 12 MB cap, demo mode). |
| Sir Pixelot (fe) | Import screen in `templates/imports.html`: drop zone, mapping dropdowns, 10-row preview, warnings, saved mappings, template download, consent dialog, progress and error states. `demo.js` mirror. Service-worker precache. |
| Sherlock Bugs (qa) | Test matrix in section 7. |
| Captain Shipit (ops) | Local gate in section 8, then PR and (after approval) deploy. |

## 6. Mobile and desktop (both are in scope, not one)
- Phone (390) : single-column flow. Pick file via the system picker (Files, Drive, WhatsApp downloads). Mapping shown as stacked cards; preview as a card list, not a wide table; 44px targets; sticky "Add to review" bar above the keyboard.
- Tablet (768) and desktop (1440): drop zone + drag-and-drop, side-by-side mapping and preview table, keyboard shortcuts consistent with the review inbox.
- Both: dark and day themes, no sideways scroll, no clipped text, reduced-motion respected, screen-reader labels, works offline for already-loaded libs.
- Large files: parse in a Web Worker so the phone UI stays responsive (5,000 rows target under 3 s on a mid phone).

## 7. Test matrix (Sherlock Bugs) - "every case"
**Mapper unit tests** (`tests/importmap_test.mjs`, run with node):
- Headers: standard, renamed, upper/lower case, extra spaces, duplicates, blank header cells, 20 junk rows above, title/summary rows below, no header at all.
- Columns: single signed amount; separate Debit/Credit (one blank per row); Dr/Cr suffix; Income/Expense column; transfer rows skipped; balance column ignored; reference numbers not mistaken for amounts; two amount columns that agree and disagree.
- Numbers: `1,23,456.78`, `1,234.50`, `1.234,50`, `Rs.`, `₹`, `INR`, `(123.00)`, `-123`, `123-`, text-stored numbers, formulas, zero and negative-zero rows, huge values (numeric(14,2) limit), more than 2 decimals.
- Dates: dd/mm/yyyy, mm/dd/yyyy, yyyy-mm-dd, `5-Oct-26`, `Oct 5, 2026`, Excel serial, datetime with seconds, 2-digit year, impossible dates (31/02), all-ambiguous files (warn), mixed orders (warn), future dates, year before 1990.
- Text: unicode and Devanagari notes, emoji, apostrophes, 500-char notes (truncated to 200), leading `=`/`+`/`-`/`@` (formula injection stays inert text).
- Structure: empty file, one row, 5,000 rows, multiple sheets (pick or ask), hidden rows/columns, merged cells, password-protected, corrupted, wrong extension, zero-byte, 13 MB (rejected).
- De-dupe: re-import same file = 0 new; overlapping date ranges; legitimate identical rows kept; edited note = new row.

**Readers**: XLSX, XLS (old BIFF), CSV (comma, semicolon, tab, BOM, CRLF/LF, quoted newlines), text PDF (multi-page, header repeated each page, wrapped narration), scanned PDF (-> consent -> Claude fallback), DOCX tables (merged cells, nested tables, no table at all).

**Fixtures** (synthetic, no real personal data): see section 9.

**Security** (extends `security/` reviews): upload never leaves the device without consent; no key in code; CSP unchanged except for vendored scripts; XSS in notes/account/category escaped on preview and review; `/api/read-statement` requires verified sign-in, per-user and per-IP rate limit, size cap, mime allow-list; zip-bomb and PDF-bomb limits; filenames never trusted; no file contents in logs.

**Database** (`tests/run.sh`): RLS between two users on new columns/tables, undo-batch only touches own rows and only pending, duplicate index, constraint failures give a friendly error, migration is idempotent.

**Browser** (Playwright, demo mode): every format end to end with the section 9 files; remap a wrong column; fix date order; consent accept and decline; accept 31 rows; undo the whole import; error states (bad file, offline, signed out); keyboard-only; phone 390 / tablet 768 / desktop 1440; dark and day; plus device emulation for an iPhone and a Pixel profile.

**Regression**: the entire existing suite (section 8) must pass unchanged. Specifically re-verify the email-import review inbox, Snap-a-bill, transactions form, recurring, budgets, goals, net worth, forecast, alerts, Ask, PWA/offline, login isolation.

## 8. Captain Shipit's local gate (required for every sprint, before the PR leaves draft)
1. Fresh clone/branch, clean install (`pip install -r requirements.txt -r requirements-dev.txt`).
2. Python unit scripts: `python3 tests/<name>.py` for every `*_test.py` / `security_test.py` / `billreader_test.py` / `billtext_test.py`.
3. Node mapper/parser tests: `node tests/importmap_test.mjs` (and any new `*.mjs`).
4. SQL: `tests/run.sh` (throwaway local Postgres): all `*.sql` including the new migration test.
5. Browser: `DEMO_MODE=1 gunicorn app:app --bind 127.0.0.1:8099`, then `BASE_URL=http://127.0.0.1:8099 python3 tests/<suite>.py` for **every** suite (smoke, bills, goals, PWA, capture, investments, email/review, phone layout, sprint suites, this sprint's suite).
6. Design check at 390 / 768 / 1440, dark + day, on emulated phone and desktop.
7. Security re-run per `docs/release-checklist.md` (incl. `pip-audit`, secrets scan, CSP, headers, RLS).
8. Regression diff: record pass counts per suite before (on `main`) and after (on the branch); any suite that drops fails the gate.
9. Post the ticked list and counts on the live board log ("Before deploy" entry), then mark the PR ready.
No PR is marked ready, and nothing is deployed, until every box is ticked and Boss approves.

## 9. Local run + sample data for Boss (delivered before release)
- `docs/local-testing.md`: three commands to run the app locally in demo mode, how to open it on a phone on the same Wi-Fi (`--bind 0.0.0.0`, then `http://<laptop-ip>:8099`), and how to reset demo data.
- `tests/fixtures/import/` (generated by `tools/make_import_samples.py`, synthetic data only): `money-manager-style.xlsx` (same 12-column shape as the supplied file), `hdfc-style.xls` (20 junk rows, Withdrawal/Deposit), `kotak-style.csv`, `splitwise.csv`, `signed-amounts.csv`, `no-header.csv`, `statement.pdf` (text), `scanned-statement.pdf` (image, tests the Claude path), `bills.docx` (table), `messy.xlsx` (merged cells, totals, bad dates), and `empty.xlsx` / `corrupt.xlsx` for error paths.
- Boss's own export is not committed to the repo (personal data); the synthetic file mirrors its structure.
- Note: the supplied file must be tested by Boss locally too; Captain Shipit will use a scrubbed copy.

## 10. Rollout
Branch `claude/festive-dijkstra-ymfr2q`, one PR for Sprint 15, draft until the section 8 gate passes. Migration 0020 is applied to Supabase only after Boss approves the "apply migration" card on the board. Feature is behind the Imports page only; no change to existing flows.

## 11. Risks and honest limits
- Arbitrary PDFs cannot be guaranteed; unreadable files must fail loudly with a clear message, never import wrong data.
- Bank column names are inferred from secondary sources; confirm with real exports from each bank (scrubbed) before claiming support.
- Vendored libraries add size; lazy-load on the import page only.
