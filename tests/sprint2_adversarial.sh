#!/usr/bin/env bash
# Sprint 2 release gate (Sherlock Bugs). Written against
# docs/sprint-2-contract.md; expect failures until the builds land.
#
#   bash tests/sprint2_adversarial.sh                 # SQL, races, Python
#   BASE_URL=http://127.0.0.1:8099 bash tests/sprint2_adversarial.sh   # + UI
#
# Parts:
#   1. tests/sprint2_adversarial_recurring.sql  dates, posting, RLS, detection (26 cases)
#   2. double-posting races (5 scenarios, real concurrent sessions)
#   3. tests/sprint2_adversarial_detection.py   Python reference bot/recurring.py
#   4. date parity: recurring_occurrence() vs bot.recurring.occurrence() on a grid
#   5. tests/sprint2_adversarial_ui.py          only when BASE_URL is set (demo mode)
# Runs every part even if an earlier one fails; exits non-zero if any failed.
# Needs PostgreSQL server binaries and must not run as root (initdb).
set -uo pipefail
cd "$(dirname "$0")/.."

BIN="$(dirname "$(command -v psql)")"
[ -x "$BIN/initdb" ] || BIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
[ -x "${BIN:-}/initdb" ] || { echo "no PostgreSQL server binaries (initdb)" >&2; exit 2; }

TMP="$(mktemp -d)"; DATA="$TMP/data"; PORT=55433
trap '"$BIN/pg_ctl" -D "$DATA" stop -m immediate >/dev/null 2>&1; rm -rf "$TMP"' EXIT
"$BIN/initdb" -D "$DATA" -U postgres --auth=trust >/dev/null
"$BIN/pg_ctl" -D "$DATA" -o "-p $PORT -k $TMP -c listen_addresses=''" -l "$TMP/log" start >/dev/null
PSQL=("$BIN/psql" -h "$TMP" -p "$PORT" -U postgres -q -X)
FAILED=()

# ---- 1. SQL gate ---------------------------------------------------------------
echo "== 1. SQL gate (dates, posting, RLS, detection)"
"$BIN/createdb" -h "$TMP" -p "$PORT" -U postgres gate
"${PSQL[@]}" -d gate -v date_cases="$(cat tests/fixtures/sprint2/date_cases.json)" \
    -v detection="$(cat tests/fixtures/sprint2/detection.json)" \
    -f tests/sprint2_adversarial_recurring.sql >"$TMP/sql.out" 2>&1
grep -v "NOTICE\|^$" "$TMP/sql.out" | sed 's/^/   /'
if ! grep -q "SPRINT2 SQL GATE: 26 passed, 0 failed, 26 recorded" "$TMP/sql.out"; then FAILED+=("sql gate"); fi

# ---- 2. Double-posting races -----------------------------------------------------
echo "== 2. Double-posting races"
"$BIN/createdb" -h "$TMP" -p "$PORT" -U postgres race
if ! "${PSQL[@]}" -d race -v ON_ERROR_STOP=1 -f tests/sprint2_adversarial_race_setup.sql >"$TMP/race-setup.out" 2>&1; then
    sed 's/^/   /' "$TMP/race-setup.out" | grep -v NOTICE | tail -5
    FAILED+=("race setup")
