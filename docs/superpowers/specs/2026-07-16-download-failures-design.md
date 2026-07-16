# Download failures: audit, reconciliation, recovery — design

Date: 2026-07-16
Status: direction approved by Marc ("je ne veux aucune erreur"), spec for review

## Context: what the nightly errors actually are (evidence-based)

Production counts (2026-07-15/16 full sweeps): gb 372, se 22, fr 13, it 6, jp 3
download errors per catalog pass — retried with 3 attempts each, every run.

Root-cause analysis (Mac, 2026-07-16):

- **gb (372-374): zero documents missing — pure noise.** The `boe:boeewp` series
  lists 1,128 papers; the 374 failing ones are the OLDEST (WP #1→~282,
  1992-2005). IDEAS points at dead pre-redesign bankofengland.co.uk URLs (404).
  Sampled papers are ALL already in the corpus as `bank_site` D1 rows (443
  native rows from the WP v3 native BoE crawl) — under modern slug URLs, with
  EMPTY `source_url` and no cross-reference to the RePEc entry. The crawler
  therefore re-attempts 374 dead downloads (× 3 retries) per catalog pass for
  papers it already owns.
- **fr (13): genuinely missing.** WP1002, DT986, WP981, WP947, WP859… are in no
  manifest (checked by URL and alt_urls); banque-france.fr returns 403 on
  direct `/system/files/*.pdf` fetches (bot protection on some files).
- **se (22) / it (6) / jp (3): unclassified** — the audit file below makes them
  classifiable without guesswork.
- **Structural defect: download failures are not persisted anywhere.**
  `Storage.save_many` swallows the exception (`except Exception: status =
  "error"`) and keeps only a counter; the only trace is container stdout.
  (`discovery_errors.jsonl` covers DISCOVERY fetch failures — a different,
  already-solved concern.)

## Goals

1. Every download failure becomes a durable, queryable audit record (class D).
2. The gb-style noise disappears — nightly AND Sunday — via reconciliation on
   stable keys, never dates, never fuzzy guesses (class A).
3. Genuinely missing papers get recovered through mirrors/Wayback, driven by
   real audit data (class B), which also classifies se/it/jp (class C).
4. Zero-error discipline: every write is dry-run first, unique-match-only,
   reported before applied; anything ambiguous is a report line, not a write.

## Delivery in two phases

**Phase 1 (this spec's plan): D + A.** Ships the audit file, the save-side
source dedup, and the one-shot reconciliation. After one real nightly/Sunday
cycle, the audit file gives the exact class-B/C inventory.
**Phase 2 (separate plan, after real audit data): B + C.** Mirror/Wayback
recovery for the audited leftovers. Not planned in detail here on purpose —
it must be calibrated on the phase-1 audit output (real-data-first rule).

## Phase 1 design

### 1. Download-failure audit file (class D)

- `Storage.save_many` catches per-record exceptions as today, but appends one
  JSON line to `data/download_errors.jsonl` before counting:
  `{ts, label, bank_code, doc_type, title, pdf_url, alt_urls, source_url, error}`
  (`ts` UTC ISO; `label` = the save_many label, e.g. `repec:gb`; `error` =
  `type: message`, single line).
- Append-only, O_APPEND line writes (same accepted concurrency semantics as
  `discovery_errors.jsonl`); never read by the crawler itself.
- The exception is still counted as `error` — counts and log lines unchanged.

### 2. Save-side dedup by source page (class A, the durable half)

- In `Storage.save()`, immediately after the `doc_id in self._ids` check and
  before the dry-run branch:

  ```python
  if rec.source_url and rec.source_url in self._source_urls:
      return "skip:known-source"
  ```

- Rationale: `source_url` (e.g. the IDEAS paper page) is a stable identity key
  for the document, exactly like pdf_url/sha256 — if a manifest row already
  carries it, the document is in the corpus and a re-listed variant (different
  or dead pdf_url ⇒ different doc_id) must not trigger a download attempt.
  This closes the gap for FULL sweeps (the PR #5 incremental skip only covers
  bounded nights, and only at listing level).
- Known non-goal (pre-existing behavior, unchanged): a revised PDF behind an
  already-known source page is not re-fetched; corrections are a separate
  concern.

### 3. One-shot reconciliation `repec-reconcile` (class A, the data half)

New CLI command `repec-reconcile --banks gb [--write] [--csv path]`:

- Enumerates each wired series (reusing `repec_check`'s existing machinery:
  series listing + matching waterfall — bank-specific number key, bank PDF URL
  on the IDEAS page, canonical title).
- For each listing entry with no manifest row carrying its IDEAS URL
  (`source_url`/alt match), tries to match an existing manifest row.
- **Write rule (strict):** stamp `source_url = <IDEAS paper URL>` on the
  matched row ONLY when (a) the match is unique (exactly one candidate row),
  and (b) the row's `source_url` is empty. Everything else — ambiguous title,
  multiple candidates, row already carrying a different source_url — is
  reported, never written. Dead RePEc pdf URLs are NOT added to `alt_urls`
  (they would pollute the download-fallback chain).
- Default is dry-run: prints and CSVs the would-be actions
  (`stamp | ambiguous | unmatched`) with row doc_ids and IDEAS URLs;
  `--write` applies stamps via the existing atomic `rewrite_manifest`
  (temp file + rename, per-bank).
- Idempotent: a second run finds nothing left to stamp.
- Expected effect on gb: ~374 stamps → those papers become skippable at
  listing level (incremental nights, `_source_urls`) AND at save level
  (Sunday full sweeps, §2) → gb error count drops to ~0. fr's 13 remain
  (they are `unmatched` — genuinely missing → phase 2 inventory).

### 4. Testing (adversarial fixtures, per the house rule)

- Audit file: a failing record writes exactly one well-formed JSON line with
  all fields; counts/log lines unchanged; dry-run never writes audit lines
  (nothing can fail before download in dry-run).
- save-side dedup: known source_url → `skip:known-source` before any fetch
  (spy fetcher asserts zero calls), in both dry-run and real mode; empty
  source_url never matches; `_source_urls` freshness after save/reindex.
- Reconciliation: fixtures with (i) a clean unique match → stamped in --write,
  reported in dry-run, file untouched in dry-run; (ii) TWO rows with the same
  canonical title → `ambiguous`, no write; (iii) row with non-empty
  source_url → no write; (iv) no match → `unmatched`; (v) idempotence
  (second --write run: zero stamps). Manifest rewrite goes through
  `rewrite_manifest` (atomicity inherited + covered by existing tests).
- Bash suite: unchanged (no run-job.sh change in phase 1 — the audit file and
  dedup live below the job layer).

### 5. Rollout

1. Merge + image rebuild + Dockge Update (pull!).
2. Run `repec-reconcile --banks gb` DRY-RUN via cb-campaign; Marc reviews the
   CSV (expected ≈ 374 stamps, ~0 ambiguous); then `--write` run; commit is
   pushed by the campaign autocommit.
3. Watch the next bounded night and the next Sunday sweep: expected
   `gb: {'skip': ~1128}` with `error ≈ 0`; `download_errors.jsonl` starts
   accumulating the real class-B/C inventory (fr/se/it/jp).
4. Phase 2 planning starts from that file.
