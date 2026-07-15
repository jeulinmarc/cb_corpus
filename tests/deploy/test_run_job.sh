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
  printf '\n'
  printf '3 banks\n'
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

# T1 — sync success: bis-sitemap, then repec, then per-bank native discover, in order.
newdir; export AUTOCOMMIT=0 DISCOVER_BANKS="us,ecb" DISCOVER_TYPES="A3" DISCOVER_ROUNDS=1
/app/deploy/run-job.sh sync
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "bis-sitemap not called"
grep -q "PYARGS:-m cb_corpus repec --download" "$PY_LOG" || fail "repec not called"
grep -q "PYARGS:-m cb_corpus discover --banks us --types A3 --rounds 1 --native-only --download" "$PY_LOG" \
  || fail "us native discover call wrong"
grep -q "PYARGS:-m cb_corpus discover --banks ecb --types A3 --rounds 1 --native-only --download" "$PY_LOG" \
  || fail "ecb native discover call wrong"
BIS_LINE=$(grep -n "bis-sitemap" "$PY_LOG" | cut -d: -f1 | head -1)
REPEC_LINE=$(grep -n "cb_corpus repec" "$PY_LOG" | cut -d: -f1 | head -1)
DISC_LINE=$(grep -n "cb_corpus discover" "$PY_LOG" | cut -d: -f1 | head -1)
[ "$BIS_LINE" -lt "$REPEC_LINE" ] && [ "$REPEC_LINE" -lt "$DISC_LINE" ] || fail "sync phases out of order"
grep -q "\[sync\] catalogs OK" "$D/reports/nas_runs.log" || fail "catalogs OK not logged"
grep -q "\[sync\] OK 2/2" "$D/reports/nas_runs.log" || fail "native summary missing"
grep -q "OK 2/2 \[sync\]" "$D/reports/last_run_status" || fail "status summary missing"
DAY=$(date -u +%Y-%m-%d)
[ -f "$D/reports/discover/$DAY/us.log" ] || fail "per-bank log missing"
unset DISCOVER_BANKS DISCOVER_TYPES DISCOVER_ROUNDS

# T2 — catalog failure aborts sync: no native phase, FAILED status, non-zero exit.
newdir; export DISCOVER_BANKS="us" PY_FAIL_MATCH="bis-sitemap"
if /app/deploy/run-job.sh sync; then fail "sync must fail when a catalog phase fails"; fi
grep -q "FAILED \[sync\]" "$D/reports/last_run_status" || fail "status != FAILED (catalog)"
if grep -q "PYARGS:-m cb_corpus discover" "$PY_LOG"; then fail "native phase must not run after catalog failure"; fi
unset PY_FAIL_MATCH DISCOVER_BANKS

# T3 — lock busy: second sync skips (exit 0), python never called.
newdir; export DISCOVER_BANKS="us"
( exec 9>"$D/.cb.lock"; flock 9; sleep 3 ) &
HOLDER=$!
sleep 0.5
/app/deploy/run-job.sh sync || fail "lock-busy sync must exit 0"
grep -q "\[sync\] SKIPPED (lock busy)" "$D/reports/nas_runs.log" || fail "SKIPPED not logged"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python should not have run"; fi
wait "$HOLDER"; unset DISCOVER_BANKS

# T4 — campaign waits for the lock then runs with its args (unchanged from before).
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 2 ) &
HOLDER=$!
sleep 0.5
START=$(date +%s)
/app/deploy/run-job.sh campaign discover --banks fr --native-only --download
END=$(date +%s)
[ $((END - START)) -ge 1 ] || fail "campaign did not wait for the lock"
grep -q "PYARGS:-m cb_corpus discover --banks fr --native-only --download" "$PY_LOG" \
  || fail "incorrect campaign args"
wait "$HOLDER"

# T5 — DISCOVER_BANKS unset: sync refused before any python call.
newdir
if /app/deploy/run-job.sh sync; then fail "sync without DISCOVER_BANKS should have failed"; fi
grep -q "FAILED \[sync\]" "$D/reports/last_run_status" || fail "status != FAILED (no DISCOVER_BANKS)"
if [ -f "$PY_LOG" ] && grep -q "cb_corpus discover" "$PY_LOG"; then
  fail "native discover should not run without DISCOVER_BANKS"
fi

# T5b — refusal happens BEFORE catalogs (cheap fail: no bis-sitemap either).
if [ -f "$PY_LOG" ] && grep -q "bis-sitemap" "$PY_LOG"; then
  fail "catalogs should not run without DISCOVER_BANKS"
fi

