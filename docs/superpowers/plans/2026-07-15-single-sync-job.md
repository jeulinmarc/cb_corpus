# Single Sync Job + Native-Only Discover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-15-single-sync-job-design.md`: a `--native-only` discover mode (skip shared BIS/RePEc catalogs), and one scheduled `sync` job replacing `refresh`+`discover` in `deploy/run-job.sh`.

**Architecture:** Python layer first (flag threaded CLI → pipeline → adapter, shared-catalog branches skipped under the flag), then the job wrapper (sync = bis-sitemap → repec → native fan-out under one lock, one autocommit), then schedule/compose/runbook.

**Tech Stack:** Python 3.13 + pytest (run with `python3.13 -m pytest tests/ -q`); bash tests in Docker (`docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" cb_corpus:test bash /app/tests/deploy/test_run_job.sh` → `RUN_JOB_OK`; full suite `tests/deploy/run_tests.sh` → `ALL_DEPLOY_TESTS_OK`).

## Global Constraints

- English only in everything committed; never add Co-Authored-By/"Generated with Claude"; no real infra values (placeholders POOL/DATASET/PUID/PGID only).
- Log/status strings are test-grepped — script and tests change together.
- Default (no `--native-only`) discover behavior must be byte-identical to today — campaign/manual usage must not change.
- Must NOT change: `campaign` job semantics (blocking flock, verbatim args); empty-volume REFUSED guard (exit 3); autocommit only after a successful (exit 0) job; the native fan-out mechanics from PR #3 (per-bank xargs, `.ok`/`.failed`, summary grammar `OK n/n` / `PARTIAL k/n FAILED: <codes>` / `FAILED 0/n banks: <codes>`, `DISCOVER_BANK_TIMEOUT` wrapping, list-banks footer filter).
- Jobs after this change: `sync` (flock -n) and `campaign` (blocking) ONLY; `refresh`/`discover` job names must be rejected; `DISCOVER_LOCK_TIMEOUT` is removed everywhere.

---

### Task 1: `--native-only` through CLI → pipeline → adapter

**Files:**
- Modify: `cb_corpus/adapters/base.py` (`discover`, `discover_all`)
- Modify: `cb_corpus/pipeline.py` (`run`)
- Modify: `cb_corpus/cli.py` (discover parser + dispatch)
- Test: `tests/test_native_only.py` (new)

**Interfaces:**
- Produces: `BankAdapter.discover(doc_type, since=None, native_only=False)`; `BankAdapter.discover_all(scope=FULL_SCOPE, since=None, native_only=False)`; `pipeline.run(..., native_only: bool = False)`; CLI flag `--native-only` on `discover`.
- Task 2 relies on: `python -m cb_corpus discover --banks <code> [--types <list>] --rounds <n> --native-only --download` being a valid invocation.

- [ ] **Step 1: Write the failing tests** — create `tests/test_native_only.py`:

```python
"""--native-only: shared-catalog sources are skipped, native sources kept."""
from datetime import date
from typing import Iterator, Optional

from cb_corpus.adapters.base import BankAdapter
from cb_corpus.banks import get_bank
from cb_corpus.models import DocRecord
from cb_corpus.taxonomy import DocType


class _SpyShared:
    """Stands in for BISSpeechIndex / RePEcDiscovery on an adapter instance."""
    def __init__(self):
        self.calls = 0

    def discover(self, *a, **k) -> Iterator[DocRecord]:
        self.calls += 1
        return iter(())

    def discover_bank(self, *a, **k) -> Iterator[DocRecord]:
        self.calls += 1
        return iter(())


def _rec(bank: str, dt: DocType, url: str) -> DocRecord:
    return DocRecord(bank_code=bank, doc_type=dt, title="t", pdf_url=url,
                     source_url=url, date=date(2026, 1, 1), language="en",
                     provenance="test")


class _NativeA3(BankAdapter):
    native_types = (DocType.A3, DocType.D1)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        yield _rec(self.bank.code, doc_type, f"https://x.test/{doc_type.code}.pdf")


def _spied(cls):
    a = cls(get_bank("se"))
    a._bis = _SpyShared()
    a._repec = _SpyShared()
    return a


def test_default_uses_shared_catalogs():
    a = _spied(_NativeA3)
    list(a.discover_all(scope=(DocType.C1, DocType.D2)))
    assert a._bis.calls == 1        # C1 → BIS index
    assert a._repec.calls == 1      # D2 non-native → RePEc


def test_native_only_never_touches_shared_catalogs():
    a = _spied(_NativeA3)
    recs = list(a.discover_all(scope=(DocType.C1, DocType.A3,
                                      DocType.D1, DocType.D2),
                               native_only=True))
    assert a._bis.calls == 0
    assert a._repec.calls == 0
    # native types still yielded (A3 via _discover_native, D1 via native branch)
    assert {r.doc_type for r in recs} == {DocType.A3, DocType.D1}


def test_native_only_generic_bank_yields_nothing():
    class _Generic(BankAdapter):
        native_types = ()
    a = _spied(_Generic)
    assert list(a.discover_all(native_only=True)) == []
    assert a._bis.calls == 0 and a._repec.calls == 0


def test_native_only_still_honors_skip_known_url():
    a = _spied(_NativeA3)
    a._skip_known_url = lambda url: url.endswith("D1.pdf")
    recs = list(a.discover_all(scope=(DocType.D1,), native_only=True))
    assert recs == []
```

