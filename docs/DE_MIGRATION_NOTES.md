# de (Bundesbank Discussion Papers) native migration — issues & analysis

Companion to [FED_MIGRATION_NOTES.md](FED_MIGRATION_NOTES.md). Bundesbank was the
"hardest, least-known site" the design doc warned about, and it lived up to it:
the listing is JS-only and the manifest rows point at EconStor, not the bank.
Written after the migration ran (2026-06-14).

## Outcome

- **617 / 689** `de` D1 rows migrated to bank-site **day** precision (554 by DP
  number, 63 by exact title); 72 unmatched legacy → date recovery.
- The bank exposes the **full ~1077-DP archive** — ~462 DPs the corpus never had
  (RePEc/EconStor only indexed a subset) are now discoverable; `discover
  --download` would add them.
- doc_id/sha256/local_path untouched; the EconStor `pdf_url` is kept and the
  Bundesbank blob is registered in `alt_urls`.

## Why it was hard

1. **The manifest rows are EconStor copies.** RePEc's `zbw:bubdps` archive points
   at `econstor.eu/bitstream/…`, not bundesbank.de — so there is no Bundesbank URL
   or DP number in the manifest to join on directly. The join leans on the DP
   number parsed from the **RePEc handle** (`bubdps:{NN}{YYYY}`) and, for the
   recent papers whose handle is a bare global EconStor id, on the **exact title**.
2. **The listing is JS-only.** `…/research/discussion-papers` server-renders just
   the 8 newest papers; `?page=N`, `?year=`, and date-range params are all
   ignored. There is no static "all DPs" page.
3. **Opaque blob PDFs.** Each paper's PDF is `/resource/blob/{id}/{hash}/{hash}/
   {YYYY-MM-DD}-dkp-{NN}-data.pdf` — must be read from the page, never derived.
   (Helpfully, the filename embeds the ISO date and DP number.)

## How the JS pagination was cracked (chrome-devtools)

`curl` of the listing only ever returned the same 8 papers, with no AJAX endpoint
visible in the HTML. I drove **chrome-devtools** (navigate → inspect the filter
form + pagination controls) and found the real result endpoint:

```
/action/en/732408/bbksearch?pageNumString=N        (0-based; ~125 pages)
```

Verified it serves the whole archive over plain HTTP (no Chrome at runtime). The
twist: result items come in **two shapes** — recent papers link a
`/discussion-papers/{slug}-{id}` page (the blob/day live there → one fetch),
older papers link the **blob PDF directly** (date + number in the filename → no
fetch). `parse_listing_page` handles both; `discover_buba_wp` walks every page,
reads the day off the blob (or the paper page), and dedups by DP number.

> **My view:** without devtools this was a dead end — the endpoint id (`732408`)
> and the `pageNumString` scheme are nowhere in the static HTML. The scraper
> hard-codes `732408`; if Bundesbank rebuilds that content element it will break
> and **fail loudly** (zero results) rather than silently truncate, which is the
> right failure mode. The two-shape result list is the kind of thing only a live
> run reveals — my first cut found 8 papers, the second 55, before I realised
> older items were direct blobs.

## Join-key design

- **DP number `(num, year)`** from the blob filename (`…-dkp-14-data.pdf` →
  `(14, 2026)`) and from the handle (`bubdps:142026` → `(14, 2026)`; bare global
  ids like `337465` → `None`, fall through to title).
- **Exact-normalized title** tier (shared with BoE) catches the recent papers
  whose handle is a global id.

## Caveats / open

- 72 unmatched legacy rows (withdrawn, title drift, or pre-archive) → `wp-dates`
  recovery.
- The ~462 archive DPs the corpus lacks are discoverable but not yet downloaded
  (a deliberate, separate `discover --download` step).
- Ongoing discovery is cheap (newest pages first, `--since` stops early); a full
  backfill walk is ~125 listing pages + the recent-paper landings (~3 min).

## Bottom line

de went from **year precision** (every row dated `YYYY-01-01`) to **day** for
617/689 rows, and the corpus gained visibility into ~462 missing papers. The
hard part was purely discovery (JS listing); once the bbksearch endpoint was
found, the same migrate→verify→flip machinery as the other banks applied.
