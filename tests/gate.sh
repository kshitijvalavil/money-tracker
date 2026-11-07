#!/usr/bin/env bash
# Captain Shipit's local release gate. Runs EVERY suite the repo has and prints
# one line per suite plus a summary, so a branch can be compared with main.
#
#   bash tests/gate.sh                 # everything
#   bash tests/gate.sh unit            # Python + Node unit tests only
#   bash tests/gate.sh sql             # database tests only
#   bash tests/gate.sh ui              # browser suites only (starts its own demo server)
#   GATE_OUT=gate-main.txt bash tests/gate.sh   # also write the results to a file
#
# Exit code is non-zero if any suite failed. Browser suites need Playwright and
# a Chromium (CHROME_PATH, default /opt/pw-browsers/chromium).
set -uo pipefail
cd "$(dirname "$0")/.."

WHAT="${1:-all}"
PORT="${GATE_PORT:-8099}"
export BASE_URL="http://127.0.0.1:$PORT"
RESULTS=()
FAILED=0
LOGDIR="$(mktemp -d)"

run() {   # run <label> <command...>
    local label="$1"; shift
    local log="$LOGDIR/$(echo "$label" | tr -c 'A-Za-z0-9' '_').log"
    local start=$SECONDS
    if "$@" >"$log" 2>&1; then
        local status=PASS
    else
        local status=FAIL; FAILED=$((FAILED + 1))
    fi
    # Count PASS/FAIL lines the suites print (most print "[FAIL]" or "PASS"/"ok").
    local fails passes
    fails=$(grep -cE '\[FAIL\]|^FAIL|AssertionError|Traceback' "$log" || true)
    passes=$(grep -cE 'PASS|^ok|\bok\b' "$log" || true)
    RESULTS+=("$(printf '%-4s %-46s %4ss  pass-lines=%-4s fail-lines=%s' "$status" "$label" $((SECONDS - start)) "$passes" "$fails")")
    echo "${RESULTS[-1]}"
    if [ "$status" = FAIL ]; then tail -15 "$log" | sed 's/^/      /'; fi
}

if [ "$WHAT" = all ] || [ "$WHAT" = unit ]; then
    for f in tests/test_parsers.py tests/test_scanner.py tests/test_scanner_adversarial.py \
             tests/test_channel_parsers.py tests/test_recurring.py tests/billreader_test.py \
             tests/billtext_test.py tests/security_test.py tests/test_review.py \
             tests/statementreader_test.py tests/xlsreader_test.py tests/voiceparse_api_test.py; do
        [ -f "$f" ] && run "unit: $f" python3 "$f"
    done
    run "unit: scanner dry run" python3 -m bot.scanner --from-dir tests/fixtures
    for f in tests/*.mjs; do
        [ -f "$f" ] && run "node: $f" node "$f"
    done
fi

if [ "$WHAT" = all ] || [ "$WHAT" = sql ]; then
    # PostgreSQL refuses to start as root (e.g. in a container): run as the postgres user.
    if [ "$(id -u)" = 0 ] && id postgres >/dev/null 2>&1; then
        SQLRUN=(su postgres -s /bin/bash -c)
        run "sql: tests/run.sh" "${SQLRUN[@]}" "cd '$PWD' && env -u BASE_URL bash tests/run.sh"
        run "sql: tests/sprint2_adversarial.sh" "${SQLRUN[@]}" "cd '$PWD' && env -u BASE_URL bash tests/sprint2_adversarial.sh"
    else
        run "sql: tests/run.sh" env -u BASE_URL bash tests/run.sh
        run "sql: tests/sprint2_adversarial.sh" env -u BASE_URL bash tests/sprint2_adversarial.sh
    fi
fi

if [ "$WHAT" = all ] || [ "$WHAT" = ui ]; then
    DEMO_MODE=1 gunicorn app:app --bind "127.0.0.1:$PORT" --workers 2 >"$LOGDIR/gunicorn.log" 2>&1 &
    GPID=$!
    trap 'kill $GPID 2>/dev/null' EXIT
    for i in $(seq 1 30); do curl -fsS -o /dev/null "$BASE_URL/" && break; sleep 1; done
    for f in tests/smoke_test.py tests/bills_ui_test.py tests/sprint2_adversarial_ui.py \
             tests/sprint3_ui_test.py tests/sprint4_pwa_test.py tests/sprint5_capture_test.py \
             tests/investments_ui_test.py tests/sprint6_budgets_goals_test.py \
             tests/sprint6_email_ui_test.py tests/sprint6_hotfix_test.py tests/phone_layout_test.py \
             tests/sprint9_review_inbox_test.py tests/sprint10_flex_test.py \
             tests/sprint11_networth_test.py tests/sprint12_ui_test.py tests/sprint13_ui_test.py \
             tests/sprint15_import_ui_test.py tests/sprint16_voice_ui_test.py; do
        [ -f "$f" ] && run "ui: $f" python3 "$f"
    done
fi

echo
echo "==== gate summary: $((${#RESULTS[@]} - FAILED)) passed, $FAILED failed, logs in $LOGDIR ===="
if [ -n "${GATE_OUT:-}" ]; then printf '%s\n' "${RESULTS[@]}" >"$GATE_OUT"; fi
[ "$FAILED" -eq 0 ]
