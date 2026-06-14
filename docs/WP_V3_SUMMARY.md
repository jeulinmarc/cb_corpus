# Working Papers v3 — implementation summary (2026-06-14)

What shipped against the v3 design ([WP_ARCHITECTURE_V3.md](WP_ARCHITECTURE_V3.md)):
bank-native working-paper discovery for the 5 big banks, with RePEc demoted to an
audit/recovery role and true publication dates everywhere they exist.

## Results — all 5 big banks migrated to bank-site dates

| Bank | Native source | D1/D2 migrated | Day | Month | Legacy (RePEc) |
|---|---|---|---|---|---|
| **ecb** | foedb JSON DB | 3630 | 3630 | – | 0 |
| **us** (Fed) | FEDS/IFDP pages + landing | 3831 | 1003 | 2828 | 357 |
| **jp** (BoJ) | wps_rev year tables | 398 | 398 | – | 4 |
| **gb** (BoE) | staff-WP sitemap + pages | 1181 | 1181 | – | 67 |
| **de** (Buba) | bbksearch result list | 617 | 617 | – | 72 |

≈ **6,800 working papers now carry true day-precision dates** (+2,828 Fed
confirmed-month). `doc_id`/`sha256`/`local_path` are never touched — every change
is a metadata rewrite, zero re-downloads (each flip live-verified). Native
discovery also surfaced papers the corpus lacked (de ~462, plus smaller counts
elsewhere) — discoverable via `discover --download`.

## Per-source notes

- **ecb**: the pubbydate page is a client-side **`foedb` JSON database**, not the
  `lazyload` HTML widget the doc assumed. Read `versions.json → metadata.json →
  data/0/chunk_N.json`; filter `scpwps`/`scpops`. Exact day, full archive, no
  per-paper fetch.
- **us**: day comes from each paper's landing `citation_publication_date`
  (MM-DD-YYYY), with a **month-constraint** so a revised paper's later date can't
  overwrite the original month. Only ~2017+ papers have a real day (see below).
  See [FED_MIGRATION_NOTES.md](FED_MIGRATION_NOTES.md).
- **jp**: the wps_rev year tables print the exact day inline (3/4/5-column layouts
  across eras) — no per-paper fetch.
- **gb**: BoE WP numbers aren't in URLs, so the join is by **slug** + an
  exact-title tier (recovered 197 slug-drift rows).
- **de**: JS-only listing cracked via chrome-devtools (`bbksearch` endpoint);
  old papers = direct blob, recent = slug page. See
  [DE_MIGRATION_NOTES.md](DE_MIGRATION_NOTES.md).

## Architecture

- Hand-written adapter per bank (`adapters/{ecb,fed,boj,boe,buba}.py`); jp/gb/de
  moved out of `banks_sources.toml`. `base.discover` routes D1/D2 native-first
  only when the type is in `native_types`, with a `_skip_known_url` hook
  (injected by the pipeline) so a flipped bank never re-downloads its migrated
  back-catalogue.
- **Per-bank manifests** `data/manifest/<bank>.jsonl` (auto-split from a legacy
  single file; atomic per-bank rewrite). Committed to git for replayability.
- `wp_migrate.py` is generic: `_NATIVE` / `_KEY_FROM_PDF` / `_KEY_FROM_HANDLE`
  dicts + an **exact-normalized-title** match tier (unambiguous-only, never
  fuzzy). Adding a bank = one scraper + two key functions.
- `wp_dates.py` (phase 4): recover the day for legacy/month rows via PDF
  `/CreationDate` + Wayback first-capture (month-constrained), into the committed
  `data/wp_dates_index.jsonl` (run-once, replay-forever, `evidence_url` per entry).

## On Fed pre-2017 dates (the honest part)

The Fed assigns each working paper a **series month** and did not publish a day
before ~2017. Verified across six sources — landing `citation_publication_date`,
RePEc, FRASER, the PDF cover, the PDF body ("This Version: …"), and the PDF
`/CreationDate` — all give only the month. So ~2,800 Fed rows are honestly
month-precision, not a tooling gap. `wp-dates` recovers a day for the ~12% that
were archived (Wayback) in their series month; the rest stay month. This matches
the design's expectation ("post-2000 → day, 1990s → month").

## Commands

```
python -m cb_corpus wp-migrate --banks ecb,us,jp,gb,de [--write]   # dates from bank sites
python -m cb_corpus wp-dates   [--banks …]            [--write]    # recover legacy days -> committed index
python -m cb_corpus discover   --banks de --types D1 --download    # fetch newly-discovered papers
```

## Replicability

`data/manifest/<bank>.jsonl` + `data/wp_dates_index.jsonl` are committed. A fresh
clone replays every date with **no re-crawl**; PDFs are rebuilt from the manifest
(`fetch-from-manifest`, planned). The expensive Wayback/foedb work runs once.

## Status of the v3 phases

- Phase 0 (schema) ✅ · Phase 1 (5 native scrapers) ✅ · Phase 3 (migration) ✅ ·
  Phase 4 (wp-dates) ✅ · Phase 5 (CLI/docs) ✅
- Phase 2 (repec-check completeness audit) — pending · Phase 6 (fetch-from-manifest)
  — pending · D2 handles for non-five banks (Q3) — pending.
