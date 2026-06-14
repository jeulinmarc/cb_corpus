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

**Two banks worth a look (likely a matching artifact, not real gaps):**
- **es (100 missing, yet manifest 1289 > RePEc 1091):** the manifest has *more*
  Spain rows than IDEAS lists, so the 100 "missing" are almost certainly
  handle/number-format mismatches (the IDEAS `bde:wpaper` id format vs the stored
  `source_url` handle), not absent papers. Needs a quick key-normalization check.
- **gb (34 missing, manifest 1248 > RePEc 1128):** similar — BoE WP numbers aren't
  in URLs, so a few IDEAS papers may not exact-title-match the bank-site titles.

> The `manifest > repec_total` cases confirm these are match misses, not corpus
> gaps. The genuinely-actionable small counts (us 14, se 25, fr 15, it 6, ca 5,
> jp 3, de 2) are candidate papers to ingest — but every one is `missing_legacy`,
> so they go through the normal RePEc + `wp-dates` path, not a native re-run.

**Next:** tighten the es/gb match keys (normalize the IDEAS id ↔ stored handle),
re-run `repec-check`, then ingest any true leftovers. No `missing_recent` means
the daily native cadence needs no change.