# T6 — all resolution via list-banks with footer filtered; full omits --types.
newdir; export DISCOVER_BANKS="all" DISCOVER_TYPES="full"
/app/deploy/run-job.sh sync
grep -q "PYARGS:-m cb_corpus list-banks" "$PY_LOG" || fail "list-banks not called"
grep -q "PYARGS:-m cb_corpus discover --banks aa --rounds 1 --native-only --download" "$PY_LOG" \
  || fail "aa call wrong (footer not filtered or --types not omitted)"
grep -q "\[sync\] OK 3/3" "$D/reports/nas_runs.log" || fail "OK 3/3 missing (footer leaked into totals)"
unset DISCOVER_BANKS DISCOVER_TYPES

# T7 — native partial failure: PARTIAL, exit 0, autocommit runs.
newdir; export AUTOCOMMIT=1 AC_LOG="$D/ac.log"
cat > "$D/ac.sh" <<'EOF'
#!/bin/bash
echo "AC:$1" >> "$AC_LOG"
EOF
chmod +x "$D/ac.sh"; export AUTOCOMMIT_BIN="$D/ac.sh"
export DISCOVER_BANKS="aa,bb,cc" PY_FAIL_MATCH="--banks bb"
/app/deploy/run-job.sh sync || fail "native partial failure must exit 0"
grep -q "\[sync\] PARTIAL 2/3 FAILED: bb" "$D/reports/nas_runs.log" || fail "PARTIAL summary missing"
grep -q "AC:sync" "$AC_LOG" || fail "autocommit not called on PARTIAL"
unset PY_FAIL_MATCH AUTOCOMMIT_BIN DISCOVER_BANKS; export AUTOCOMMIT=0

# T8 — all native banks failed: FAILED, non-zero exit.
newdir; export DISCOVER_BANKS="aa,bb" PY_FAIL_MATCH="cb_corpus discover"
if /app/deploy/run-job.sh sync; then fail "all-native-failed sync must exit non-zero"; fi
grep -q "\[sync\] FAILED 0/2 banks: aa,bb" "$D/reports/nas_runs.log" || fail "all-failed summary missing"
unset PY_FAIL_MATCH DISCOVER_BANKS

# T9 — parallelism bounded by DISCOVER_WORKERS (concurrency counter in stub).
newdir; export DISCOVER_BANKS="b1,b2,b3,b4,b5,b6" DISCOVER_WORKERS=2
export PY_CONC_DIR="$D/conc" PY_SLEEP=0.3
mkdir -p "$PY_CONC_DIR"
/app/deploy/run-job.sh sync
MAXC=$(cat "$PY_CONC_DIR/max")
[ "$MAXC" -le 2 ] || fail "parallelism exceeded DISCOVER_WORKERS (max=$MAXC)"
[ "$MAXC" -ge 2 ] || fail "no parallelism observed (max=$MAXC)"
unset DISCOVER_BANKS DISCOVER_WORKERS PY_CONC_DIR PY_SLEEP

# T10 — DISCOVER_BANK_TIMEOUT kills a wedged bank; both timed out => FAILED.
newdir; export DISCOVER_BANKS="aa,bb" DISCOVER_BANK_TIMEOUT=1
export PY_CONC_DIR="$D/conc" PY_SLEEP=5
mkdir -p "$PY_CONC_DIR"
START=$(date +%s)
if /app/deploy/run-job.sh sync; then fail "all-timed-out sync must exit non-zero"; fi
END=$(date +%s)
[ $((END - START)) -lt 20 ] || fail "banks were not killed by DISCOVER_BANK_TIMEOUT"
grep -q "\[sync\] FAILED 0/2 banks: aa,bb" "$D/reports/nas_runs.log" || fail "timed-out banks not counted"
unset DISCOVER_BANKS DISCOVER_BANK_TIMEOUT PY_CONC_DIR PY_SLEEP

# T11 — retired job names are rejected.
newdir
for j in refresh discover bogus; do
  if /app/deploy/run-job.sh "$j"; then fail "job '$j' should be rejected"; fi
done

# T12 — empty volume: REFUSED (exit 3), python never called; override works.
D=$(mktemp -d); export CB_DATA_DIR="$D" PY_LOG="$D/py.log" DISCOVER_BANKS="us"
set +e; /app/deploy/run-job.sh sync; rc=$?; set -e
[ "$rc" = "3" ] || fail "empty volume must exit 3 (rc=$rc)"
grep -q "REFUSED" "$D/reports/nas_runs.log" || fail "REFUSED not logged"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python must not run on empty volume"; fi
export CB_ALLOW_EMPTY_DATA=1
/app/deploy/run-job.sh sync || fail "CB_ALLOW_EMPTY_DATA=1 must allow it"
unset CB_ALLOW_EMPTY_DATA DISCOVER_BANKS

echo "RUN_JOB_OK"
