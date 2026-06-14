# Implementation plan & complexity assessment — WP v3

Where the code goes, what it reuses, what's new, and how hard each piece is.
Companion to [WP_ARCHITECTURE_V3.md](WP_ARCHITECTURE_V3.md),
[WP_NATIVE_SOURCES.md](WP_NATIVE_SOURCES.md),
[REPEC_AS_CHECK.md](REPEC_AS_CHECK.md), [DATE_RECOVERY.md](DATE_RECOVERY.md).

## What the existing framework already gives us (good news)

- **`DocRecord.doc_id` is date-independent** (`models.py`: hash of
  bank|type|pdf_url only, with an explicit comment that dates are mutable
  metadata). Date enrichment = pure metadata rewrite, no file renames,
  no dedup churn. This was clearly anticipated.
- **Adapter plumbing**: `_discover_native(doc_type, since)` is the single
  hook to add a native WP source; pipeline/storage/dedup/retry are untouched.
- **Existing parsers to reuse**: ECB lazy-load year includes
  (`adapters/ecb.py: parse_year_includes`), BoE WP sitemap
  (`sources/boe_wp.py` — nearly the whole gb scraper exists), BoJ year-listing
  iteration (`adapters/listing_crawler.py`), Wayback client
  (`sources/wayback.py`), manifest IO (`storage.py: iter_manifest`).
- **Match keys**: RePEc handles are already in every v2 row's `source_url`
  (`.../p/ecb/ecbwps/20253117.html`) — the join key is parseable from the
  existing manifest, no re-crawl needed for migration.

## Work breakdown

### Phase 0 — schema (XS)

| What | Where |
|---|---|
| Add `date_precision: str = "day"`, `date_source: str = "bank_site"` and optional `repec_handle: str = ""` to `DocRecord`, serialize in `to_row`; **persist `alt_urls`** (currently runtime-only) | [cb_corpus/models.py](../cb_corpus/models.py) |
| Backward compat: `iter_manifest` rows missing the fields default to `month/repec` for D1-D2, `day/bank_site` otherwise; `_load_existing` must also index `alt_urls` for dedup | [cb_corpus/storage.py](../cb_corpus/storage.py) |

Risk: none. Manifest is JSONL, additive fields are free.

### Phase 1 — native WP scrapers, one module per bank (M each)

New package `cb_corpus/sources/wp_native/` (or extend each bank's adapter —
see "design choice" below):

| Bank | New code | Reuses | Complexity |
|---|---|---|---|
| ecb | `wp_ecb.py`: pubbydate year pages → (wp number, date, pdf_url, title) | `parse_year_includes` mechanism | **M** — lazy-load includes + D1/D2 regexes; date printed next to item |
| us | `wp_fed.py`: FEDS/IFDP year pages + per-paper landing page for the day (**always**, incl. backfill — Q2 decision) | `Fetcher` only | **M** — 2 series, landing-page fetch per paper; legacy URL variants pre-2015 |
| jp | `wp_boj.py`: year-listing table rows carry code+date+pdf in one page | listing iteration pattern | **S** — single table parse, day already on listing |
| gb | promote `boe_wp.py` to primary D1 source | ~90% exists | **S** — wire `sitemap_pages`/`paper_pdf` into `_discover_native` |
| de | `wp_buba.py`: paginated DP listing → paper page → blob PDF | `Fetcher` | **M** — pagination + opaque blob URLs (must read page, never derive); least-known site of the five |

