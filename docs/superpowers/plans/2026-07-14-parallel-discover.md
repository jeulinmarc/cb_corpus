# Parallel Nightly Discover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `discover` job in `deploy/run-job.sh` crawl all 63 banks nightly, N banks in parallel, with per-bank logs, a partial-failure summary, and a blocking lock — per the approved spec `docs/superpowers/specs/2026-07-14-parallel-discover-design.md`.

**Architecture:** Shell-level fan-out inside the existing `run-job.sh` (one `python -m cb_corpus discover --banks <code>` process per bank via `xargs -P`), a single global lock and a single autocommit per run, new `DISCOVER_*` env variables replacing `DISCOVER_ARGS`. No Python changes.

**Tech Stack:** bash, flock, xargs; tests are the existing bash suite `tests/deploy/test_run_job.sh` run inside the Docker image via `tests/deploy/run_tests.sh`.

## Global Constraints

- Everything committed on this branch merges to master: **English only** (comments, log messages, docs).
- **Never** add `Co-Authored-By: Claude` (or "Generated with Claude") to commits.
- **No real infra values** (IPs, hostnames, real `/mnt` paths, UIDs) in any committed file — placeholders only, as in the existing `deploy/*.example.yml`.
- Log/status strings are test-grepped — when changing one, update script and test in the same task.
- Existing behaviors that must NOT change: `refresh` job content and its `flock -n`; `campaign` blocking lock; empty-volume `REFUSED` guard (exit 3); single autocommit after a successful job; status file format `<ts> <verdict> [<job>]` where the verdict is `OK|FAILED|REFUSED` for refresh/campaign and `OK <n>/<n>|PARTIAL <k>/<n> FAILED: <codes>|FAILED` for discover (per spec §2.4: `last_run_status` reflects OK or PARTIAL).
- Tests run inside Docker (Linux `flock`/`xargs` semantics — do not run them directly on macOS):

  ```bash
  # one-time image build (repo root):
  docker build -f deploy/Dockerfile -t cb_corpus:test .
  # iterate on run-job tests without rebuilding (deploy/ and tests/ are bind-mounted):
  docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
    cb_corpus:test bash /app/tests/deploy/test_run_job.sh
  # full suite (rebuilds image, runs all test files):
  tests/deploy/run_tests.sh
  ```

  Expected success output of `test_run_job.sh`: `RUN_JOB_OK`.

---

### Task 1: Discover fan-out in `run-job.sh` (env interface, per-bank processes, summary, partial failures)

**Files:**
- Modify: `deploy/run-job.sh`
- Test: `tests/deploy/test_run_job.sh`

**Interfaces:**
- Consumes: `python -m cb_corpus list-banks` (one bank per line, code = first column), `python -m cb_corpus discover --banks <code> [--types <list>] --rounds <n> --download`.
- Produces (relied on by Task 2 and Task 3):
  - Env contract: `DISCOVER_BANKS` (required; `all` or comma list), `DISCOVER_TYPES` (default `full` = omit `--types`), `DISCOVER_ROUNDS` (default `1`), `DISCOVER_WORKERS` (default `6`).
  - Functions in `run-job.sh`: `resolve_banks()` (prints one code per line), `discover_one <code>` (exported, never returns non-zero), `run_discover()` (returns 0 on OK/PARTIAL, 1 when all banks failed, 2 on refusal), global `JOB_SUMMARY` string.
  - Artifacts: `reports/discover/<UTC-date>/<code>.log`, `.ok` / `.failed` marker files in the same dir.
  - Log lines: `[discover] OK <n>/<n>`, `[discover] PARTIAL <ok>/<total> FAILED: <codes>`, `[discover] FAILED 0/<total> banks: <codes>`.

- [ ] **Step 1: Replace the `python` stub and the T5/T5b tests with the new discover tests**

In `tests/deploy/test_run_job.sh`, replace the stub heredoc at the top (the `cat > "$STUB/python" <<'EOF' … EOF` block) with:

```bash
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
```

Then replace the T5 and T5b blocks (from the comment `# T5 — discover consumes DISCOVER_ARGS.` through the end of the T5b block, i.e. up to but not including `# T6`) with:

```bash
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
```

Note: `newdir` already sets `AUTOCOMMIT` only in T1 (`export AUTOCOMMIT=0` there persists for later tests) — the T5d block flips it to 1 and back to 0 explicitly.

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker build -f deploy/Dockerfile -t cb_corpus:test .
docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
  cb_corpus:test bash /app/tests/deploy/test_run_job.sh
