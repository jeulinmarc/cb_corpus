# Hardening Implementation Plan (cold-audit findings)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the confirmed findings of the 2026-07-16 four-lens cold audit, on branch feat/download-failures-audit (which already carries the download-error audit file and the full-sweep repec skip). Marc's decisions: revision-blindness accepted WITH visible counters and a corrected spec; hardening ships before the reconcile command.

**Tech Stack:** Python 3.13 + pytest (137 green); bash suite in Docker (`docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" cb_corpus:test bash /app/tests/deploy/test_run_job.sh` → `RUN_JOB_OK`); full gates at the end (`tests/deploy/run_tests.sh` → `ALL_DEPLOY_TESTS_OK`).

## Global Constraints

- English only; never Co-Authored-By/"Generated with Claude"; placeholders only.
- Data-integrity prime directive: a torn tail may be REPAIRED (it is a lost append — the document re-converges next run via stable-key dedup); mid-file corruption must FAIL LOUDLY, never be silently dropped.
- Log/status strings are test-grepped — script and tests change together.
- Every fix lands with the test that would have caught its absence (the audit found the gaps by mutation reasoning — the tests must kill those mutants).

---

### Task H1: Torn-manifest resilience (audit C1 — two independent auditors)

**Files:**
- Modify: `cb_corpus/storage.py` (`iter_manifest_rows` and/or its caller path)
- Modify: `deploy/autocommit.sh` (pre-push validation)
- Modify: `deploy/compose.refresh.example.yml`, `deploy/compose.campaign.example.yml` (`stop_grace_period: 60s`)
- Test: `tests/test_torn_manifest.py` (new), `tests/deploy/test_autocommit.sh` (extend)

**Behavior to implement:**

1. `iter_manifest_rows` (cb_corpus/storage.py:64-68 area): a malformed FINAL line of a per-bank file is a torn append → repair: atomically truncate the file to drop it (write the torn fragment to `<file>.torn` for forensics, print one loud stderr warning naming file and byte offset), then continue loading. A malformed NON-final line → raise a clear error naming file + line number (hard stop — that is corruption, not a torn append). Blank lines keep being skipped as today.
2. `deploy/autocommit.sh`: before `git add`, validate every `manifest/*.jsonl` in the COPY (python one-liner: every non-blank line must `json.loads`); on failure, print `autocommit: REFUSED (malformed manifest: <file>)` and exit non-zero (run-job already logs `AUTOCOMMIT FAILED (local state intact...)` — verify the message flows through).
3. Both compose examples gain `stop_grace_period: 60s` (container kills during writes get 60 s of SIGTERM grace before SIGKILL).

