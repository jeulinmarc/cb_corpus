#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

# `python` stub: traces the args, exit code controllable via PY_EXIT.
STUB=$(mktemp -d)
cat > "$STUB/python" <<'EOF'
#!/bin/bash
echo "PYARGS:$*" >> "$PY_LOG"
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

# T5 — discover consumes DISCOVER_ARGS.
newdir; export DISCOVER_ARGS="--banks us --types A3 --rounds 1"
/app/deploy/run-job.sh discover
grep -q "PYARGS:-m cb_corpus discover --banks us --types A3 --rounds 1 --download" "$PY_LOG" \
  || fail "DISCOVER_ARGS not passed through"
unset DISCOVER_ARGS

# T5b — discover without DISCOVER_ARGS: explicit refusal (no implicit A-F discover).
newdir
if /app/deploy/run-job.sh discover; then fail "discover without DISCOVER_ARGS should have failed"; fi
grep -q "FAILED \[discover\]" "$D/reports/last_run_status" || fail "status != FAILED (discover without DISCOVER_ARGS)"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python should not have run without DISCOVER_ARGS"; fi

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
