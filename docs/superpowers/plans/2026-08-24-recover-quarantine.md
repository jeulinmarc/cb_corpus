# Recover-the-76 + Quarantine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Inject externally-recovered PDFs through storage discipline; quarantine permanently-failing URLs out of the nightly loop.

**Architecture:** New `cb_corpus/quarantine.py` state module (JSONL beside `download_errors.jsonl`, latest-line-wins). `Storage` consults it before network and feeds it on failure/success — one seam for every source. `recover-downloads --candidates` maps hunt results onto `DocRecord`s and registers files via `target_path` + `reindex`.

**Tech:** Python 3.13 (`python3.13 -m pytest tests/ -q` — suite must stay green), stdlib only. English. TDD. No infra values in code.

## Global constraints
- Spec: `docs/superpowers/specs/2026-08-24-recover-quarantine-design.md` — read first.
- Threshold env `QUARANTINE_AFTER_NIGHTS` default 5; bypass env `QUARANTINE_RETRY=1` (set by the Sunday `sync full` path in `deploy/run-job.sh`).
- Provenance rules exactly as specced (wayback / bank_site / mirror).
- Adversarial fixtures: torn state lines, same-night repeat failures (must count ONCE), resurrection, unknown dead_pdf_url in candidates.

---

### Task 1: `cb_corpus/quarantine.py` + tests

**Files:** Create `cb_corpus/quarantine.py`, `tests/test_quarantine.py`.

**Interfaces (Produces):**
```python
class Quarantine:
    def __init__(self, cfg: Config): ...            # state at cfg.data_dir/"download_quarantine.jsonl"
    def is_quarantined(self, url: str) -> bool      # True only when active and not bypassed
    def record_failure(self, url: str, night: str) -> None   # night = "YYYY-MM-DD"; same night counted once
    def record_success(self, url: str) -> None      # tombstone line {"url":..., "released": true}
    def skipped_count(self) -> int                  # is_quarantined() hits this run (for the summary log line)
    def summary_line(self) -> str | None            # 'quarantine: skipped N url(s)' or None
```
State line: `{"url", "nights": ["YYYY-MM-DD", ...], "quarantined": bool}` — latest line per url wins; `released` tombstone clears. Threshold from `os.environ.get("QUARANTINE_AFTER_NIGHTS", "5")` read at call time; bypass when `os.environ.get("QUARANTINE_RETRY") == "1"` (is_quarantined returns False but state is kept). Corrupt line → skip with one warning, never crash. Nights list capped at the threshold (no unbounded growth).

Steps: write failing tests for — counting distinct nights (5 distinct → quarantined; 5 entries same night → not), same-night idempotence, resurrection via record_success then re-failure restarts count, bypass env, torn/corrupt line tolerance, skipped_count/summary. Run (`python3.13 -m pytest tests/test_quarantine.py -q`) red → implement → green → `python3.13 -m pytest tests/ -q` all green → commit `feat(quarantine): per-URL dead-letter state with nightly counting`.

### Task 2: Storage hooks + tests

**Files:** Modify `cb_corpus/storage.py`; Create `tests/test_quarantine_storage.py`.

- `Storage.__init__` builds `self.quarantine = Quarantine(cfg)`.
- At the TOP of `Storage.save` (before any network): if `self.quarantine.is_quarantined(rec.pdf_url)` → return `"skip:quarantined"` (count it).
- In `_record_download_error`: after appending the audit line, `self.quarantine.record_failure(rec.pdf_url, night=<today UTC date iso>)`.
- On successful download in `save` (status `"saved"`): `self.quarantine.record_success(rec.pdf_url)`.
- `save_many` end-of-run: if `self.quarantine.summary_line()`, print it to stderr.

Tests (no network — monkeypatch the fetch): quarantined rec short-circuits with `skip:quarantined`; failure path feeds quarantine; success releases; summary line appears once. Full suite green. Commit `feat(storage): quarantine consult/feed hooks`.

### Task 2b (review follow-up): recovery flows must not be blocked by their own quarantine

**Files:** Modify `cb_corpus/storage.py`, `cb_corpus/recover.py`; extend `tests/test_quarantine_storage.py`, `tests/test_recover_downloads.py`.