Each scraper: ~80–150 lines + pure-helper tests with HTML fixtures (the
repo's established test style, cf. `tests/test_framework.py`).

Wiring: bank's adapter overrides `discover(D1/D2)` to call the native module
first; RePEc fallback stays for banks outside the five.

### Phase 2 — `repec-check` command (M)

New `cb_corpus/completeness_repec.py` + CLI subcommand in
[cb_corpus/cli.py](../cb_corpus/cli.py):

- **Prerequisite (Q3 decision — D2 for all banks)**: research pass to wire
  the D2/occasional-paper RePEc series handle for each bank into `SERIES`
  in [cb_corpus/sources/repec.py](../cb_corpus/sources/repec.py) (today
  only `ecbops` exists). One-off, ~an hour of IDEAS browsing.
- Enumerate IDEAS series listings — reuses `RePEcDiscovery` crawl, **minus**
  per-paper page fetches for matched papers (listing alone gives handle+title).
- Match cascade key→URL→title (normalizer is a 10-line pure function,
  heavily unit-testable).
- Report: stdout + CSV in `data/reports/`. No downloads by construction.

Complexity driver: URL normalization edge cases (doubled slashes `//pub/`,
`~hash` suffixes, http/https). All pure functions → cheap to test.

### Phase 3 — v2 date migration, one-off (S)

Script-level: dry-run native discovery (phase 1) × `iter_manifest` join ×
in-place rewrite of `date`/`date_precision`/`date_source`. Needs a
`Storage.rewrite_manifest(rows)` helper (atomic: write temp file, rename).
Zero downloads, zero id churn (guaranteed by doc_id design).

> ⚠️ **Ordering constraint — migrate BEFORE switching native scrapers to
> download mode.** `doc_id = sha1(bank|type|pdf_url)` and dedup is
> URL-based (`storage.is_known_url`). The native scraper will often find
> the *same paper under a different URL* than the RePEc-era row
> (Bundesbank blob URLs, ECB redirects…). If `discover --download` runs
> natively on the back-catalogue before migration, every URL mismatch is
> seen as a new document → mass re-download + duplicates under new ids.
> The migration prevents this: the join (key → URL → exact title) maps
> native records onto existing rows, keeps the original `doc_id`/`pdf_url`/
> file, rewrites metadata only, and registers the native URL in `alt_urls`
> so dedup recognises it forever after. Once migrated, flipping D1/D2 to
> native-first causes **zero re-downloads** — only genuinely new papers
> enter with native URLs as their key.

### Phase 4 — `wp-dates` recovery waterfall (M-L)

New `cb_corpus/sources/wp_dates.py` + CLI subcommand:

| Rung | Effort | Note |
|---|---|---|
| Index lookup (`data/wp_dates_index.jsonl`) | XS | read/append JSONL |
| 1. Wayback CDX first-capture | S | extend existing `wayback.py`; rate-limit 1 r/s |
| 2. PDF `/CreationDate` | S | pypdf on `local_path`; **new dependency** (or reuse if already in requirements) |
| 3. Cover-page regex | M | text extraction quality varies; en+de month names; jp later |
| 4. NEP bound | S | parse `nep-xxx/YYYY-MM-DD` links from IDEAS paper page |
| LLM-assisted tail | — | manual/agentic process, outside the codebase; only its *output* (index entries with `evidence_url`) is consumed |

Month-constraint validator shared by all rungs (pure function).

### Phase 5 — CLI & docs glue (S)

`discover` keeps working unchanged (native scrapers slot in behind the same
command); new subcommands `repec-check`, `wp-dates`; README note.

### Phase 6 — `fetch-from-manifest` command (S, independent)

Rebuild `data/raw/` from a committed manifest: iterate `iter_manifest`,
download each `pdf_url` (fall back to `alt_urls`), verify `sha256`, write to
`local_path`. Motivation: `is_known_url` skips every manifested URL, so a
fresh clone with manifest-but-no-PDFs downloads *nothing* via `discover` —
this command is the supported rebuild path (~12–15 h for 60k docs at current
throttle vs 2–3 days of full re-discovery). Independent of all other phases.

## Design choice to settle before coding

**Where do native WP scrapers live?** Two options:

1. **`_discover_native` on each adapter** (consistent with A/B/E/F today).
   Pro: zero new architecture. Con: `discover(D1)` currently hard-routes to
   RePEc in `base.py` — needs a "native-first, repec-fallback" override.
2. **Separate `sources/wp_native/` package** registered like `boe_wp.py`.
   Pro: keeps WP logic out of adapters; mirrors how C1/BIS is centralized.
   Con: one more registry.

Recommendation: **option 1** — change `BankAdapter.discover` so D1/D2 check
`self.native_types` first; a bank that declares `DocType.D1` in
`native_types` uses its own listing, everyone else falls through to RePEc.
That's a ~5-line change in [base.py](../cb_corpus/adapters/base.py) and the
five scrapers become ordinary `_discover_native` branches.

## Overall assessment

| Phase | Size | Risk |
|---|---|---|
| 0 schema | XS | none |
| 1 scrapers ×5 | 2×S + 3×M | site-structure drift (mitigated: fixture tests, errors recorded by `_fetch_text`) |
| 2 repec-check | M | matching edge cases — pure functions, testable |
| 3 migration | S | must be atomic; dry-run first |
| 4 wp-dates | M-L | Wayback availability, PDF text quality — but failure mode is "stay at month precision", never corruption |
| 5 glue | S | none |
| 6 fetch-from-manifest | S | dead URLs over time (mitigated: `alt_urls` fallback, report failures) |

Total: a **medium project, no architectural rewrite** — the v2 framework
(date-free doc_id, `_discover_native` hook, recovery-source pattern) was
built for exactly this kind of extension. Phases are independently shippable
in order 0 → 1(ecb) → 3 → 1(rest) → 2 → 4; ECB-first delivers the biggest
date-quality win immediately (largest D1 corpus, easiest precise-date source).
The only hard sequencing rule: **phase 3 (migration) must complete for a
bank before its native scraper goes live in download mode** (see the
warning under phase 3) — dry-run discovery is always safe.

Suggested first PR: phase 0 + ECB scraper + migration dry-run report.
