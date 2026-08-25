# ECB C2 live source (foedb) — design spec

**Status: DRAFT — awaiting Marc's validation. Decision points for Marc are marked ⚖️.**

## Problem

ECB C2 (interviews / op-eds) is discovered by an INTERIM source (PR #11): per-year
Wayback CDX fallbacks around the old `/press/inter/` listing. It lags the live site,
produces generic titles, and one corrupted URL. Reconnaissance (2026-08-25) proved the
live mechanism: interviews are records of the **same foedb JSON DB the repo already
consumes for D1/D2** (`publications.en`, consumed by `cb_corpus/sources/ecb_foedb.py`),
with `type == 27` ("ECB Interview" per the `publications_types` map DB). 611 records
2004→today; the manifest's 604 C2 rows match it 601/604 by URL; the DB had an interview
published *yesterday* — zero lag.

## Design

1. **Discovery = full-DB walk + `type == 27` filter**, structurally identical to
   `discover_ecb_wp` (global `pub_timestamp` DESC sort → same early-stop-on-`since`
   incremental behavior), reusing the existing `ecb_foedb.py` helpers
   (`parse_versions`, `parse_metadata`, `chunk_records`, `_record_date`, `_abs_url`).
   ⚖️ *Alternative considered and rejected*: the `indexes/type/` route (fewer fetches
   but new sort_id→chunk/offset math, and a position-not-value trap on
   `index_value_id`). The full walk is ~81 small JSON chunks, the exact cost D1/D2
   discovery already pays nightly — YAGNI.
2. **Emitted rows**: `pdf_url` = absolute `documentTypes[0]` (always a single
   `/press/inter/date/<year>/html/...html` entry), `title` =
   `publicationProperties.Title` (real titles), `date` from `pub_timestamp` in
   Europe/Berlin (day precision, `date_source="bank_site"`), `mime_type="text/html"`,
   `source_url` = the foedb DB URL (D1/D2 convention). Storage's existing HTML path
   handles the rest (html_path, optional PDF render) — unchanged downstream.
3. **Type-id honesty guard**: resolve the id from the `publications_types` DB each run
   (find "ECB Interview" → id, expect 27) instead of hard-coding 27 silently; if the
   name is missing or maps elsewhere, fail loudly (no silent empty harvest). The id
   stays an env-overridable constant only if Marc prefers — default: dynamic resolve.
4. ⚖️ **Language policy — recommend EN-only (status quo)**: 9 of 611 interviews
   (2013–2020) exist only in it/nl/fr/de. The interim path drops them; keeping EN-only
   is consistent with the whole corpus. They are *documented* as a known exclusion in
   the module docstring (honest-gap principle). Including them later = flip one filter.
5. **Date policy**: foedb `pub_timestamp` wins over URL-encoded yymmdd (4/611
   disagree) — same rule the WP migration established. Safe: doc_id ⊥ date.
6. **Interim code removed**: `_discover_inter`'s CDX/Wayback fallback and its
   constants are deleted (git history keeps them); the INTERIM module-docstring
   paragraphs replaced by the foedb description.
7. ⚖️ **One-shot in-branch enrichment of the existing 604 C2 rows** (wp-migrate
   precedent — data amendment in the PR branch, no re-download): match manifest rows
   to foedb records by normalized URL (601 match) and stamp real `title` +
   foedb `date`/`date_precision="day"`/`date_source="bank_site"` onto them. Also fix
   the ONE corrupted URL row (`in220114...html/nter/...` concatenation artifact →
   foedb's clean form goes to `alt_urls`, or pdf_url corrected — the enrichment
   script reports both options' row before applying; corrected pdf_url changes doc_id,
   so the safe amendment is: keep row identity, stamp clean URL as alt_urls entry).
   Report CSV under `data/reports/` (same style as wp_migrate).

## Accepted risks (documented, no code)

- **URL churn** (ECB re-issues a page under a new `~hash`, old URL stays live —
  observed once): the new URL is yielded, content-hash dedup in `Storage.save` is the
  backstop; if bytes differ, the duplicate row is caught later by recover's
  self-healing (PR #13) machinery. One known concrete pair (in191202) — the
  enrichment pass stamps the new-hash URL as alt_urls on the existing row, closing it
  preemptively.
- **Reclassification churn** (in210831 moved interviews→blog): historical manifest
  rows keep their truth; a cross-type duplicate (C2 + D3 identities) is possible and
  acceptable — different doc_type is a different document identity by design.
- **Torn reads across a DB rebuild** (versions.json max-age 60s): same exposure
  D1/D2 already accepts — fail loudly, next night self-corrects.

## Tests

- Unit on the type-27 filter + guard (fixture chunks with mixed types; guard failure
  raises).
- Row-shape test (URL absolutized, title, Berlin date, mime/text-html, source_url).
- Incremental early-stop honored (`since` cuts the walk, same as WP tests).
- EN-only filter (a non-.en.html record is excluded and counted/logged, not silent).
- Enrichment script: URL-matched row gains title/date; non-matching rows untouched;
  corrupted-URL row handled as specced; CSV report written.
- Suite green: `python3.13 -m pytest tests/ -q`.