Adjust `_rec` fields to `DocRecord`'s actual constructor if it differs (read `cb_corpus/models.py` first); the four test behaviors are the requirement, the helper is plumbing.

- [ ] **Step 2: Run to verify failure**

Run: `python3.13 -m pytest tests/test_native_only.py -q`
Expected: TypeError — `discover_all() got an unexpected keyword argument 'native_only'`.

- [ ] **Step 3: Implement the flag in `cb_corpus/adapters/base.py`**

Change the two method signatures and the two shared-catalog branches:

```python
    def discover(self, doc_type: DocType,
                 since: Optional[date] = None,
                 native_only: bool = False) -> Iterator[DocRecord]:
        if doc_type == DocType.C1:
            # C1 comes from the shared BIS index (no adapter has a native C1
            # route today) — skipped entirely under native_only: the sync
            # job's catalog phase owns that source.
            if native_only:
                return
            yield from self._bis.discover(since=since, only_banks={self.bank.code})
        elif doc_type in (DocType.D1, DocType.D2):
```

and in the same `elif`, the non-native branch:

```python
            else:
                if native_only:
                    return
                yield from (r for r in self._repec.discover_bank(self.bank.code)
                            if r.doc_type == doc_type)
```

(The `if doc_type in self.native_types:` branch and the final `else: yield from self._discover_native(...)` are unchanged — native routes ignore the flag.)

```python
    def discover_all(self, scope: tuple[DocType, ...] = FULL_SCOPE,
                     since: Optional[date] = None,
                     native_only: bool = False) -> Iterator[DocRecord]:
        for dt in scope:
            if dt in self.supported_types():
                yield from self.discover(dt, since=since, native_only=native_only)
```

- [ ] **Step 4: Thread it through `pipeline.py` and `cli.py`**

`pipeline.run` signature gains `native_only: bool = False` (after `max_rounds`), and the call site becomes:

```python
            recs = adapter.discover_all(scope=scope, since=since,
                                        native_only=native_only)
```

`cli.py`: on the discover parser add

```python
    d.add_argument("--native-only", action="store_true",
                   help="skip shared catalogs (BIS index, RePEc) — bank-site "
                        "sources only; the sync job's catalog phase owns those")
```

and pass `native_only=args.native_only` in the `run(...)` dispatch call.

- [ ] **Step 5: Run tests**

Run: `python3.13 -m pytest tests/ -q`
Expected: all pass (121 existing + 4 new).

- [ ] **Step 6: Commit**

```bash
git add cb_corpus/adapters/base.py cb_corpus/pipeline.py cb_corpus/cli.py tests/test_native_only.py
git commit -m "feat(discover): --native-only skips shared BIS/RePEc catalogs"
```

---

### Task 2: `run-job.sh` — single `sync` job

**Files:**
- Modify: `deploy/run-job.sh`
- Test: `tests/deploy/test_run_job.sh`

**Interfaces:**
- Consumes: Task 1's `--native-only` flag.
- Produces: job names `sync | campaign` only; log lines `[sync] START`, `[sync] catalogs OK`, then the PR #3 summary grammar; env contract unchanged minus `DISCOVER_LOCK_TIMEOUT`.

- [ ] **Step 1: Rework the bash tests**

In `tests/deploy/test_run_job.sh` (stub unchanged), replace the T1–T5i suite with the sync equivalents — keep each existing assertion style, changing job names and adding the new ones. The complete new test list (replace the whole body between the stub setup and the final `echo RUN_JOB_OK`):

