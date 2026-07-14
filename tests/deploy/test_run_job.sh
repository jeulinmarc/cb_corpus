#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

# `python` stub: traces the args, exit code controllable via PY_EXIT.
STUB=$(mktemp -d)
cat > "$STUB/python" <<'EOF'
#!/bin/bash
echo "PYARGS:$*" >> "$PY_LOG"
if [ "$*" = "-m cb_corpus list-banks" ]; then
  printf 'aa   Bank Aa                              aa.example\n'
  printf 'bb   Bank Bb                              bb.example  (verify domain)\n'
  printf 'cc   Bank Cc                              cc.example\n'
fi
case "$*" in
  *"${PY_FAIL_MATCH:-@@none@@}"*) exit 1 ;;
esac
if [ -n "${PY_CONC_DIR:-}" ]; then
  (
    flock 8
    n=$(( $(cat "$PY_CONC_DIR/cur" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$PY_CONC_DIR/cur"
    m=$(cat "$PY_CONC_DIR/max" 2>/dev/null || echo 0)
    if [ "$n" -gt "$m" ]; then echo "$n" > "$PY_CONC_DIR/max"; fi
  ) 8>"$PY_CONC_DIR/lock"
  sleep "${PY_SLEEP:-0.3}"
  (
    flock 8
    echo "$(( $(cat "$PY_CONC_DIR/cur") - 1 ))" > "$PY_CONC_DIR/cur"
  ) 8>"$PY_CONC_DIR/lock"
fi
exit "${PY_EXIT:-0}"
EOF
chmod +x "$STUB/python"
export PATH="$STUB:$PATH"
export CB_APP_DIR=/app

newdir() { D=$(mktemp -d); export CB_DATA_DIR="$D" PY_LOG="$D/py.log"; mkdir -p "$D/manifest"; echo '{}' > "$D/manifest/stub.jsonl"; }

# T1 — refresh success: bis-sitemap then repec, log OK, status OK.
newdir; export AUTOCOMMIT=0
/app/deploy/run-job.sh refresh
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "bis-sitemap not called"
grep -q "PYARGS:-m cb_corpus repec --download" "$PY_LOG" || fail "repec not called"
grep -q "\[refresh\] OK" "$D/reports/nas_runs.log" || fail "log OK missing"
grep -q "OK \[refresh\]" "$D/reports/last_run_status" || fail "status != OK"

# T2 — refresh failure: status FAILED, non-zero exit.
newdir; export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh should have failed"; fi
grep -q "FAILED \[refresh\]" "$D/reports/last_run_status" || fail "status != FAILED"
unset PY_EXIT

# T3 — lock busy: refresh skips (exit 0), python never called.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 3 ) &
HOLDER=$!
sleep 0.5
/app/deploy/run-job.sh refresh || fail "skip must exit 0"
grep -q "\[refresh\] SKIPPED" "$D/reports/nas_runs.log" || fail "SKIPPED not logged"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python should not have run"; fi
wait "$HOLDER"

# T4 — campaign waits for the lock then runs with its args.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 2 ) &
HOLDER=$!
sleep 0.5
START=$(date +%s)
/app/deploy/run-job.sh campaign discover --banks fr --types A3 --download
END=$(date +%s)
[ $((END - START)) -ge 1 ] || fail "campaign did not wait for the lock"
grep -q "PYARGS:-m cb_corpus discover --banks fr --types A3 --download" "$PY_LOG" \
  || fail "incorrect campaign args"
wait "$HOLDER"

# T5 — discover: explicit bank list, explicit types → one call per bank + per-bank logs.
newdir; export DISCOVER_BANKS="us,ecb" DISCOVER_TYPES="A3" DISCOVER_ROUNDS=1
/app/deploy/run-job.sh discover
grep -q "PYARGS:-m cb_corpus discover --banks us --types A3 --rounds 1 --download" "$PY_LOG" \
  || fail "us discover call wrong"
grep -q "PYARGS:-m cb_corpus discover --banks ecb --types A3 --rounds 1 --download" "$PY_LOG" \
  || fail "ecb discover call wrong"
DAY=$(date -u +%Y-%m-%d)
[ -f "$D/reports/discover/$DAY/us.log" ] || fail "per-bank log us missing"
[ -f "$D/reports/discover/$DAY/ecb.log" ] || fail "per-bank log ecb missing"
grep -q "\[discover\] OK 2/2" "$D/reports/nas_runs.log" || fail "OK summary missing"
grep -q "OK 2/2 \[discover\]" "$D/reports/last_run_status" || fail "status summary missing"
unset DISCOVER_BANKS DISCOVER_TYPES DISCOVER_ROUNDS

# T5b — discover without DISCOVER_BANKS: explicit refusal (no implicit all-banks discover).
newdir
if /app/deploy/run-job.sh discover; then fail "discover without DISCOVER_BANKS should have failed"; fi
grep -q "FAILED \[discover\]" "$D/reports/last_run_status" || fail "status != FAILED (no DISCOVER_BANKS)"
if [ -f "$PY_LOG" ] && grep -q "PYARGS:-m cb_corpus discover" "$PY_LOG"; then
  fail "python discover should not have run without DISCOVER_BANKS"
fi

# T5c — DISCOVER_BANKS=all resolves via list-banks; DISCOVER_TYPES=full omits --types.
newdir; export DISCOVER_BANKS="all" DISCOVER_TYPES="full"
/app/deploy/run-job.sh discover
grep -q "PYARGS:-m cb_corpus list-banks" "$PY_LOG" || fail "list-banks not called for all"
grep -q "PYARGS:-m cb_corpus discover --banks aa --rounds 1 --download" "$PY_LOG" \
  || fail "aa call wrong (types must be omitted when full)"
grep -q "PYARGS:-m cb_corpus discover --banks bb --rounds 1 --download" "$PY_LOG" || fail "bb call missing"
grep -q "PYARGS:-m cb_corpus discover --banks cc --rounds 1 --download" "$PY_LOG" || fail "cc call missing"
grep -q "\[discover\] OK 3/3" "$D/reports/nas_runs.log" || fail "OK 3/3 summary missing"
unset DISCOVER_BANKS DISCOVER_TYPES

# T5d — partial failure: PARTIAL summary, exit 0, autocommit still runs.
newdir; export AUTOCOMMIT=1 AC_LOG="$D/ac.log"
cat > "$D/ac.sh" <<'EOF'
#!/bin/bash
echo "AC:$1" >> "$AC_LOG"
EOF
chmod +x "$D/ac.sh"; export AUTOCOMMIT_BIN="$D/ac.sh"
export DISCOVER_BANKS="aa,bb,cc" PY_FAIL_MATCH="--banks bb"
/app/deploy/run-job.sh discover || fail "partial failure must exit 0"
grep -q "\[discover\] PARTIAL 2/3 FAILED: bb" "$D/reports/nas_runs.log" || fail "PARTIAL summary missing"
grep -q "PARTIAL 2/3 FAILED: bb \[discover\]" "$D/reports/last_run_status" || fail "PARTIAL status missing"
grep -q "AC:discover" "$AC_LOG" || fail "autocommit not called on PARTIAL"
unset PY_FAIL_MATCH AUTOCOMMIT_BIN DISCOVER_BANKS; export AUTOCOMMIT=0

# T5e — all banks failed: FAILED status, non-zero exit.
newdir; export DISCOVER_BANKS="aa,bb" PY_EXIT=1
if /app/deploy/run-job.sh discover; then fail "all-failed discover must exit non-zero"; fi
grep -q "\[discover\] FAILED 0/2 banks: aa,bb" "$D/reports/nas_runs.log" || fail "all-failed summary missing"
grep -q "FAILED \[discover\]" "$D/reports/last_run_status" || fail "status != FAILED (all banks)"
unset PY_EXIT DISCOVER_BANKS

# T6 — autocommit called after success (AUTOCOMMIT=1), not after failure.
newdir; export AUTOCOMMIT=1 AC_LOG="$D/ac.log"
cat > "$D/ac.sh" <<'EOF'
#!/bin/bash
echo "AC:$1" >> "$AC_LOG"
EOF
chmod +x "$D/ac.sh"; export AUTOCOMMIT_BIN="$D/ac.sh"
/app/deploy/run-job.sh refresh
grep -q "AC:refresh" "$AC_LOG" || fail "autocommit not called after success"
export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh should have failed"; fi
[ "$(grep -c "AC:" "$AC_LOG")" = "1" ] || fail "autocommit called after failure"
unset PY_EXIT AUTOCOMMIT_BIN

# T7 — unknown job: error.
newdir
if /app/deploy/run-job.sh bogus; then fail "unknown job accepted"; fi

# T8 — empty volume: refused (exit 3), REFUSED logged, python never called.
D=$(mktemp -d); export CB_DATA_DIR="$D" PY_LOG="$D/py.log"
export AUTOCOMMIT=0
set +e; /app/deploy/run-job.sh refresh; rc=$?; set -e
[ "$rc" = "3" ] || fail "empty volume must exit 3 (rc=$rc)"
grep -q "REFUSED" "$D/reports/nas_runs.log" || fail "REFUSED not logged"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python should not have run on an empty volume"; fi
# and with the override, it runs
export CB_ALLOW_EMPTY_DATA=1
/app/deploy/run-job.sh refresh || fail "CB_ALLOW_EMPTY_DATA=1 must allow it"
unset CB_ALLOW_EMPTY_DATA

echo "RUN_JOB_OK"