```

Expected: `FAIL: ...` on the new T5 (the current script still consumes `DISCOVER_ARGS`, so the per-bank call is never made). Must NOT print `RUN_JOB_OK`.

- [ ] **Step 3: Rewrite the discover path in `deploy/run-job.sh`**

Replace the whole `run_job()` block (from `run_job() {` up to and including its closing `}`) with:

```bash
# ---- discover: parallel per-bank fan-out ------------------------------------
# One bank = one process = one host: the 0.5s per-host politeness throttle is
# untouched, and manifest/raw writes are per-bank hence disjoint between
# processes. data/discovery_errors.jsonl is shared across banks: O_APPEND
# line-sized writes, accepted limitation.

resolve_banks() {
  if [ "$DISCOVER_BANKS" = "all" ]; then
    python -m cb_corpus list-banks | awk '{print $1}'
  else
    echo "$DISCOVER_BANKS" | tr ',' '\n' | sed '/^$/d'
  fi
}

discover_one() {
  bank="$1"
  set -- python -m cb_corpus discover --banks "$bank"
  if [ "$DISCOVER_TYPES" != "full" ]; then
    set -- "$@" --types "$DISCOVER_TYPES"
  fi
  set -- "$@" --rounds "$DISCOVER_ROUNDS" --download
  if "$@" > "$DISCOVER_LOG_DIR/$bank.log" 2>&1; then
    echo "$bank" >> "$DISCOVER_LOG_DIR/.ok"
  else
    echo "$bank" >> "$DISCOVER_LOG_DIR/.failed"
  fi
  return 0   # one failing bank must not abort the batch
}
export -f discover_one

JOB_SUMMARY=""

run_discover() {
  if [ -z "${DISCOVER_BANKS:-}" ]; then
    echo "run-job: DISCOVER_BANKS not set — refusing an implicit all-banks discover" >&2
    return 2
  fi
  export DISCOVER_TYPES="${DISCOVER_TYPES:-full}"
  export DISCOVER_ROUNDS="${DISCOVER_ROUNDS:-1}"
  export DISCOVER_LOG_DIR="$DATA_DIR/reports/discover/$(date -u +%Y-%m-%d)"
  mkdir -p "$DISCOVER_LOG_DIR"
  rm -f "$DISCOVER_LOG_DIR/.ok" "$DISCOVER_LOG_DIR/.failed"

  local banks total ok failed
  banks=$(resolve_banks)
  if [ -z "$banks" ]; then
    echo "run-job: empty bank list" >&2
    return 2
  fi
  echo "$banks" | xargs -n1 -P "${DISCOVER_WORKERS:-6}" bash -c 'discover_one "$1"' _

  total=$(echo "$banks" | wc -l | tr -d ' ')
  ok=0
  if [ -f "$DISCOVER_LOG_DIR/.ok" ]; then ok=$(wc -l < "$DISCOVER_LOG_DIR/.ok" | tr -d ' '); fi
  failed=$(sort "$DISCOVER_LOG_DIR/.failed" 2>/dev/null | paste -sd, - || true)
  if [ "$ok" -eq "$total" ]; then
    JOB_SUMMARY="OK $ok/$total"
    return 0
  elif [ "$ok" -gt 0 ]; then
    JOB_SUMMARY="PARTIAL $ok/$total FAILED: $failed"
    return 0
  else
    JOB_SUMMARY="FAILED 0/$total banks: $failed"
    return 1
  fi
}

run_job() {
  case "$JOB" in
    refresh)
      python -m cb_corpus bis-sitemap --download \
        && python -m cb_corpus repec --download ;;
    discover)
      run_discover ;;
    campaign)
      python -m cb_corpus "$@" ;;
  esac
}
```

Then, in the success/failure block at the bottom of the script, replace:

```bash
log "START"
if run_job "$@"; then
  log "OK"
  echo "$(ts) OK [$JOB]" > "$STATUS"
```

with:

```bash
log "START"
if run_job "$@"; then
  log "${JOB_SUMMARY:-OK}"
  echo "$(ts) ${JOB_SUMMARY:-OK} [$JOB]" > "$STATUS"
```

and replace the failure branch:

```bash
else
  rc=$?
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
```

with:

```bash
else
  rc=$?
  if [ -n "$JOB_SUMMARY" ]; then log "$JOB_SUMMARY"; fi
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
```

Nothing else changes in this task (the lock block is Task 2).

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
  cb_corpus:test bash /app/tests/deploy/test_run_job.sh
```

Expected: `RUN_JOB_OK` (all tests, T1–T8 including the new T5 series).

- [ ] **Step 5: Commit**

```bash
git add deploy/run-job.sh tests/deploy/test_run_job.sh
git commit -m "feat(deploy): parallel per-bank discover fan-out with partial-failure summary"
```

---

### Task 2: Bounded parallelism check + blocking lock for discover

**Files:**
- Modify: `deploy/run-job.sh` (lock block only)
- Test: `tests/deploy/test_run_job.sh`

