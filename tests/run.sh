#!/usr/bin/env bash
# Run the Money Tracker SQL tests against a throwaway local PostgreSQL.
# Requires PostgreSQL server binaries (initdb, pg_ctl) and psql.
set -euo pipefail

cd "$(dirname "$0")/.."                      # repo root (so \i migrations/... resolves)
HERE_MIGRATIONS="supabase"

# Find PostgreSQL server binaries (not always on PATH, e.g. Debian/Ubuntu).
BIN="$(dirname "$(command -v psql)")"
if [ ! -x "$BIN/initdb" ]; then
    BIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)"
fi
if [ -z "${BIN:-}" ] || [ ! -x "$BIN/initdb" ]; then
    echo "Could not find PostgreSQL server binaries (initdb). Install postgresql." >&2
    exit 1
fi

TMP="$(mktemp -d)"
DATA="$TMP/data"
PORT=55432
trap '"$BIN/pg_ctl" -D "$DATA" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

"$BIN/initdb" -D "$DATA" -U postgres --auth=trust >/dev/null
"$BIN/pg_ctl" -D "$DATA" -o "-p $PORT -k $TMP -c listen_addresses=''" -l "$TMP/log" start >/dev/null
sleep 1
"$BIN/createdb" -h "$TMP" -p "$PORT" -U postgres app

# Run from the supabase/ dir so the test's \i migrations/... paths resolve.
"$BIN/psql" -h "$TMP" -p "$PORT" -U postgres -d app -q -v ON_ERROR_STOP=1 \
    -c "\\cd $PWD/$HERE_MIGRATIONS" \
    -f "$PWD/tests/rls_and_logic_test.sql"

# ---- End-to-end: scanner output -> pending_imports -> approve -> balances ----
# Fresh database, schema from setup_database.sql, rows from the real scanner.
PSQL=("$BIN/psql" -h "$TMP" -p "$PORT" -U postgres -q -v ON_ERROR_STOP=1)
"$BIN/createdb" -h "$TMP" -p "$PORT" -U postgres e2e
PAYLOAD="$(python3 tests/e2e_payload.py tests/fixtures)"
"${PSQL[@]}" -d e2e -v payload="$PAYLOAD" -f "$PWD/tests/e2e_import_test.sql"

# ---- Double-approve race: two sessions approve the same import at once -------
# Session 1 approves and holds its transaction open; session 2 must wait on the
# row lock and then be refused with 55000 — never a second transaction.
APPROVE="set role app_user; set request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
  select public.approve_import(
    (select id from public.pending_imports where source_message_id = 'race-1'),
    (select id from public.accounts where name = 'SBI 000001'),
    (select id from public.categories where name = 'Food' and kind = 'expense'));"
"${PSQL[@]}" -d e2e -c "begin; $APPROVE select pg_sleep(2); commit;" >"$TMP/race1.out" 2>&1 &
RACE1=$!
sleep 0.7
set +e
"${PSQL[@]}" -d e2e -c "$APPROVE" >"$TMP/race2.out" 2>&1
RACE2=$?
wait "$RACE1"; RACE1=$?
set -e
N="$("${PSQL[@]}" -d e2e -At -c "select count(*) from public.transactions where source_message_id = 'race-1'")"
if [ "$RACE1" -ne 0 ] || [ "$RACE2" -eq 0 ] || ! grep -q "already approved" "$TMP/race2.out" || [ "$N" != 1 ]; then
    echo "DOUBLE-APPROVE RACE FAILED: session1=$RACE1 session2=$RACE2 transactions=$N" >&2
    cat "$TMP/race1.out" "$TMP/race2.out" >&2
    exit 1
fi
echo "  double-approve race: second session refused (55000), 1 transaction"
