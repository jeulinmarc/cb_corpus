# cb_corpus — Runbook

Operational guide for building the official central-bank corpus (scope A–F, 63 BIS banks).
Pairs with `central_bank_corpus_inventory.md` (the plan) and `README.md` (the package).

## 1. Install

```bash
unzip cb_corpus.zip && cd cb_corpus
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m pytest -q          # expect: 13 passed
```

## 2. Set the crawler identity (do this before any real run)

Edit `cb_corpus/config.py` → `Config.user_agent` and put a real contact address in it.
This is how the central-bank webmasters reach you; it is part of being a polite crawler.

## 3. Validate selectors on a tiny window FIRST

The parsers were written to each site's documented markup but **not** validated against
live HTML. Always start with one or two banks and a short date range, dry-run (no download):

```bash
PYTHONPATH=. python -m cb_corpus discover --banks us,ecb --since 2024-01-01
cat data/manifest.jsonl | head            # inspect discovered URLs
```

If a parser returns nothing or wrong rows:
1. Save the live page HTML.
2. Open the relevant pure parser (`parse_listing`, `parse_minutes_links`, `parse_index`,
   `parse_series_page`, `extract_official_pdf`).
3. Adjust the selector, re-run the matching test in `tests/test_framework.py`.

## 4. Download for real

```bash
PYTHONPATH=. python -m cb_corpus discover --banks us,ecb --since 2015-01-01 --download
```

- PDFs land in `data/raw/<bank>/<doctype>/<year>/<doc_id>.pdf`.
- The domain guard refuses any PDF not on the bank's own domain (or `*.bis.org`).
- Re-runs are idempotent: already-indexed IDs and duplicate content hashes are skipped.

## 5. Scale to all 63

```python
from cb_corpus.pipeline import run
run(dry_run=True)        # full discovery pass, no download
run(dry_run=False)       # download everything in scope A–F
```

Throughput is bounded by politeness (`min_delay_seconds`, default 2s/domain). 63 banks crawl
in parallel across domains because the rate limit is per-host.

## 6. Check completeness

```bash
PYTHONPATH=. python -m cb_corpus report --years 2015-2025 --csv data/reports/matrix.csv
```

Status per (bank × doc_type × year):

| status   | meaning                                   | action |
|----------|-------------------------------------------|--------|
| `ok`     | downloaded ≥ expected (or unknown & >0)   | none |
| `partial`| 0 < downloaded < expected                 | re-crawl that bank/type/year |
| `missing`| expected > 0 but downloaded == 0          | fix adapter/selector |
| `unknown`| no expected count and nothing downloaded  | add `expected_per_year` or trust runtime listing |

To make more cells *expected* (not `unknown`), add `expected_per_year` to that bank's adapter
from its published meeting calendar.

## 7. Extend coverage

- **Adapters:** see `BANKS.md` for the build order. C1/D are inherited; only implement the
  native A/B/E/F listings in `_discover_native`.
- **RePEc handles:** extend `sources/repec.py::SERIES` toward all 63 (verify each on IDEAS).
- **Domains flagged ⚠️ in `BANKS.md`** should be confirmed reachable before first crawl.

## 8. Push to git

```bash
git clone cb_corpus.bundle cb_corpus      # keeps the prepared commit
cd cb_corpus
git config user.name "You"; git config user.email "you@domain"
git commit --amend --reset-author --no-edit
git remote set-url origin git@github.com:USER/REPO.git
git branch -M main && git push -u origin main
```

## Reminders

- Official primary sources only — no academic datasets, no machine translation, no model-OCR
  as a substitute for the original PDF (local OCR for indexing is fine if kept separate).
- Respect each site's terms; BIS content is noncommercial-use.
- Non-English originals are kept as-is.
