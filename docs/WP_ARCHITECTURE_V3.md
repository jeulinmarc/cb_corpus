# Working Papers v3 — Bank-native discovery, RePEc as completeness check

**Status: design document (not yet implemented).**

## Problem with the current (v2) pipeline

Today D1/D2 working papers are discovered **exclusively via RePEc/IDEAS**
(`cb_corpus/sources/repec.py`). Two structural defects:

1. **Indexing lag** — a paper appears on the bank's site first and on IDEAS
   days to ~2 weeks later. The corpus is permanently behind for recent docs.
2. **Month-only dates** — the `Creation-Date` ReDIF field the banks feed to
   RePEc contains only `YYYY-MM`. Every WP in the manifest is dated `YYYY-MM-01`.
   Verified across the whole RePEc ecosystem (IDEAS meta tags, EconPapers,
   RePEc API): day precision does not exist anywhere in RePEc.

## v3 architecture (target)

Invert the roles:

| Role | v2 (current) | v3 (target) |
|---|---|---|
| Primary discovery + dates | RePEc/IDEAS | **Bank website listings** (per-bank scraper) |
| Completeness / gap check | — (implicit) | **RePEc/IDEAS** (no download, audit only) |
| Legacy docs absent from bank sites | RePEc (date = YYYY-MM-01) | RePEc + **date-recovery waterfall** |

```
                ┌──────────────────────┐
   per bank ──▶ │ native WP scraper    │──▶ DocRecord (precise date) ──▶ download + manifest
                └──────────────────────┘
                          │
                          ▼  (audit, scheduled)
                ┌──────────────────────┐
                │ RePEc completeness   │──▶ missing-docs report
                │ check (no download)  │    ├─ doc on bank site too → re-run native scraper
                └──────────────────────┘    └─ doc ONLY on RePEc (legacy) →
                                               RePEc record + date-recovery waterfall
```

Scope of v3: the 5 big banks first — **us (Fed), ecb (ECB), jp (BoJ),
gb (BoE), de (Bundesbank)**. (PBoC excluded: no comparable WP series /
no RePEc archive; Bundesbank taken as #5.) All other banks keep the
v2 RePEc-only path unchanged. **D2 (occasional/discussion-adjacent series)
is in scope for all banks that have one** — natively for the five, via
RePEc for the rest (decision Q3 in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md);
requires wiring the D2 series handles into `SERIES`).

## Manifest schema additions

To make date quality auditable, add three fields to `DocRecord` / manifest rows:

```json
{
  "date_precision": "day | month | year",
  "date_source":    "bank_site | repec | wayback | pdf_meta | nep_bound | llm_crawl",
  "repec_handle":   "RePEc:ecb:ecbwps:20253117"
}
```

`repec_handle` is optional and stamped by `repec-check --write` (see
[REPEC_AS_CHECK.md](REPEC_AS_CHECK.md)) — once present, audit reruns match
by handle directly instead of the title/URL cascade.

Rows produced by the current pipeline are implicitly
`{"date_precision": "month", "date_source": "repec"}` for D1/D2.

## Documents in this folder

- [WP_NATIVE_SOURCES.md](WP_NATIVE_SOURCES.md) — per-bank listing pages,
  URL patterns, date formats, matching keys (verified June 2026).
- [REPEC_AS_CHECK.md](REPEC_AS_CHECK.md) — how to use RePEc purely as a
  completeness/dedup check: matching rules, "same paper → don't re-download".
- [DATE_RECOVERY.md](DATE_RECOVERY.md) — recovering the real publication date
  for legacy docs that exist only on RePEc.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — work breakdown, phases,
  complexity, ordering constraints.
- [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) — **decision record** (all questions
  answered 2026-06-12): full-history backfill, Fed day-precision everywhere,
  D2 for all banks, 45 d grace window, manifest in this repo, handles stamped
  for all wired banks, fail-loudly on scraper errors, pypdf only.

---

## Implementer's handoff — everything a fresh session needs

This section captures the codebase facts, verified findings and decisions
behind this design, so implementation can start cold from these docs alone.

### Codebase map (what matters for v3)

