# RePEc completeness audit — findings (2026-06-14)

Output of `repec-check` (phase 2) across all 13 wired WP banks: how many IDEAS
papers per series are covered by the manifest, and how many are missing. Read-only
(no downloads). `missing_recent` = within the last 45 days (native discovery lag);
`missing_legacy` = older (ingest via RePEc + date recovery). Full list:
`data/reports/repec_check.csv` (gitignored).

| Bank | RePEc total | Covered | Missing (legacy) | Manifest D1/D2 |
|---|---|---|---|---|
| us | 4203 | 4189 | 14 | 4188 |
| ca | 1360 | 1355 | 5 | 1355 |
| it | 1211 | 1205 | 6 | 1205 |
| gb | 1128 | 1094 | **34** | 1248 |
| es | 1091 | 991 | **100** | 1289 |
| fr | 1007 | 992 | 15 | 992 |
| de | 691 | 689 | 2 | 689 |
| au | 536 | 536 | 0 | 536 |
| jp | 405 | 402 | 3 | 402 |
| se | 391 | 366 | 25 | 366 |
| ch | 318 | 318 | 0 | 318 |
| nl | 200 | 200 | 0 | 200 |

**Headline:** coverage is high everywhere (≥ 91%); **0 `missing_recent`** for every
bank — native discovery is keeping up. au/ch/nl are 100%.

**es / gb — investigated (a `--write`-free, paper-page second pass was added).**
`repec-check` now matches in cascade handle → key → listing-title → **fetch the
IDEAS paper page for the leftovers** and re-match by the bank PDF URL or the full
canonical title. That recovered only +2 each, so the remainder are **not** a
handle-format artifact — they are real discrepancies of two kinds:

- **Language-variant duplicates:** IDEAS lists e.g. `bde:wpaper:2618` (Spanish)
  *and* `bde:wpaper:2618e` (English) as separate papers; the corpus holds one, so
  the other language reads as "missing". Cosmetic — same paper.
- **Genuinely old papers** the corpus lacks (e.g. es `9918/9919/9922`, 1999; the
  old BoE WPs IDEAS keeps under dead pre-2014 URLs). These are real
  `missing_legacy` candidates → ingest via RePEc + `wp-dates`.

Refined counts after the page-fetch pass: **es 98, gb 32**; small genuine tails
elsewhere (us 14, se 25, fr 15, it 6, ca 5, jp 3, de 2). Full list:
`data/reports/repec_check.csv`.

> **Conclusion:** not a matching bug — the audit is correctly surfacing language
> duplicates (ignore) + a real legacy tail (ingest). `0 missing_recent` everywhere,
> so the daily native cadence needs no change. A future refinement could fold
> `{id}e`/`{id}r` language/revision variants onto their base id to drop the
> cosmetic duplicates from the report.