```bash
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
```

Notes for the implementer: T10's timing bound is wide (20 s vs 1 s timeout + `timeout -k` grace) to stay flake-free; T2/T8 rely on `PY_FAIL_MATCH` substring-matching the stub's full arg string; keep the stub exactly as it is on the branch (it already has the list-banks footer and the concurrency counter).

- [ ] **Step 2: Run to verify failure**

Docker one-time build then the run-job test file. Expected: T1 fails (`unknown job 'sync'`). Must NOT print `RUN_JOB_OK`.

- [ ] **Step 3: Rework `deploy/run-job.sh`**

Starting from the current file on this branch, apply exactly:

1. Job validation case becomes `sync|campaign` (refresh/discover fall to the error branch).
2. `discover_one`: the command construction gains `--native-only`:

```bash
  set -- "$@" --rounds "$DISCOVER_ROUNDS" --native-only --download
```

3. Add `run_sync` above `run_job` and rewire `run_job`:

```bash
run_sync() {
  # Phase 1+2: shared catalogs, read once for all banks (the whole point:
  # per-bank discover no longer re-walks them — see the 2026-07-15 spec).
  python -m cb_corpus bis-sitemap --download || return $?
  python -m cb_corpus repec --download || return $?
  log "catalogs OK"
  # Phase 3: native bank-site fan-out (unchanged mechanics from PR #3).
  run_discover
}

run_job() {
  case "$JOB" in
    sync)     run_sync ;;
    campaign) python -m cb_corpus "$@" ;;
  esac
}
```

4. Move the `DISCOVER_BANKS` guard so it runs FIRST in `run_sync` (before the catalog phases): lift the existing guard block out of `run_discover` to the top of `run_sync`, keeping `run_discover`'s behavior otherwise identical. (Rationale: a misconfigured stack must fail in seconds, not after 4 h of catalogs — pinned by T5b.)
5. Lock block: delete the `discover)` branch (and with it every `DISCOVER_LOCK_TIMEOUT` reference); `sync` falls through to the default `flock -n` branch; `campaign` unchanged.

- [ ] **Step 4: Run the bash tests**

Expected: `RUN_JOB_OK`.

- [ ] **Step 5: Commit**

```bash
git add deploy/run-job.sh tests/deploy/test_run_job.sh
git commit -m "feat(deploy): single nightly sync job (catalogs once + native fan-out)"
```

---

### Task 3: Schedule, compose, runbook

**Files:**
- Modify: `deploy/crontab`
- Modify: `deploy/compose.refresh.example.yml`
- Modify: `deploy/README.md`

- [ ] **Step 1: `deploy/crontab`** — full new content:

```
# One nightly sync: shared catalogs (bis-sitemap + repec) once for all banks,
# then the parallel native bank-site fan-out. ~4 h total. Schedule = parameter:
# edit here (image rebuild) or override the service command in Dockge to point
# to a crontab mounted as a volume.
0 1 * * * /app/deploy/run-job.sh sync
```

- [ ] **Step 2: `deploy/compose.refresh.example.yml`** — header comment becomes "Dockge stack \"cb-refresh\" — one nightly sync (catalogs + native discover)."; delete the `DISCOVER_LOCK_TIMEOUT` comment line; everything else stays.

- [ ] **Step 3: `deploy/README.md`** — in section 3, rewrite the env paragraph: schedule is now "one sync nightly at 01:00 (Paris)"; remove `DISCOVER_LOCK_TIMEOUT` from the env list; state that `DISCOVER_*` vars scope the NATIVE phase only (catalogs always run once for all banks); extend the migration note: "stacks created before 2026-07-15 used the refresh/discover job pair — the crontab and job names changed; recreate the stack after re-pulling the image." In section 5, adjust the first sanity bullet to reference `data: NAS sync <date>` commits and `[sync] catalogs OK` + summary lines.

- [ ] **Step 4: Full regression**

Run: `tests/deploy/run_tests.sh` → `ALL_DEPLOY_TESTS_OK`, and `python3.13 -m pytest tests/ -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/crontab deploy/compose.refresh.example.yml deploy/README.md
git commit -m "feat(deploy): nightly sync schedule, compose and runbook"
```

---

## Post-implementation

Final whole-branch review + post-fix pass, push, PR — **Marc reviews the PR in detail himself before any merge** (explicitly requested 2026-07-15). Dockge migration and NAS validation happen only after his review and merge, together with the deploy_key directory→file repair.
