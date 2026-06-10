# cb_corpus

Builder for a corpus of **official central-bank documents** — first-hand sources only,
for downstream RAG / analysis.

**Scope:** document families A–F (see `taxonomy.py`). **Targets:** the 63 BIS member central banks.
**Rule:** official primary sources only — every document comes from the issuing bank's own
domain, or `*.bis.org` (the official re-host for speeches). No academic-curated datasets,
no machine-translated or model-generated text.

> 📊 Data reference: run `python gen_corpus.py` → **`CORPUS.md`** (counts, per-bank / per-type inventory).
> 🤖 RAG handoff contract (schema, filters, citation): **`INGESTION_RAG.md`**.

## What's wired up

- **BIS speech index** (`sources/bis_speeches.py`) → speeches (C1) for all 63 banks from the
  BIS yearly sitemaps; keeps the **original PDF**. Institution attribution is **alias-aware**
  (`banks.py::BIS_ALIASES`, accent/apostrophe-insensitive) so renamed / historical / native
  BIS labels (e.g. "Bank of Latvia" → `lv`, pre-2010 "…US Federal Reserve System" → `us`)
  still map to the right bank instead of being silently dropped.
- **RePEc / IDEAS discovery** (`sources/repec.py`) → working papers (D1/D2). **Paginates** the
  full series back-catalogue (not just the ~200 newest) and reads the publication **date** from
  the IDEAS `citation_*` metadata. Prefers the bank's own PDF, falls back to any available PDF.
- **Per-bank adapters** (`adapters/`) → site-specific listings (A/B/E/F). `GenericAdapter`
  (C1 + D for every bank) is the default; bespoke `ECBAdapter` (A1 decisions / A2 statements /
  A3 accounts), `FedAdapter` (A2 statements / A3 minutes / F1), `RBAAdapter` (au, A1 rate
  decisions) and the TOML-declarative adapters (`banks_sources.toml`: ch/ca/fr/jp) add native
  listings. **Rate decisions, policy statements & minutes (A1/A2/A3) are wired for the majors.**
- **Bank-of-England official recovery** (`sources/boe_wp.py` + `pipeline.run_boe_wp_recovery` /
  `run_boe_recovery`) → BoE working papers and MPC minutes from the bank's **own sitemaps**
  (`/sitemap/staff-working-paper`, `/sitemap/minutes`), since IDEAS only carries the pre-2017
  dead URLs. Generic over any BoE sitemap section (reports/FSR are a one-line addition).
- **Wayback recovery** (`sources/wayback.py`) → official PDFs a bank has taken offline, fetched
  from archive.org's raw snapshot (`provenance="wayback"`, fully audited). Used for the Riksbank.
- **HTML→PDF** (`htmlpdf.py`) → HTML-only documents (e.g. ECB monetary-policy accounts) are
  rendered to PDF via headless Chrome; the **raw HTML is also kept** (`html_path`).
- **Storage / dedup** (`storage.py`) → manifest at `data/manifest.jsonl`; dedup on `doc_id`
  (stable hash of bank+type+**url** — date-independent, so correcting a date is a pure metadata
  update, no id churn) and content `sha256`. **No domain guard** — discovery owns URL quality.
- **Reindex from disk** (`pipeline.reindex_native_from_disk` / `reindex_bis_from_disk`, CLI
  `reindex-from-disk`) → rebuild manifest rows for PDFs that are **on disk but missing from the
  manifest** (e.g. the manifest was reset/lost while downloads kept accumulating). Replays
  discovery — native per-bank adapters (`--source native`, default; covers C1/A/B/E/F) or BIS
  sitemaps (`--source bis-sitemap`; C1) — to recover each document's exact **date** and **title**,
  then writes the row **without re-downloading the PDF**, matched to the on-disk file by the
  stable `doc_id`. Dry-run by default (`--write` to persist); idempotent.
- **Completeness matrix** (`completeness.py`) → expected-vs-downloaded per (bank × type × year):
  `ok / partial / missing / unknown`.
- **Fetcher** (`http.py`) → per-host rate limit (0.5s default) + retries with exponential
  backoff. Deliberately **not** robots-gated; set a real contact address in
  `config.py::Config.user_agent`.
- **Reproducible rebuild** (`pipeline.py`) → idempotent re-runs; `run(..., max_rounds=N)`
  re-crawls until a clean round (no new docs, no errors); discovery failures are logged to
  `data/discovery_errors.jsonl` (no silent drops).

## Install / test

```bash
pip install -r requirements.txt
python3.13 -m pytest tests/ -q          # 85 tests
```
> Use **`python3.13`** — that interpreter has the dependencies in this environment
> (`python3` resolves to 3.14 without them).

## Use

```bash
python -m cb_corpus list-banks

# Speeches (C1) — single pass over the BIS yearly sitemaps, all banks at once
python -m cb_corpus bis-sitemap --download

# Working papers (D1/D2) via RePEc (paginated, dated)
python -m cb_corpus repec --download                  # all wired series
python -m cb_corpus repec --banks ecb,us --download

# Per-bank native listings (A/B/E/F + inherited C1/D), with convergence retries
python -m cb_corpus discover --banks us,ecb --types A3,E4 --download --rounds 3

# Reindex: rebuild manifest rows for PDFs on disk but missing from the manifest
# (recovers exact dates/titles, no PDF re-download). Dry-run report unless --write.
python -m cb_corpus reindex-from-disk --source native --banks ecb            # report
python -m cb_corpus reindex-from-disk --source native --banks ecb --write    # persist

# Completeness report -> CSV
python -m cb_corpus report --years 2015-2025 --csv data/reports/matrix.csv
```