**Tests (TDD, RED first):**
- pytest: (i) torn final line → loaded rows = all-but-torn, file repaired on disk, `.torn` file contains the fragment, warning emitted; (ii) torn MIDDLE line → raises, message names file+line; (iii) intact file unchanged byte-for-byte after a load (no gratuitous rewrites); (iv) empty file and trailing-newline-only file load as zero rows (today's behavior).
- bash (test_autocommit.sh): a manifest copy containing a torn line → autocommit exits non-zero, `REFUSED (malformed manifest` in output, nothing pushed (assert on the test's fake remote, following the existing test's pattern).

Commit: `fix(storage): repair torn manifest tails, refuse to push malformed manifests`

---

### Task H2: Operator visibility + UTC log-dir fix (audit prod-I1/I2/I4, shell-I1)

**Files:**
- Modify: `deploy/run-job.sh`
- Modify: `deploy/README.md` (status semantics, Sunday-noise note, volume-wins note)
- Test: `tests/deploy/test_run_job.sh`

**Behavior:**

1. `DISCOVER_LOG_DIR` keyed by CONTAINER-LOCAL date (`date +%Y-%m-%d`, TZ=Europe/Paris in prod) instead of `date -u` — Monday-01:00-CEST no longer resolves to Sunday's dir and clobbers the full-sweep logs. (Tests run in the container without TZ set → UTC; compute expected the same way via `date +%Y-%m-%d`.)
2. Catalog phases tee'd to disk: in `run_sync`, redirect each catalog command's stdout+stderr through `tee -a "$DISCOVER_LOG_DIR/catalogs.log"` (create the dir before phase 1 — move the mkdir up), so a Dockge Update can no longer erase the only copy of a catalog traceback. Add `log "bis-sitemap OK"` between the two phases (heartbeat).
3. Lock visibility: the campaign branch logs `log "WAITING (lock busy)"` before its blocking `flock 9` when the lock is already held (use a `flock -n 9 || { log "WAITING (lock busy)"; flock 9; }` pattern); the sync skip path ALSO writes the SKIPPED line to `last_run_status` (`echo "$(ts) SKIPPED [$JOB]" > "$STATUS"`) so status monitoring can't mistake a skipped night for freshness.
4. Env hygiene: `DISCOVER_BANKS` comma-list is whitespace-trimmed (`tr -d '[:space:]'` before split); `SYNC_WINDOW_DAYS`, when set, must be all digits or the job fails fast with `FAILED (invalid SYNC_WINDOW_DAYS)` before any phase; `cd "$APP_DIR"` gets `|| exit 1`.
5. README: document the status verdicts incl. SKIPPED; the transitional Sunday error barrage until `repec-reconcile` is written+run (expected ~400 gb/fr lines in download_errors.jsonl per full sweep — do not misread either a noisy or a prematurely-quiet Sunday); the volume-always-wins autocommit semantics.

**Tests:** extend the bash suite — SKIPPED writes status (extend T3); WAITING logged by a campaign that had to wait (extend T4); `catalogs.log` exists and contains stub output after a sync (extend T1); trimmed `DISCOVER_BANKS=" us , ecb "` still yields exactly 2 banks (new); `SYNC_WINDOW_DAYS=90x` → fast FAILED, no python calls (new); log-dir date computed with local date (adjust DAY= in existing tests to `date +%Y-%m-%d`).

Commit: `fix(deploy): operator visibility — local-date log dirs, catalog tee, SKIPPED status, env validation`

---

### Task H3: Seam tests + audit-guard test (audit test-C1/I2/I3, shell-I3)

**Files:**
- Test: `tests/test_seams.py` (new), `tests/test_download_audit.py` (extend), `tests/deploy/test_run_job.sh` (extend), `tests/deploy/test_autocommit.sh` (extend)
- Modify: NOTHING in production code (these tests must pass against current code; if one fails, STOP and report — that is a live bug, not a test to adjust)

**Tests to add:**
1. `native_only` end-to-end seam: call `pipeline.run(bank_codes=["se"], native_only=True, ...)` with a recording fake adapter injected via the registry (monkeypatch `ADAPTERS`/`get_adapter`) asserting `discover_all` receives `native_only=True`; same for the CLI hop (monkeypatch `pipeline.run` in the cli dispatch test style of test_framework.py, asserting the kwarg VALUE, not just acceptance).
2. `skip_url` identity seam: `run_repec(..., incremental=True)` AND `(incremental=False)` with a real `Storage` over a tmp manifest → assert the captured callable IS `storage.is_known_source_url` (identity or behavior: returns True for the seeded source_url and False under `storage.is_known_url` semantics — pin the INDEX, not just callability).
3. Audit-guard adversarial: `_record_download_error` forced to raise (monkeypatch it, or point `data_dir` at a read-only dir for the audit write) → `save_many` still returns `{"error": 1}` and processes subsequent records (two-record batch: first fails download AND fails audit, second succeeds → counts `{"error":1,"saved":1}`-shaped).
4. bash: autocommit-failure branch — AUTOCOMMIT_BIN stub exits 1 → sync still exits 0, `AUTOCOMMIT FAILED (local state intact` logged (grep), status keeps the OK verdict; campaign non-zero rc propagates (stub PY_EXIT=1 via campaign → wrapper exit code equals it, `FAILED [campaign]` in status).
5. Registry-vs-filter assertion (kills the mirror-test): in `tests/deploy/test_image.sh` (runs the REAL CLI in-image), assert `python -m cb_corpus list-banks | awk 'NF && $1 ~ /^[a-z]{2,4}$/' | wc -l` equals `python -m cb_corpus list-banks | grep -c '^[a-z]'` — i.e. the filter drops exactly the footer, nothing else, against the live registry.

Commit: `test: pin cross-layer seams, audit-guard isolation, autocommit failure paths, registry-vs-filter`

---

### Task H4: Spec truth + revision counters + portability (audit data-I1/minors, shell-I4)

**Files:**
- Modify: `cb_corpus/sources/repec.py` (skip counter), `cb_corpus/pipeline.py` (surface it)
- Modify: `deploy/run-job.sh` (portable awk), `docs/superpowers/specs/2026-07-16-download-failures-design.md` (premise correction)
- Test: `tests/test_repec_incremental.py` (extend)

**Behavior:**
1. Revision-blindness made visible (Marc's decision: accept + counters): `discover_bank` counts skip_url-skipped URLs per bank and reports them — simplest honest channel: return-by-print `[repec:<code>] skipped-known: N` to stderr at bank end (match the existing progress-line style; run_repec passes the bank code in, or discover_bank prints with the bank code it already has). Test: spy-based fixture asserts the line content for a known/unknown mix.
2. Spec §2: replace the "pre-existing behavior, unchanged" sentence with the accurate statement: same-URL revisions were already invisible; CHANGED-URL revisions become invisible with this change; accepted by Marc 2026-07-16 with the skip counters as the visibility mechanism and periodic repec-check as the safety net.
3. Portable awk in `resolve_banks`: replace the `{2,4}` interval (mawk-version-dependent) with `awk 'NF && $1 ~ /^[a-z][a-z][a-z]?[a-z]?$/ {print $1}'` or the length()-based form — behavior identical, no dependency on POSIX-interval support. Bash tests keep passing unchanged (same accepted/rejected sets).
4. Note in `deploy/README.md` §6 (updates): recommend pinning/reviewing the base-image jump when rebuilding after long gaps (audit shell-I4) — one sentence.

Commit: `fix(repec): visible skipped-known counters; portable bank-code filter; spec premise corrected`

---

## Deliberately deferred (recorded, not lost)

To the reconcile PR or later, per audit triage: catalog-phase wall-clock timeout env; log/error-file rotation; chromium orphan/profile sweep hardening; state-repo squash policy; `run-job.sh sync <typo>` strictness; M1 status-absent doc fix (folded into H2 README edits); cross-bank sha256 race (duplicates only); dry-run `_source_urls` comment (fold into H4 if trivial).

## Post-implementation

Full gates; final whole-branch review (most capable model — attention: the torn-tail repair path is new file-mutating code in the hot loader, review it adversarially); fix wave; docs to `documentation` branch; PR titled "audit hardening + download-failure audit trail" for Marc's review. The reconcile command (original plan's Task 3) moves to the NEXT branch/PR.
