# Native WP sources — the 5 big banks (verified June 2026)

For each bank: where the WP listing lives on the bank's own site, what date
precision it gives, how to extract the PDF, and the **matching key** used to
join against RePEc records (see [REPEC_AS_CHECK.md](REPEC_AS_CHECK.md)).

Summary:

| Bank | Listing | Date precision on bank site | Matching key vs RePEc |
|---|---|---|---|
| ecb | pubbydate year pages | **day** | WP number (`wp3117`) |
| us (Fed) | FEDS/IFDP year pages | month on listing, **day** on landing page | FEDS/IFDP number (`2025-110`) |
| jp (BoJ) | WP year pages | **day** (on the listing itself) | paper code (`25-E-13`) |
| gb (BoE) | staff-WP sitemap + pages | **day** (on paper page) | normalized title |
| de (Buba) | discussion-paper listing | **day** (on paper page) | DP number (`No 15/2025`) |

---

## ecb — European Central Bank

> **CORRECTION (verified live 2026-06-14, implemented in `sources/ecb_foedb.py`).**
> The pubbydate page is **not** a `lazyload-container` HTML listing — it is a
> client-side **`foedb` JSON database** (the `parse_year_includes` reuse idea
> below does not apply). Implemented source of truth:
> ```
> /foedb/dbs/foedb/publications.en/versions.json        -> [{version, hash}]
>   .../<version>/<hash>/metadata.json                  -> total_records, chunk_size, header[]
>   .../<version>/<hash>/data/0/chunk_<N>.json          -> flat arrays (records = len(header) values,
>                                                          index "0" = all, sorted pub_timestamp DESC)
> ```
> ~20k records (all ECB publications); filter by the `scpwps`/`scpops` URL in
> each record's `documentTypes`. Gives **exact day** (`pub_timestamp`, read in
> **Europe/Berlin** — ~25%+ of records store local-midnight and UTC would date
> them one day early), title, number and PDF URL — no HTML, no per-paper fetch.
> Verified: foedb-in-Berlin == the bank's own RSS feed (`/rss/wppub.html`) 14/14.

- **What it lists**: every ECB publication by exact date, including
  *Working Paper Series* (D1) and *Occasional Paper Series* (D2) entries.
- **Date**: exact day from `pub_timestamp` (Europe/Berlin).
- **PDF URL pattern**: `/pub/pdf/scpwps/ecb.wp{N}~{hash}.en.pdf` (D1, modern),
  `/pub/pdf/scpops/ecb.op{N}~{hash}.en.pdf` (D2, modern). Legacy/variant forms
  exist and must be handled: `ecbwp{N}.pdf`, `ecbocp{N}.pdf`, and a hash-before-
  number form `ecb~{hash}.wp{N}en.pdf` (see `ecb_wp_number`).
- **Matching key**: WP number `N`, extractable from both the bank PDF URL
  (`ecb.wp3117~...`) and the RePEc handle (`RePEc:ecb:ecbwps:20253117` →
  digits after the 4-digit year = `3117`).
- **History**: foedb covers ~1999→present; complete for the WP/OP series
  (migration matched 3628/3628 existing manifest D1/D2 rows by number).

## us — Federal Reserve Board

- **Listings** (one page per year, two series):
  - FEDS: `https://www.federalreserve.gov/econres/feds/{YYYY}.htm`
  - IFDP: `https://www.federalreserve.gov/econres/ifdp/{YYYY}.htm`
- **What it lists**: `FEDS{YYYY}-{NNN} {Month} {YYYY}` + link to a landing
  page per paper. **The year listing only gives the month** (verified:
  `FEDS2025-110 December 2025`).
- **Day precision**: the per-paper **landing page** carries the full date
  (HTML meta / visible header). So: 1 extra HTTP request per paper.
  **Decision (see [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) Q2): day precision
  everywhere, including backfill** — ~3 000 papers × 0.5 s throttle ≈ 30 min
  one-off. No month-precision Fed rows.
- **PDF URL pattern**: `https://www.federalreserve.gov/econres/feds/files/{YYYY}{NNN}pap.pdf`
  (modern; older years vary — derive from the landing page, don't guess).
- **Matching key**: series + number `2025-110` ↔ RePEc handle
  (`RePEc:fip:fedgfe:2025-110` style).
- **History**: FEDS pages go back to 1996 via `all-years.htm`.

## jp — Bank of Japan

- **Listing**: `https://www.boj.or.jp/en/research/wps_rev/wps_{YYYY}/index.htm`
- **What it lists**: an HTML table `| 25-E-13 | Nov. 13, 2025 | authors | title | [PDF] |`
  — **exact day directly on the listing**, no extra request needed (verified).
- **PDF URL**: in the table row (`/en/research/wps_rev/wps_{YYYY}/data/wp{code}.pdf` style).
- **Matching key**: paper code `25-E-13` (also present in the RePEc handle
  for `boj:bojwps`), fallback normalized title.
- **Note**: the existing `[jp.listing]` entry in `banks_sources.toml` already
  crawls this URL family for A3 minutes; the WP scraper can share the
  year-listing iteration logic.
- **History**: year pages exist back to the 1990s.

## gb — Bank of England

- **Listing**: BoE staff-working-paper **sitemap**
  `https://www.bankofengland.co.uk/sitemap/staff-working-paper` — already
  implemented in `cb_corpus/sources/boe_wp.py` (`sitemap_pages`,
  `paper_pdf`). v3 promotes this from "recovery script" to **primary source**.
- **Date**: the paper page carries the exact publication date; `boe_wp.py`
  already extracts `(date, page_url, derived_pdf_url)`.
- **PDF URL pattern**: `/-/media/boe/files/working-paper/{YYYY}/{slug}.pdf`
  (real URL read from the page, derived URL as fallback).
- **Matching key**: normalized title (BoE WP numbers are not in the URL;
  RePEc `boe:boeewp` handles carry the number — title match is the join).
- **History**: sitemap covers the full archive on the current site (~2014+);
  older papers are RePEc-only → date-recovery path.

## de — Deutsche Bundesbank

- **Listing**: `https://www.bundesbank.de/en/publications/research/discussion-papers`
  (paginated listing, filterable by year). Each item links a paper page.
- **Date**: exact day on the paper page (and usually on the listing teaser).
- **PDF URL pattern**: `/resource/blob/{id}/{hash}/mL/{slug}-data.pdf`
  — opaque IDs: always read from the page, never derive.
- **Matching key**: DP number `No {NN}/{YYYY}` printed on the page and
  embedded in the RePEc `zbw:bubdps` handle.
- **History**: the site lists DPs back to ~2002 (older series partially);
  pre-archive papers are RePEc/EconStor-only → date-recovery path.

---

## Implementation notes (common)

- Each native WP scraper is `_discover_native(DocType.D1/D2)` on the bank's
  adapter — same pattern as the existing A/B/E/F native types, so the
  pipeline, storage, dedup (sha256) and retry logic are unchanged.
- Every record gets `date_precision="day"` and `date_source="bank_site"`
  (Fed included — see Q2 decision).
- Dedup vs documents already downloaded through RePEc in v2: match on the
  keys above; if matched, **update the manifest row's date in place**
  (keep `doc_id`, `sha256`, `local_path`) — do not re-download.
  See [REPEC_AS_CHECK.md](REPEC_AS_CHECK.md) § "Migration of v2 rows".
