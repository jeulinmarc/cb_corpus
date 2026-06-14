# RePEc as a completeness check (no downloads)

In v3, RePEc/IDEAS stops being a *source* for the 5 big banks and becomes an
**audit oracle**: it answers "is the corpus missing any paper?" without ever
downloading a document.

## Why RePEc is a good oracle

- The banks themselves maintain their RePEc archives (official ReDIF feeds),
  so series coverage is near-exhaustive.
- Series are flat, numbered, easy to enumerate (`ideas.repec.org/s/<handle>.html`
  with pagination) — one cheap crawl per series.
- Its weaknesses (month-only dates, 1-2 week lag) don't matter for an
  *audit*: we only need the paper's existence, identity and approximate date.

## Proposed command

```
python -m cb_corpus repec-check [--banks us,ecb,jp,gb,de] [--csv path] [--write]
```

**This command never downloads PDFs**, in either mode. Default is a pure
dry-run report (stdout + optional CSV in `data/reports/`). With `--write`,
it additionally persists match results into the manifest (metadata only —
see "`--write` mode" below).

## Algorithm

1. **Enumerate RePEc** — for each wired series (`SERIES` in
   `cb_corpus/sources/repec.py`), crawl the IDEAS series listing and collect,
   per paper: handle, number, normalized title, month-date. (Paper pages only
   need fetching for papers that fail to match in step 3 — keeps the crawl
   light and polite.)
2. **Load the manifest** — all D1/D2 rows for the requested banks.
3. **Match** RePEc records against manifest rows. A RePEc record is *covered*
   (→ no need to download anything) if ANY of:
   - **key match**: the bank-specific number (see table in
     [WP_NATIVE_SOURCES.md](WP_NATIVE_SOURCES.md)) extracted from the RePEc
     handle equals a number extracted from a manifest row's `pdf_url` /
     `source_url`;
   - **URL match**: the RePEc download URL equals a manifest `pdf_url`
     (after normalization: scheme, doubled slashes, `~hash` suffix stripped);
   - **title match**: normalized titles are equal (see below).
4. **Classify the leftovers** (RePEc records with no manifest match):
   - `missing_recent` — paper's month is within the native scraper's reach
     (bank site still lists it) → fix = re-run the native scraper; if it
     still doesn't appear, the native scraper has a bug → investigate.
   - `missing_legacy` — paper is old / no longer on the bank site → ingest
     via the RePEc record itself + date recovery
     ([DATE_RECOVERY.md](DATE_RECOVERY.md)).
5. **Reverse check** (manifest rows with no RePEc match) — two cases:
   - **`pending_repec`** (expected, not an anomaly): row's `date` is within
     the last ~45 days → the paper was fetched from the bank site before
     RePEc indexed it (RePEc lags days to ~2 weeks; 45 d gives margin).
     This is the normal v3 flow — native discovery is *ahead* of RePEc by
     design. Reported informationally; auto-resolves at a later check once
     RePEc catches up.
   - **`unmatched_old`**: older rows with no RePEc record. Usually benign
     (paper withdrawn from RePEc, series gap, non-WP stray picked up by a
     scraper), but a systematic excess hints at a scraper bug → review.

## Title normalization (the dedup rule)

Same title ⇒ same paper ⇒ **never download again**. Normalization before
comparison:

```
lowercase
→ unicode NFKD, strip accents/diacritics
→ replace any non-alphanumeric run by a single space
→ strip leading/trailing spaces
→ collapse internal whitespace
```

Match on equality of the normalized strings. Optionally add a fuzzy tier
(e.g. Levenshtein ratio ≥ 0.95) flagged as `match_fuzzy` in the report for
human review — punctuation and subtitle drift between bank sites and RePEc
is common ("ECB-global" vs "ECB-Global", em-dash vs hyphen, etc.).

**Never auto-act on fuzzy matches.** Exact-normalized matches are safe to
treat as duplicates; fuzzy ones go to the report only.

## `--write` mode: enrich the manifest with `repec_handle`

For every manifest row matched in step 3 (key / URL / exact-title only,
never fuzzy), `--write` stamps the RePEc handle into a new metadata field:

```json
{"repec_handle": "RePEc:ecb:ecbwps:20253117"}
```

Why this is safe and useful:

- `doc_id = sha1(bank|type|pdf_url)` — adding `repec_handle` changes nothing
  about identity, file, `sha256` or `local_path`. Pure metadata update,
  zero re-downloads.
- **Reruns get faster and stricter**: rows that already carry a handle are
  matched directly by handle on the next check, skipping the title/URL
  cascade entirely. Only un-stamped rows go through the cascade.
- **`pending_repec` self-heals**: a recent paper absent from RePEc today
  gets its handle stamped automatically at a later `--write` run, once
  RePEc has caught up. Each rerun incrementally completes what's missing.
- Rows with no RePEc match keep no field (absent ≠ empty), so the reverse
  check stays meaningful.

Recommended workflow: `repec-check` (inspect the report) →
`repec-check --write` (persist handles, and date fixes during the v2
migration below).

## Migration of v2 rows (one-off)

The current manifest has thousands of D1/D2 rows with `date = YYYY-MM-01`
sourced from RePEc. When native scrapers land:

1. Run the native scraper in dry-run for the full history.
2. Join native records to manifest rows with the same match cascade
   (key → URL → exact title).
3. For each match: rewrite the row's `date` with the bank-site date, set
   `date_precision="day"`, `date_source="bank_site"`, and stamp
   `repec_handle` while we're at it. If the native scraper found the paper
   under a different URL, register it in `alt_urls` so dedup recognises it
   — **this is what prevents mass re-downloads when native scrapers go
   live** (see the ordering warning in
   [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)). Keep `doc_id`,
   `sha256`, `local_path` untouched — the PDF on disk is the same file.
4. Rows that match nothing on the bank site are `missing_legacy` →
   [DATE_RECOVERY.md](DATE_RECOVERY.md).

This fixes dates in place with **zero re-downloads**.

## Scheduling

- Native scrapers: run with the regular `discover` cadence.
- `repec-check`: weekly is plenty (RePEc itself lags by days anyway).
- Treat a non-empty `missing_recent` bucket as a CI-style failure signal.