`download_errors.jsonl` feeds BOTH quarantine counting and recover-downloads' inventory — so by the time anyone runs recovery, its targets are quarantined and `Storage.save` short-circuits before trying the snapshot alt_url, with `skip:quarantined` swallowed into the CSV `duplicate` bucket. Fixes:
- `Storage.save(rec, *, bypass_quarantine: bool = False)` — gate skipped when True (state untouched).
- `Storage.reindex`: on `"reindexed"` success, call `self.quarantine.record_success(rec.pdf_url)` (an externally-recovered doc must release its quarantine).
- `recover.py` `--download` path: call `save(rec, bypass_quarantine=True)`; branch on EXACT statuses — `skip:already-indexed`/`skip:duplicate-content` → `duplicate`; any other `skip:*` reported verbatim in the CSV action (never mislabeled).
Tests: quarantined URL + snapshot-found → recovery attempts the fetch (bypass works) and reports honestly; reindex releases quarantine (fresh save on that URL no longer short-circuits). Full suite green. Commit `fix(recover): quarantine bypass for recovery flows + reindex release`.

### Task 3: Sunday bypass + operator docs

**Files:** Modify `deploy/run-job.sh` (the `sync full` branch exports `QUARANTINE_RETRY=1`), `deploy/README.md` (short paragraph: what quarantine is, the env knobs, the state file location).
Verification: `bash -n deploy/run-job.sh`; grep the exported var in the full branch only. Commit `feat(deploy): Sunday sweep retries quarantined URLs`.

### Task 4: `recover-downloads --candidates` + tests

**Files:** Modify `cb_corpus/recover.py`, `cb_corpus/cli.py`; Create `tests/test_recover_candidates.py`.

- CLI: `--candidates PATH` (JSONL). With it, `run_recover_downloads` processes ONLY candidates (no CDX walk): match audit entry by `dead_pdf_url` (unknown → CSV row `action="unknown-entry"`, skip); `_refresh_metadata` as today; build `DocRecord` per spec provenance rules:
  - `wayback`: pdf_url=dead url, alt_urls=[final_url first], provenance `wayback`, date_source `wayback` if no repec date.
  - `bank_site`: pdf_url=final_url (live), alt_urls=[dead url], provenance `bank_site`.
  - `mirror`: pdf_url=dead url, alt_urls=[final_url], provenance `mirror`.
- Verify the local file (exists, `%PDF` magic, >20KB) → else CSV `action="bad-file"`.
- `--download` mode: `dest = storage.target_path(rec)`; `shutil.copy2(file_path, dest)` (mkdir parents); `status = storage.reindex(rec, dest)`; CSV action `recovered` on `"reindexed"`, `duplicate` on `skip:*` (remove the copied file again on skip — no orphan bytes). Dry-run: CSV `recoverable`, nothing written.
- Also new flag `--seed-quarantine PATH`: JSONL of `{url}` lines appended to quarantine as already-at-threshold (for the final unrecoverables) — implemented via `Quarantine.record_failure` called with N synthetic distinct nights? NO — add explicit `Quarantine.seed(url, reason)` writing a quarantined line with `{"seeded": reason}`; that keeps history honest. (Add `seed()` + 1 test in this task.)

Tests: tmp corpus fixture; one candidate per provenance rule → manifest row asserted (pdf_url/alt_urls/provenance per rule, file at target_path); bad file; unknown entry; duplicate content dedup; dry-run writes nothing. Full suite green. Commit `feat(recover): candidate-feed injection + quarantine seeding`.

### Task 5 (operational, controller-driven — not a subagent code task)
Run injection with the hunt results (all banks), commit manifests (`data: recover 7x dead-URL documents`), rsync new raw files to the NAS, write `data/reports/recover_unrecoverable.jsonl`, seed quarantine on the NAS state file for the residue, final review wave, PR.

## Final wave
- `python3.13 -m pytest tests/ -q` full green; grep gates (no infra, English-only in new code).
- requesting-code-review + fixes + re-review BEFORE push. PR references the production review.