**Interfaces:**
- Consumes: Task 1's `run_discover` fan-out, the stub's `PY_CONC_DIR`/`PY_SLEEP` instrumentation, `DISCOVER_WORKERS`.
- Produces: new env `DISCOVER_LOCK_TIMEOUT` (seconds, default `7200`); log line `[discover] SKIPPED (lock timeout after <n>s)`; discover waits on the lock instead of skipping.

- [ ] **Step 1: Add the failing tests (append after the T5e block, before `# T6`)**

```bash
# T5f — parallelism is real and bounded by DISCOVER_WORKERS.
newdir; export DISCOVER_BANKS="b1,b2,b3,b4,b5,b6" DISCOVER_WORKERS=2
export PY_CONC_DIR="$D/conc" PY_SLEEP=0.3
mkdir -p "$PY_CONC_DIR"
/app/deploy/run-job.sh discover
MAXC=$(cat "$PY_CONC_DIR/max")
[ "$MAXC" -le 2 ] || fail "parallelism exceeded DISCOVER_WORKERS (max=$MAXC)"
[ "$MAXC" -ge 2 ] || fail "no parallelism observed (max=$MAXC)"
unset DISCOVER_BANKS DISCOVER_WORKERS PY_CONC_DIR PY_SLEEP

# T5g — discover waits for a busy lock (blocking flock) instead of skipping.
newdir; export DISCOVER_BANKS="us"
( exec 9>"$D/.cb.lock"; flock 9; sleep 2 ) &
HOLDER=$!
sleep 0.5
START=$(date +%s)
/app/deploy/run-job.sh discover
END=$(date +%s)
[ $((END - START)) -ge 1 ] || fail "discover did not wait for the lock"
grep -q "PYARGS:-m cb_corpus discover --banks us" "$PY_LOG" || fail "discover did not run after the wait"
wait "$HOLDER"
unset DISCOVER_BANKS

# T5h — discover gives up after DISCOVER_LOCK_TIMEOUT (exit 0, SKIPPED logged).
newdir; export DISCOVER_BANKS="us" DISCOVER_LOCK_TIMEOUT=1
( exec 9>"$D/.cb.lock"; flock 9; sleep 3 ) &
HOLDER=$!
sleep 0.5
/app/deploy/run-job.sh discover || fail "lock timeout must exit 0"
grep -q "\[discover\] SKIPPED (lock timeout" "$D/reports/nas_runs.log" || fail "lock timeout not logged"
if [ -f "$PY_LOG" ] && grep -q "PYARGS:-m cb_corpus discover" "$PY_LOG"; then
  fail "python should not have run on lock timeout"
fi
wait "$HOLDER"
unset DISCOVER_BANKS DISCOVER_LOCK_TIMEOUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
  cb_corpus:test bash /app/tests/deploy/test_run_job.sh
```

Expected: T5f passes (fan-out already bounded by Task 1), then `FAIL: discover did not wait for the lock` on T5g (discover still uses `flock -n` and skips). Must NOT print `RUN_JOB_OK`.

- [ ] **Step 3: Make the discover lock blocking with a timeout**

In `deploy/run-job.sh`, replace the lock block:

```bash
exec 9>"$LOCK"
if [ "$JOB" = "campaign" ]; then
  flock 9   # a campaign waits its turn (refresh in progress, etc.)
else
  if ! flock -n 9; then
    log "SKIPPED (lock busy)"
    exit 0
  fi
fi
```

with:

```bash
exec 9>"$LOCK"
case "$JOB" in
  campaign)
    flock 9 ;;   # a campaign waits its turn (refresh in progress, etc.)
  discover)
    # Wait for an overrunning refresh instead of silently losing the night.
    if ! flock -w "${DISCOVER_LOCK_TIMEOUT:-7200}" 9; then
      log "SKIPPED (lock timeout after ${DISCOVER_LOCK_TIMEOUT:-7200}s)"
      exit 0
    fi ;;
  *)
    if ! flock -n 9; then
      log "SKIPPED (lock busy)"
      exit 0
    fi ;;
esac
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
  cb_corpus:test bash /app/tests/deploy/test_run_job.sh
```