Full rebuild from Python:

```python
from cb_corpus.pipeline import run_bis_sitemap, run_repec, run
run_bis_sitemap(dry_run=False)      # C1 speeches (single pass, idempotent)
run_repec(dry_run=False)            # D1/D2 working papers (paginated)
run(dry_run=False, max_rounds=3)    # native A/B/E/F per bank (converges)
```

## Before a real run

1. **Set a contact in `config.py::Config.user_agent`.** It's how central-bank webmasters reach
   you — part of being a polite crawler. Throughput is bounded by politeness
   (`min_delay_seconds`, default ~2 s/domain); the rate limit is **per-host**, so banks on
   different domains crawl in parallel.
2. **Validate on a small window first.** Parsers were written to each site's documented markup,
   not all validated against live HTML. Dry-run one or two banks before a full crawl:
   ```bash
   PYTHONPATH=. python -m cb_corpus discover --banks us,ecb --since 2024-01-01   # no --download
   ```
   If a parser returns nothing / wrong rows: save the live HTML, adjust the pure `parse_*`
   function, and re-run its test in `tests/`.

**Completeness** (`report`) — status per (bank × type × year): `ok` (downloaded ≥ expected),
`partial` (0 < got < expected → re-crawl that bank/type/year), `missing` (expected but 0 → fix the
adapter/selector), `unknown` (no expected count). Add `expected_per_year` to an adapter (from the
bank's published meeting calendar) to turn `unknown` cells into real checks.

## Reproducibility & reliability

- **Idempotent** — re-running skips already-saved docs (`doc_id` + `sha256`), so a full
  rebuild converges instead of duplicating.
- **Convergence** — `--rounds N` (or `run(max_rounds=N)`) re-crawls until a round adds nothing
  new and reports no errors, filling transient-failure gaps.
- **Visible failures** — discovery fetch failures are appended to `data/discovery_errors.jsonl`
  instead of being swallowed, so an incomplete run is detectable.

## Adding a bank adapter

```python
from cb_corpus.adapters.base import BankAdapter, register
from cb_corpus.taxonomy import DocType

@register("gb")                       # overrides GenericAdapter for the Bank of England
class BoEAdapter(BankAdapter):
    native_types = (DocType.A3, DocType.E1, DocType.E2)
    expected_per_year = {DocType.A3: 8}          # MPC minutes/yr
    def _discover_native(self, doc_type, since):
        # fetch the bank's listing page(s) via self._fetch_text (records failures),
        # parse with a pure parse_* function, yield DocRecord(...)
        ...
```
C1 (speeches) and D1/D2 (papers) are inherited — only implement the native listings.
Use `self._fetch_text(url, context=...)` for listing fetches so failures are surfaced.
When a parser misbehaves, save the live HTML and adjust the relevant pure function
(`parse_listing`, `parse_minutes_links`, `parse_index`, `parse_series_page`,
`extract_official_pdf`), then re-run its test in `tests/`.

**Extending coverage** — the adapter build order is in `BANKS.md`; confirm any domain flagged
⚠️ there is reachable before a first crawl. To add working-paper series, extend
`sources/repec.py::SERIES` toward all 63 banks (verify each handle on IDEAS first).

## Layout

```
cb_corpus/
  taxonomy.py        DocType A1..G3, FULL_SCOPE (A–F)
  banks.py           the 63 BIS members + BIS_ALIASES (institution matching)
  models.py          DocRecord
  config.py          settings (UA, throttle, html_to_pdf)
  http.py            fetcher (per-host throttle + retry/backoff; throttle() for Chrome)
  storage.py         manifest, dedup (doc_id + sha256), HTML→PDF render, html_path
  htmlpdf.py         headless-Chrome HTML→PDF (shared profile)
  completeness.py    expected-vs-downloaded matrix
  pipeline.py        run() (convergence), run_bis_sitemap(), run_repec(),
                     reindex_*_from_disk() (manifest recovery, no re-download), run_*_recovery()
  cli.py             command line (list-banks, discover, bis-sitemap, repec, reindex-from-disk, report, ...)
  sources/
    bis_speeches.py  BIS speech index         -> C1 (alias-aware attribution)
    repec.py         RePEc discovery          -> D1/D2 (paginated, dated)
    wayback.py       Wayback CDX recovery      -> dead-but-official PDFs/HTML
    ecb_pub.py       ECB per-section includes  -> A1/B1/C2/E1-E4/G2 (primary -> CDX fallback)
    boe_wp.py        BoE official sitemaps     -> working papers + MPC minutes
  adapters/
    base.py          BankAdapter ABC + registry + GenericAdapter + _fetch_text
    fed.py / ecb.py / rba.py  worked native examples (us / ecb / au)
    declarative.py + generic_sitemap.py + listing_crawler.py  TOML-driven adapters
tests/               85 tests (taxonomy, registry, parsers, dedup, matrix, reliability)
```

## Notes

- Official primary sources only; non-English originals are kept as-is (the BIS speech corpus
  is English-language). No machine translation or model-OCR as a substitute for the original
  PDF (local OCR for indexing is fine if kept separate).
- Respect each site's terms — BIS content is noncommercial-use.
- The corpus is **~99 % complete** against each source's authoritative catalogue (BIS speeches,
  IDEAS working-paper series, bank publication calendars).
