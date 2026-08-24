# Native Banque de France Working-Paper Source (fr D1) — Design

**Date:** 2026-08-24 · **Status:** assumptions announced to Marc (D1-only, full depth, stable-key dedup), recon done · **Scope:** one PR

## Problem

fr D1 has been silent 213 days: BdF working papers were RePEc-only, and that
feed stalled (last 2026-01-23) while the bank kept publishing (WP n°1060 on
2026-08-21). fr is the only big bank without a flowing native WP source.

## Recon facts (verified 2026-08-24)

Two publication systems, both plain server-rendered HTML, both crawlable
(robots.txt: no restrictions; legacy host has none):

- **Legacy** `publications.banque-france.fr` — frozen archive 1994→Sept 2023
  (last n°924). Per-year pages
  `/liste-chronologique/documents-de-travail_year={YYYY}.html` (via
  `/node/1651/year/{YYYY}` redirect), ALL papers of a year on one page with
  full metadata per row: `n°{NUM}`, title, authors, `Publié le DD/MM/YYYY`
  (sampled days are all `01/` → treat as **month precision** unless a wider
  sample proves day precision), PDF link. Year list gap at 1995 (probe it
  anyway); 2024+ redirects broken → skip-gracefully per boj_wp's pattern.
- **New** `www.banque-france.fr/en/publications-and-research/our-main-publications/working-papers`
  — 2018→present, `?page=0..N` pagination (12/page), listing card = title +
  **day-precision** date; detail page (or its `og:description` meta) carries
  authors and the WP number as free text (`Working Paper Series no. 1060.` /
  `Document de travail n° 1060.`) → regex extraction. PDF:
  `/system/files/{YYYY-MM}/WP{NUM}.pdf`. FR and EN pages share one PDF —
  ingest once (EN listing as primary source_url).
- **PDF filenames are inconsistent on legacy** (`document-de-travail_…` vs
  `working-paper_…`, revised-version suffixes) → ALWAYS scrape the link,
  never derive a filename.
- **WAF risk**: both hosts 403 generic bot UAs (curl with browser UA passes).
  The connector must work with the repo's `Fetcher` as configured — verify
  its headers against both hosts FIRST; if blocked, the fix belongs in the
  connector's request headers (per-source override if the house Fetcher
  already passes elsewhere), never a global UA change without checking other
  sources.

## Design

One new module `cb_corpus/sources/bdf_wp.py` exposing
`discover_fr_wp(fetcher, since=None, years=None) -> Iterator[DocRecord]`,
mirroring the existing native-WP interface (boj_wp/buba_wp):

1. **New-system walker** (template: buba_wp.py): paginate `?page=N`; stop
   early in bounded mode (`since`: stop when a full page is older); per
   paper: fetch detail page, regex WP number + authors from og:description /
   body; `date_precision="day"`, `date_source="bank_site"`,
   `provenance="bank_site"`, `source_url`=detail page, scraped PDF URL.
2. **Legacy walker** (template: boj_wp.py): iterate year pages 1994..2023
   (probe 1995 too); parse rows content-based (not column-position);
   `date_precision="month"` (day-01 normalization) unless the row's sampled
   dates prove day precision; skip missing/empty years gracefully.
3. **Dedup within the source**: WP **number** is continuous across both
   systems → key the merged stream on it; overlap era (2018-2023) prefers
   the NEW system's record (day precision beats month).
4. **Dedup against the corpus** (RePEc-era rows): standard Storage identity
   (pdf_url/doc_id + sha256 content hash + known source_url) — plus stamp
   `repec_handle` when the IDEAS join is available later (out of scope to
   backfill here).
5. **Wiring**: register fr D1 in the same native-discovery path the other
   WP sources use (pipeline registry / banks_sources wiring — follow how
   buba_wp is invoked, including the nightly bounded vs Sunday-full modes);
   `bank_code="fr"` exists in banks_sources.toml (only sitemap E2 today).

## Testing

TDD with saved-fixture HTML (adversarial: legacy row with revised-version
filename, mixed `document-de-travail_`/`working-paper_` prefixes, a paper
whose og:description lacks the number → detail-body fallback → else skip
with warning, empty year page, page with fewer than 12 items, since-bounded
early stop). No live network in unit tests. One optional live smoke test
marked like the integration marker convention.

## Rollout

Merge → nightly bounded run picks new papers; the FIRST full run (Sunday or
manual `sync full`) walks the whole native corpus (1994→present, both
systems) and dedups it against what fr already holds. Measured 2026-08-24: fr
already holds ~997 of the ~1060 known WP numbers (RePEc-era backfill), so the
native walk resolves almost entirely to **content-hash dedup, not new rows**
— expect only **~50-64 new manifest rows** (the numbers RePEc never carried:
gaps, late additions, the pre-RePEc tail), against **~700+ PDF downloads**
that get fetched, hashed, and discarded as already-known on that first sweep.
Announce BOTH numbers in the PR so the operator's anomaly baseline reads the
download-volume spike as expected dedup traffic, not as ~600-1000 new
documents landing. The 13 recovered fr docs and RePEc-era rows stay
authoritative where already present (dedup keeps them; native adds only
what's missing). Wiring fr into `wp_migrate.py` (join on the WP number,
stamping native URLs into `alt_urls` before the bank flips to native-only
discovery — see `adapters/base.py`'s precondition) is what turns that
one-time ~700-download sweep into a recurring near-zero one: once alt_urls
carries the native URL, `is_known_url()` skips it pre-download on every
subsequent run.

## Out of scope

Other BdF doc types; backfilling repec_handle joins; French-page bilingual
metadata capture (source_url = EN page; FR slug noted in alt_urls only if
trivially available).
