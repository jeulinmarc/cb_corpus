#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

# Stub `python` : trace les args, code retour pilotable par PY_EXIT.
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

# T1 — refresh succès : bis-sitemap puis repec, log OK, status OK.
newdir; export AUTOCOMMIT=0
/app/deploy/run-job.sh refresh
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "bis-sitemap non appelé"
grep -q "PYARGS:-m cb_corpus repec --download" "$PY_LOG" || fail "repec non appelé"
grep -q "\[refresh\] OK" "$D/reports/nas_runs.log" || fail "log OK absent"
grep -q "OK \[refresh\]" "$D/reports/last_run_status" || fail "status != OK"

# T2 — refresh échec : status FAILED, exit non nul.
newdir; export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh aurait dû échouer"; fi
grep -q "FAILED \[refresh\]" "$D/reports/last_run_status" || fail "status != FAILED"
unset PY_EXIT

# T3 — lock occupé : refresh skippe (exit 0), python jamais appelé.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 3 ) &
HOLDER=$!
sleep 0.5
/app/deploy/run-job.sh refresh || fail "skip doit sortir en 0"
grep -q "\[refresh\] SKIPPED" "$D/reports/nas_runs.log" || fail "SKIPPED non logué"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python ne devait pas tourner"; fi
wait "$HOLDER"

# T4 — campaign attend le lock puis exécute avec ses args.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 2 ) &
HOLDER=$!
sleep 0.5
START=$(date +%s)
/app/deploy/run-job.sh campaign discover --banks fr --types A3 --download
END=$(date +%s)
[ $((END - START)) -ge 1 ] || fail "campaign n'a pas attendu le lock"
grep -q "PYARGS:-m cb_corpus discover --banks fr --types A3 --download" "$PY_LOG" \
  || fail "args campaign incorrects"
wait "$HOLDER"

# T5 — discover consomme DISCOVER_ARGS.
newdir; export DISCOVER_ARGS="--banks us --types A3 --rounds 1"
/app/deploy/run-job.sh discover
grep -q "PYARGS:-m cb_corpus discover --banks us --types A3 --rounds 1 --download" "$PY_LOG" \
  || fail "DISCOVER_ARGS non transmis"
unset DISCOVER_ARGS

# T5b — discover sans DISCOVER_ARGS : refus explicite (pas de discover A-F implicite).
newdir
if /app/deploy/run-job.sh discover; then fail "discover sans DISCOVER_ARGS aurait dû échouer"; fi
grep -q "FAILED \[discover\]" "$D/reports/last_run_status" || fail "status != FAILED (discover sans DISCOVER_ARGS)"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python ne devait pas tourner sans DISCOVER_ARGS"; fi

# T6 — autocommit appelé après succès (AUTOCOMMIT=1), pas après échec.
newdir; export AUTOCOMMIT=1 AC_LOG="$D/ac.log"
cat > "$D/ac.sh" <<'EOF'
#!/bin/bash
echo "AC:$1" >> "$AC_LOG"
EOF
chmod +x "$D/ac.sh"; export AUTOCOMMIT_BIN="$D/ac.sh"
/app/deploy/run-job.sh refresh
grep -q "AC:refresh" "$AC_LOG" || fail "autocommit non appelé après succès"
export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh aurait dû échouer"; fi
[ "$(grep -c "AC:" "$AC_LOG")" = "1" ] || fail "autocommit appelé après échec"
unset PY_EXIT AUTOCOMMIT_BIN

# T7 — job inconnu : erreur.
newdir
if /app/deploy/run-job.sh bogus; then fail "job inconnu accepté"; fi

# T8 — volume vide : refus (exit 3), REFUSED logué, python jamais appelé.
D=$(mktemp -d); export CB_DATA_DIR="$D" PY_LOG="$D/py.log"
export AUTOCOMMIT=0
set +e; /app/deploy/run-job.sh refresh; rc=$?; set -e
[ "$rc" = "3" ] || fail "volume vide doit sortir en 3 (rc=$rc)"
grep -q "REFUSED" "$D/reports/nas_runs.log" || fail "REFUSED non logué"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python ne devait pas tourner sur volume vide"; fi
# et avec l'override, ça tourne
export CB_ALLOW_EMPTY_DATA=1
/app/deploy/run-job.sh refresh || fail "CB_ALLOW_EMPTY_DATA=1 doit autoriser"
unset CB_ALLOW_EMPTY_DATA

echo "RUN_JOB_OK"