| File | Role |
|---|---|
| `cb_corpus/models.py` | `DocRecord` dataclass. **`doc_id = sha1(bank_code\|doc_type\|pdf_url)[:16]` — deliberately date-free** (comment in code says dates are mutable metadata). `to_row()` serializes to manifest. `alt_urls` is currently **runtime-only, not serialized** — migration must persist it (see below). |
| `cb_corpus/storage.py` | `Storage`: `data/manifest.jsonl` append-only JSONL. `_load_existing()` / `is_known_url()` = URL-based dedup (skips any URL already in manifest). `iter_manifest()`, `save_many()`, `reindex()`. No atomic-rewrite helper yet → phase 3 needs `rewrite_manifest(rows)` (temp file + rename). |
| `cb_corpus/adapters/base.py` | `BankAdapter.discover()` routes: C1 → BIS speech index; **D1/D2 → hard-routed to RePEc**; else `_discover_native()`. The v3 change is ~5 lines: check `self.native_types` first for D1/D2, RePEc as fallback. |
| `cb_corpus/sources/repec.py` | `SERIES` dict (13 banks wired: ecb→ecbwps/ecbops, us→fedgfe+fedgif, gb→boeewp, de→bubdps, jp→bojwps, + it/es/fr/ca/ch/se/nl/au). `_paper_meta()` reads `citation_publication_date` (YYYY/MM only); `_iso_date()` pads day=01 → **root cause of all the YYYY-MM-01 dates**. The bare `date` meta tag is deliberately ignored (it's the RePEc *index* date — once mis-used, it dated ~16k papers to the same day). |
| `cb_corpus/adapters/ecb.py` | `parse_year_includes`: ECB "lazyload-container" year-include mechanism — **reuse it for the ECB WP scraper** (pubbydate pages use the same widget). |
| `cb_corpus/sources/boe_wp.py` | ~90 % of the gb native scraper already exists (staff-WP sitemap walk). Promote to primary. |
| `cb_corpus/sources/wayback.py` | Wayback client — extend with a `first_capture(url)` CDX helper for date recovery (rung 1). |
| `cb_corpus/http.py` | `Fetcher`: retries, per-host throttle (`min_delay_seconds=0.5`), `verify=False` deliberate (urllib3 `InsecureRequestWarning` suppressed at import). `config.py`: `parallel_hosts=10`, timeout 30 s, download 90 s. |
| `cb_corpus/cli.py` | subcommand wiring (`discover`, `retry-html`, …) — add `repec-check`, `wp-dates` here. |

### Verified facts (live-checked June 2026 — do not re-litigate)

- **RePEc has no day precision anywhere**: IDEAS meta tags
  (`citation_publication_date` = `YYYY/MM`), EconPapers ("Date: 2025-09"),
  RePEc API — all reflect the bank's own ReDIF `Creation-Date`, which is
  month-only. Confirmed for ECB, Fed, BoJ, BoE, Buba series.
- **NEP dates ≠ publication dates**: NEP issues (`nep-xxx/YYYY-MM-DD` links
  on IDEAS paper pages) are *newsletter announcement* dates, 1–4 weeks late.
  Example: ECB WP 3117 → NEP 2025-09-29, usable only as an upper bound
  (`date_source="nep_bound"`).
  > **CORRECTION (2026-06-14):** the earlier "real ECB pubdate 2025-09-12" for
  > WP 3117 was wrong. The bank's own foedb date is **2025-09-22** (PDF ModDate
  > 2025-09-18; PDF CreationDate is a garbage 2016 template — illustrating why
  > PDF-meta date recovery is unreliable and bank_site is the right primary
  > source). The `bank_site` foedb date is authoritative.
- **Fed FEDS/IFDP year listings** (`federalreserve.gov/econres/feds/{YYYY}.htm`)
  show **month only**; the day requires the per-paper landing page
  (1 extra request per *new* paper — acceptable).
- **BoJ year listings** (`.../wps_{YYYY}/index.htm`) carry the exact day in
  the table ("25-E-13 | Nov. 13, 2025") — no extra requests.
- **ECB pubbydate year pages** show the exact day next to each item.
- **Bundesbank** PDF URLs are opaque blobs — never derive a URL, always read
  it from the paper page.

### Invariants & traps

1. **`doc_id` never changes** for an existing paper. All v3 operations on
   existing rows are metadata rewrites (date, precision, source, handle,
   alt_urls). If you find yourself changing a `doc_id`, stop.
2. **Migration BEFORE native download mode** (detailed warning in
   [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), phase 3). Native
   scrapers often see the same paper under a different URL; without the
   migration join, dedup treats it as new → mass re-download + duplicates.
3. **`alt_urls` must become persistent.** Today it's runtime-only. The
   migration writes native URLs into it; `is_known_url` must load it back
   from the manifest, or dedup protection dies on restart.
4. **Fresh-clone trap**: `is_known_url` skips every URL already in the
   manifest. A clone with a committed manifest but empty `data/raw/` would
   download *nothing*. The planned `fetch-from-manifest` command (download
   PDFs straight from manifest rows, verify `sha256`) is the fix — also the
   fast path to rebuild a corpus from the git-committed manifest
  (~12–15 h vs 2–3 days of full re-discovery).
5. **Fuzzy title matches are report-only, never auto-acted** (rule in
   [REPEC_AS_CHECK.md](REPEC_AS_CHECK.md)).
6. **Month constraint** in date recovery: RePEc's `YYYY-MM` is authoritative;
   any recovered day outside that month is rejected
   ([DATE_RECOVERY.md](DATE_RECOVERY.md)).

### Operational target (context for design choices)

- A VPS runs a daily cron: `discover --download` (native-first) → weekly
  `repec-check --write` → `wp-dates` on the legacy tail → `git push` of
  `data/manifest.jsonl` (~35–40 MB for 60k docs) and
  `data/wp_dates_index.jsonl` (committed, append-only: only *expensive /
  non-reproducible* date resolutions — wayback/pdf_meta/llm_crawl — with a
  mandatory `evidence_url`; native-dated docs never go in the index).
- Replicability goal: anyone cloning the repo replays committed metadata
  (run-once, replay-forever) and rebuilds PDFs via `fetch-from-manifest`.

### Conventions

- Docs in English; discussion with the maintainer in French.
- Tests: pure-helper functions + HTML fixtures (see `tests/test_framework.py`
  style). Scrapers should expose parse functions taking HTML strings.
- New dependency allowed for phase 4: `pypdf` (check `requirements.txt` first).
- Suggested first PR: **phase 0 (schema) + ECB native scraper + migration
  dry-run report**.