else
    AS_A="set role app_user; set request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';"
    AS_B="set role app_user; set request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';"
    BILL() { echo "(select id from public.recurring where name = '$1')"; }
    q() { "${PSQL[@]}" -d race -v ON_ERROR_STOP=1 -At -c "$1"; }
    # Invariant for a bill: rows posted == next_seq, seqs are exactly 0..n-1, and
    # next_due == recurring_occurrence(start, freq, next_seq).
    check_bill() {   # name expected_rows label
        local got
        got="$(q "select count(*) || '/' || count(distinct t.recurring_seq) || '/' || r.next_seq || '/' ||
                  coalesce(max(t.recurring_seq), -1) || '/' ||
                  (r.next_due = public.recurring_occurrence(r.start_date, r.frequency, r.next_seq))
                  from public.recurring r left join public.transactions t on t.recurring_id = r.id
                  where r.name = '$1' group by r.id")"
        local want="$2/$2/$2/$(($2 - 1))/true"
        if [ "$got" = "$want" ]; then echo "   PASS $3 (rows/distinct/next_seq/max_seq/next_due_ok = $got)"
        else echo "   FAIL $3: want $want got $got"; FAILED+=("race: $3"); fi
    }
    hold() {  # run SQL in a transaction that stays open 2s, in the background
        "${PSQL[@]}" -d race -c "begin; $1 select pg_sleep(2); commit;" >>"$TMP/race.out" 2>&1 &
    }

    # a) post_due vs post_due
    hold "$AS_A select public.post_due_recurring('2026-06-30');"; P=$!
    sleep 0.5; "${PSQL[@]}" -d race -c "$AS_A select public.post_due_recurring('2026-06-30');" >>"$TMP/race.out" 2>&1
    wait $P
    check_bill race-post-post 6 "post_due twice concurrently: 6 occurrences, no duplicates"

    # b) Mark paid holds the row, post_due arrives: paid seq 0 + seqs 1..5 auto
    q "$AS_A update public.recurring set auto_post = (name = 'race-pay-then-post');" >/dev/null
    hold "$AS_A select public.mark_recurring_paid($(BILL race-pay-then-post), '2026-02-01');"; P=$!
    sleep 0.5; "${PSQL[@]}" -d race -c "$AS_A select public.post_due_recurring('2026-06-30');" >>"$TMP/race.out" 2>&1
    wait $P
    check_bill race-pay-then-post 6 "Mark paid racing post_due: paid seq not re-posted"

    # c) post_due holds, Mark paid arrives: 6 auto + 1 paid (the July occurrence)
    q "$AS_A update public.recurring set auto_post = (name = 'race-post-then-pay');" >/dev/null
    hold "$AS_A select public.post_due_recurring('2026-06-30');"; P=$!
    sleep 0.5; "${PSQL[@]}" -d race -c "$AS_A select public.mark_recurring_paid($(BILL race-post-then-pay), '2026-06-30');" >>"$TMP/race.out" 2>&1
    wait $P
    check_bill race-post-then-pay 7 "post_due racing Mark paid: no seq posted twice"

    # d) two Mark paid at once (double tap): two different seqs, never the same one
    hold "$AS_A select public.mark_recurring_paid($(BILL race-pay-pay));"; P=$!
    sleep 0.5; "${PSQL[@]}" -d race -c "$AS_A select public.mark_recurring_paid($(BILL race-pay-pay));" >>"$TMP/race.out" 2>&1
    wait $P
    check_bill race-pay-pay 2 "two Mark paid at once: seqs 0 and 1, no duplicate"

    # e) storm: 8 sessions call post_due at once (A x6, B x2), no holds
    q "$AS_A update public.recurring set auto_post = (name = 'race-storm');" >/dev/null
    PIDS=()
    for i in 1 2 3 4 5 6; do "${PSQL[@]}" -d race -c "$AS_A select public.post_due_recurring('2026-06-30');" >>"$TMP/race.out" 2>&1 & PIDS+=($!); done
    for i in 1 2; do "${PSQL[@]}" -d race -c "$AS_B select public.post_due_recurring('2026-06-30');" >>"$TMP/race.out" 2>&1 & PIDS+=($!); done
    wait "${PIDS[@]}"
    check_bill race-storm 57 "8 concurrent post_due calls: 57 weekly occurrences exactly once"
    check_bill race-b 6 "B's concurrent calls post only B's bill"
    if grep -q "ERROR" "$TMP/race.out"; then
        echo "   FAIL a racing session errored instead of waiting/advancing:"; grep ERROR "$TMP/race.out" | sort | uniq -c | sed 's/^/     /'
        FAILED+=("race: session errors")
    fi
fi

# ---- 3. Python reference -----------------------------------------------------------
echo "== 3. Python reference (bot/recurring.py)"
python3 tests/sprint2_adversarial_detection.py 2>&1 | grep -v "^PASS" | sed 's/^/   /'
[ "${PIPESTATUS[0]}" -eq 0 ] || FAILED+=("python reference")

# ---- 4. Date parity SQL vs Python ------------------------------------------------
echo "== 4. Date parity: recurring_occurrence vs bot.recurring.occurrence"
GRID="$(python3 - <<'EOF' 2>/dev/null
import json, sys
from datetime import date, timedelta
sys.path.insert(0, ".")
from bot.recurring import occurrence
out = []
d = date(2023, 1, 1)
while d <= date(2025, 12, 31):
    if d.day >= 27 or d.day in (1, 15):
        for f in ("weekly", "monthly", "quarterly", "yearly"):
            for n in (0, 1, 2, 3, 5, 11, 12, 13, 25, 48):
                out.append([d.isoformat(), f, n, str(occurrence(d, f, n))[:10]])
    d += timedelta(days=1)
print(json.dumps(out))
EOF
)"
if [ -z "$GRID" ]; then echo "   FAIL bot.recurring.occurrence unavailable"; FAILED+=("date parity")
else
    RES="$("${PSQL[@]}" -d race -At -v grid="$GRID" -c "
      select count(*) || ' cases, ' || count(*) filter (where public.recurring_occurrence(s::date, f, n) is distinct from e::date) || ' mismatches'
        from json_to_recordset((select json_agg(json_build_object('s', x->>0, 'f', x->>1, 'n', (x->>2)::int, 'e', x->>3))
                                  from json_array_elements(:'grid'::json) x)) as t(s text, f text, n int, e text);" 2>&1)"
    echo "   $RES"
    case "$RES" in *" 0 mismatches") ;; *) FAILED+=("date parity");; esac
fi

# ---- 5. UI ---------------------------------------------------------------------------
if [ -n "${BASE_URL:-}" ]; then
    echo "== 5. UI (demo mode at $BASE_URL)"
    python3 tests/sprint2_adversarial_ui.py 2>&1 | grep -v "^\s*\[PASS\]\|agent-proxy\|^- \|^For details" | sed 's/^/   /'
    [ "${PIPESTATUS[0]}" -eq 0 ] || FAILED+=("ui")
else
    echo "== 5. UI skipped (set BASE_URL to a DEMO_MODE=1 server)"
fi

echo
if [ ${#FAILED[@]} -eq 0 ]; then echo "SPRINT 2 GATE: ALL PARTS PASSED"; exit 0; fi
printf 'SPRINT 2 GATE FAILED: %s\n' "$(IFS=';'; echo "${FAILED[*]}")"; exit 1
