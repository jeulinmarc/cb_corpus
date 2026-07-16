# Recovery Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-16-recovery-phase2-design.md`: the `recover-downloads` command (inventory-driven Wayback recovery) and the `--propose`/`--apply-csv` human-approved reconciliation modes.

**Tech Stack:** Python 3.13 + pytest (`python3.13 -m pytest tests/ -q`, 160 green); Docker run-job regression once at the end.

## Global Constraints

- English only; never Co-Authored-By/"Generated with Claude"; no real infra values.
- Zero-fuzzy-writes doctrine: similarity scores may ORDER proposals, never trigger a write; every write path re-validates its invariants at apply time; dry-run is the default everywhere; CSV lines must never claim a write that did not happen (PR #7 invariant).
- Recovery records: `pdf_url` = official bank URL (citation + doc_id stability), raw snapshot in `alt_urls`, `provenance="wayback"`, date honesty (`date_precision`/`date_source` as per the metadata source).
- Fixtures include multi-byte UTF-8 titles (house lesson).
- Existing behavior unchanged; 160 tests stay green.

---

### Task 1: `recover-downloads` (inventory → Wayback → save)

**Files:**
- Modify: `cb_corpus/sources/wayback.py` (add `latest_capture`)
- Create: `cb_corpus/recover.py` (the command's logic; keeps repec_check.py focused)
- Modify: `cb_corpus/cli.py`
- Test: `tests/test_recover_downloads.py` (new)

**Interfaces:**
- `latest_capture(fetcher, url, mimetype="application/pdf") -> Optional[str]` in `sources/wayback.py`: latest HTTP-200 snapshot timestamp for the EXACT url (CDX, `limit=-1` tail or sort desc — mirror `first_capture`'s style; no prefix matching), else None.
- `run_recover_downloads(bank_codes=None, download=False, csv_path=None, config=None, fetcher=None) -> dict[str, dict]` in `cb_corpus/recover.py`: returns per-bank `{"recoverable": n, "recovered": n, "unrecoverable": n, "converged": n}` (`recovered` only counts actual saves in `--download` mode).
- CSV columns: `{bank, pdf_url, action, snapshot_ts, title}`, action ∈ `recoverable|recovered|unrecoverable|converged`; written in dry-run AND download modes (default path `data/reports/recover_downloads.csv`).
- CLI: `recover-downloads [--banks a,b] [--download] [--csv path]`.

**Behavior (from the spec, step by step):**
1. Read `data/download_errors.jsonl` (tolerate missing file → empty run with a stderr note). Dedup by `pdf_url` keeping the LATEST entry (file is append-ordered). `--banks` filters on `bank_code`.
2. Per entry, converged check: `storage.is_known_url(pdf_url)` or any alt known, or `storage.is_known_source_url(source_url)` → action `converged`, no network.
3. Metadata refresh: if `source_url` startswith the IDEAS host → `fetcher.get_text(source_url)`, `_paper_meta` for title/date (month precision, `date_source="repec"`), `extract_pdf_candidates` for fresh alts; on fetch failure fall back to the audit entry's fields (title from the audit line; date None with `date_precision`/`date_source` left to the DocRecord defaults for undated rows — check how existing wayback records handle missing dates in `run_wayback_recovery`/`WaybackSource` and mirror that).
4. `latest_capture(pdf_url)`; on None, try each alt URL in order (the snapshot's ORIGINAL url is then that alt — the record's pdf_url stays the OFFICIAL bank url from the audit entry).
5. `recoverable`: dry-run → CSV line only. `--download` → build the DocRecord (doc_type from the audit entry's `doc_type` code via `DocType[...]`; bank_code; title; `pdf_url` official; `alt_urls` = [raw_url(snapshot)] + fresh candidates (minus pdf_url, deduped, snapshot FIRST so the fallback hits it before other blockable mirrors — wait, no: `save()` tries `pdf_url` first, then alt_urls in order; putting the snapshot first among alts is correct); `source_url`; `provenance="wayback"`) and `storage.save(rec)`; action `recovered` on a `saved`-ish status, keep the returned status in the CSV title-side note if useful. A save that itself fails lands in `download_errors.jsonl` naturally (audit loop) — acceptable, note in the report.
6. No snapshot anywhere → `unrecoverable`.

**Tests (TDD, fixtures with UTF-8 titles):**
- `latest_capture`: CDX-fixture fetcher → latest ts; empty CDX → None; exact-url query shape asserted (no `matchType=prefix`).
- Inventory: dedup keeps latest; converged via pdf_url AND via source_url; `--banks` filter; missing audit file → all-zero counts, no crash.
- Recovery record: official pdf_url preserved; raw snapshot first in alt_urls; provenance wayback; with a stub fetcher where the official URL raises and the snapshot returns bytes → `storage.save` persists (real tmp manifest, assert the row); dry-run: zero fetcher.get_bytes calls (spy), CSV still written.
- Counts dict + CSV shape.

**Real-data check (read-only, mandatory):** after GREEN, run `PYTHONPATH=. python3.13 -m cb_corpus recover-downloads --banks fr` — dry-run — against a MINIMAL synthetic `data/download_errors.jsonl`? NO: the real audit file does not exist on the Mac (it lives on the NAS). Instead: craft `/tmp`-style scratchpad Config with a hand-built `download_errors.jsonl` containing the 13 real fr entries (bank fr, doc_type D1, the real banque-france pdf_urls WP1002/DT986/WP981/WP947/WP859 + the others from the audit — take whatever URLs you can reconstruct from the earlier logs, at least those five), point `data_dir` at a COPY of `data/manifest` (read-only semantics: dry-run writes nothing) and run the function directly. Expected: most entries `recoverable` with a snapshot_ts. Record the real counts in the report; if 0 recoverable, STOP and report DONE_WITH_CONCERNS.

Commit: `feat(recover): recover-downloads — inventory-driven Wayback recovery`

---

### Task 2: `repec-reconcile --propose` / `--apply-csv` (human-approved drift reconciliation)

**Files:**
- Modify: `cb_corpus/repec_check.py` (extend `run_repec_reconcile` or add siblings `run_reconcile_propose` / `run_reconcile_apply`)
- Modify: `cb_corpus/cli.py`
- Test: `tests/test_repec_reconcile.py` (extend)

**Interfaces:**
- CLI: `repec-reconcile --propose [--banks gb] [--csv path]` (mutually exclusive with `--write` and `--apply-csv`; propose NEVER writes manifests).
- CLI: `repec-reconcile --apply-csv <path> [--write] [--csv path]` (without `--write` = dry-run report of what would be stamped).
- Propose CSV columns: `{bank, ideas_url, repec_title, candidate_doc_id, candidate_title, score, approve}` — up to 3 candidates per unmatched entry, ranked by `difflib.SequenceMatcher.ratio()` on `normalize_title` outputs, `approve` EMPTY.
- Apply reads the same CSV; a row is approved iff `approve.strip().lower() in ("x","yes","1","oui")`.
- Apply counts: `{"applied": n, "skipped": n}`; skip reasons in the output CSV (`row-gone|source-not-empty|duplicate-doc-id|duplicate-ideas-url|not-approved`).

**Behavior:**
- Propose: reuse the reconcile walk; for entries that end `unmatched`, rank candidate rows = same bank, doc_type D1/D2, `source_url` empty, by similarity of normalized titles; emit top ≤3 (score rounded to 3 decimals). Entries with zero candidates emit one row with empty candidate fields.
- Apply: load the CSV; for approved rows, re-validate NOW against the live manifest: doc_id exists, its `source_url` still empty, this doc_id not already claimed in this run, this ideas_url not already claimed and not already known (`is_known_source_url` → skip `already`); stamp via the same full-row-set `rewrite_manifest` path as phase 1 (share the writing helper — extract it if needed rather than duplicating).
- Invariants identical to phase 1: CSV `applied` lines == writes; first-wins on duplicates; dry-run purity byte-for-byte.

**Tests:** propose ranks correctly and caps at 3 (fixtures incl. UTF-8 and a zero-candidate entry); propose writes no manifest bytes; apply stamps exactly approved pairs; each skip reason exercised (row gone, source no longer empty, duplicate doc_id across two approved rows → first wins + skip line, unapproved untouched, apply without --write touches nothing); idempotence (re-apply same CSV → all `already`-ish skips).

Commit: `feat(repec): propose/apply-csv — human-approved reconciliation for title drift`

---

## Post-implementation

Both tasks reviewed (write path of Task 2 gets the adversarial treatment — it shares phase 1's most dangerous line); final whole-branch review (most capable model); fix wave; Docker run-job regression; docs to `documentation` branch; PR for Marc. Rollout per spec (fr recovery first, then gb propose→Marc→apply).
