# Silent Series Fixes (ecb D3, ecb C2, se D1) — Design

**Date:** 2026-08-24 · **Scope:** one PR (branch fix/silent-series, worktree) ·
**Source:** read-only diagnosis of the six silent series from the production review.

## Verdicts driving this PR

1. **ecb D3 (blog/economic letters)** — D3 was never in `ECBAdapter.native_types`;
   the 212 existing docs came from `run_ecb_pub_recovery("blog", ...)`, a manual
   one-off never wired into any CLI command; AND its per-year endpoint
   (`/press/blog/date/<year>/html/index_include.en.html`) is now 404 for every
   year. The human-facing master listing `/press/blog/html/index.en.html` is
   alive (200, static HTML, posts inlined through 2026-08-17).
2. **ecb C2 (interviews)** — same structural gap; per-year endpoint 404 for
   2025+, alive for ≤2024; master listing is JS-rendered (unscrapable by plain
   fetch). Real new interviews exist upstream (e.g. 2026-07-15 Ouest-France).
3. **se D1 (Riksbank WPs)** — connector (RePEc) is current with IDEAS, but
   IDEAS lags the bank: riksbank.se already lists #466-#471 (live PDFs) vs
   corpus/IDEAS at #465. Real 6-paper gap, growing.
4. **ca E1, it D1, es D1 — false alarms, no code change**: connectors verified
   current with their upstream sources. Root cause is the review's flat
   docs-per-window statistic penalizing lumpy/quarterly cadences —
   methodology note handed to the cadence-watchdog work (PR 4), documented in
   this spec as the honest closure of those three investigations.

## Design

**A. ecb D3 — full fix.** Add D3 to `ECBAdapter.native_types` with a
`_discover_native` branch sourcing the master blog listing
`/press/blog/html/index.en.html`: parse anchors matching
`/press/blog/date/\d{4}/html/.*\.en\.html$` (reuse the adapter's existing
URL/date parsing idioms), day-precision dates from the URL/labels as
available. Bounded nightly behavior consistent with the adapter's other
native types. Existing 212 rows stay authoritative (standard dedup).

**B. se D1 — native Riksbank source.** New `cb_corpus/sources/riksbank_wp.py`
walking `riksbank.se/en-gb/press-and-published/publications/working-paper-series/`
(static server-rendered; PDF URLs under
`/globalassets/media/rapporter/working-papers/<year>/`): WP number, title,
date (site precision — verify day vs month on sample), scraped PDF links
(never derived). Wired like the other native WP sources; RePEc series stays
active for `se` (dual-source, dedup by stable keys — same coexistence as
other WP-v3 banks); `alt_urls` carry the cross-source URL when matchable by
WP number to prevent re-downloads (the WP-v3 zero-redownload guard).

**C. ecb C2 — interim fix, honestly labeled.** Wire C2 into
`native_types`/recurring discovery with the per-year include pages as PRIMARY
(they still serve ≤2024 and may resurface) and the ALREADY-CODED Wayback CDX
fallback (`cdx_fallback_prefix`) engaged for the dead years. This yields
delayed/partial coverage of 2025-2026 (whatever the archive captured) — the
limitation is stated in the adapter docstring and the PR. Proper live-source
discovery (ECB's JS search API) = explicit follow-up, out of scope.

## Testing

TDD, fixture-driven (no live network in unit tests): master-blog listing
fixture (incl. a malformed anchor), Riksbank listing fixture (number/date
extraction, PDF-link scraping, a row with an unexpected filename), C2 wiring
tests (native_types inclusion, fallback engagement on 404 fixture). Full
suite green before every commit.

## Out of scope

ecb C2 live JS-source reverse-engineering; monitor-methodology changes
(→ PR 4); any backfill data operations (Sunday full sweep does it in prod).