Expected: `RUN_JOB_OK`. T3 (refresh skips on busy lock) must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add deploy/run-job.sh tests/deploy/test_run_job.sh
git commit -m "feat(deploy): blocking lock with timeout for nightly discover"
```

---

### Task 3: Schedule, compose example, runbook

**Files:**
- Modify: `deploy/crontab`
- Modify: `deploy/compose.refresh.example.yml`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: env contract from Task 1 (`DISCOVER_BANKS/TYPES/ROUNDS/WORKERS`) and Task 2 (`DISCOVER_LOCK_TIMEOUT` default 7200).
- Produces: the deployable configuration (image crontab + Dockge env block + operator docs).

- [ ] **Step 1: Replace `deploy/crontab` content**

```
# refresh (bis-sitemap + repec) every 12 h; nightly all-banks discover at 04:00
# (right after the 00:00 refresh, ~4 h observed; discover also waits on the
# lock up to DISCOVER_LOCK_TIMEOUT). Schedule = parameter: edit here (the
# image must be rebuilt) or override the service command in Dockge to point
# to a crontab mounted as a volume.
0 */12 * * * /app/deploy/run-job.sh refresh
0 4 * * * /app/deploy/run-job.sh discover
```

- [ ] **Step 2: Update `deploy/compose.refresh.example.yml`**

Replace the file content with:

```yaml
# Dockge stack "cb-refresh" — 12 h refresh + nightly all-banks discover.
# REPLACE the placeholders (POOL/DATASET/PUID/PGID) in Dockge ONLY.
# NEVER commit the filled-in version: the repo is public.
services:
  cb-refresh:
    image: ghcr.io/jeulinmarc/cb_corpus:latest
    restart: unless-stopped
    user: "PUID:PGID"          # UID:GID of the dataset owner (discover-ids stack)
    environment:
      TZ: Europe/Paris
      DISCOVER_BANKS: all      # "all" or a comma list of bank codes (us,ecb,fr)
      DISCOVER_TYPES: full     # "full" (= whole A-F scope) or a comma list (A3,E2)
      DISCOVER_ROUNDS: "1"     # incremental: first listing pages only
      DISCOVER_WORKERS: "6"    # banks crawled in parallel (bounds CPU/RAM/bandwidth)
      AUTOCOMMIT: "1"
    volumes:
      - /mnt/POOL/DATASET:/app/data              # host path of the SMB dataset
      - ./deploy_key:/run/secrets/deploy_key:ro  # key dropped into the stack's folder
    # mem_limit: 4g        # discover peak: ~6 x (python + headless chromium) = 2-3 GB
    # cpus: "2"
```

- [ ] **Step 3: Update `deploy/README.md`**

In section `## 3. cb-refresh stack`, append this paragraph after the existing key-permissions paragraph (line 60):

```markdown
Discover scope is controlled by env vars (Dockge, no rebuild needed):
`DISCOVER_BANKS` (`all` or comma list — required, the job refuses to run
without it), `DISCOVER_TYPES` (`full` = whole A–F scope, or comma list),
`DISCOVER_ROUNDS` (1 = incremental), `DISCOVER_WORKERS` (parallel banks,
default 6 — the single knob bounding CPU/RAM/bandwidth),
`DISCOVER_LOCK_TIMEOUT` (seconds discover waits for a running refresh,
default 7200). Schedule: refresh at 00:00/12:00, discover nightly at 04:00
(Paris). **Migration from DISCOVER_ARGS:** stacks created before 2026-07-14
used `DISCOVER_ARGS`, which no longer exists — replace it with the variables
above and recreate the stack.
```

In section `## 5. Sanity checks`, append two bullets:

```markdown
- After a nightly discover: per-bank logs under `data/reports/discover/<date>/`,
  one summary line `OK n/n` or `PARTIAL k/n FAILED: <codes>` in `nas_runs.log`,
  and a `data: NAS discover <date>` commit on GitHub when something changed.
- A `PARTIAL` status is not an emergency: failed banks are retried the next
  night. Investigate a bank only when it fails several nights in a row
  (its log under `reports/discover/<date>/<code>.log` has the traceback).
```

- [ ] **Step 4: Run the full deploy test suite (regression check)**

```bash
tests/deploy/run_tests.sh
```

Expected: `ALL_DEPLOY_TESTS_OK` (image rebuild picks up the new crontab; `test_image.sh` checks the image layout).

- [ ] **Step 5: Commit**

```bash
git add deploy/crontab deploy/compose.refresh.example.yml deploy/README.md
git commit -m "feat(deploy): nightly all-banks discover schedule, compose env and runbook"
```

---

## Post-implementation (not part of the coding tasks)

Per the spec §6 and the user's workflow rules:

1. Final branch review + `superpowers:requesting-code-review` pass, then PR to master (never a direct push), watch the real CI run (`gh run watch`).
2. After merge: CI rebuilds `ghcr.io/jeulinmarc/cb_corpus:latest`; in Dockge replace `DISCOVER_ARGS` with the new env block, re-pull, recreate `cb-refresh`.
3. Real validation on the NAS: one campaign-style subset run (`DISCOVER_BANKS=nl,at,se`), then a full `all` run; check `reports/discover/<date>/`, the summary line, wall-clock duration and the commit before trusting the nightly cron.
