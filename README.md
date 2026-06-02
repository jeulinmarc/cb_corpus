# cb_corpus

Official central-bank document corpus builder.
**Scope:** document types A–F (see taxonomy). **Targets:** the 63 BIS member central banks.
**Rule:** official primary sources only — every PDF must come from the issuing bank's own
domain (or `*.bis.org`, the official re-host for speeches). No academic-curated datasets,
no machine-translated or model-generated text.

## What's wired up

- **BIS speech index** (`sources/bis_speeches.py`) → discovers speeches (C1) for all ~130
  banks and takes the **original PDF**, not the BIS text extract.
- **RePEc URL discovery** (`sources/repec.py`) → finds working papers (D1/D2) but keeps a
  PDF **only if it resolves to the bank's own domain**; IDEAS-cached copies are dropped.
- **Per-bank adapter framework** (`adapters/`) → one adapter per bank for the
  site-specific listings (A/B/E/F). A `GenericAdapter` is registered for all 63 banks, so
  every bank already yields speeches + papers; `FedAdapter` and `ECBAdapter` are worked
  examples that add native listings.
- **Completeness matrix** (`completeness.py`) → expected-vs-downloaded per
  (bank × doc_type × year), with `ok / partial / missing / unknown` status.
- **Politeness** (`http.py`) → per-domain rate limiting, robots.txt, retries, crawler UA.
- **Domain guard** (`storage.py`) → refuses any PDF not on an official domain; dedupes on
  content hash; manifest at `data/manifest.jsonl`.

## Install

```bash
pip install -r requirements.txt
```

## Use

```bash
# the 63 target banks
python -m cb_corpus list-banks

# DRY RUN (index URLs only, no download) for two banks since 2015
python -m cb_corpus discover --banks us,ecb --since 2015-01-01

# actually download PDFs (respects robots.txt + rate limits)
python -m cb_corpus discover --banks us,ecb --since 2015-01-01 --download

# completeness report -> CSV
python -m cb_corpus report --years 2015-2025 --csv data/reports/matrix.csv
```

Full run (all 63, dry run first) from Python:

```python
from cb_corpus.pipeline import run
results = run(dry_run=True)          # omit dry_run to download
```

## Adding a bank adapter

```python
from cb_corpus.adapters.base import BankAdapter, register
from cb_corpus.taxonomy import DocType

@register("gb")                       # overrides GenericAdapter for the Bank of England
class BoEAdapter(BankAdapter):
    native_types = (DocType.A3, DocType.E1, DocType.E2)
    expected_per_year = {DocType.A3: 8}          # MPC minutes/yr
    def _discover_native(self, doc_type, since):
        # fetch the bank's listing page(s), parse, yield DocRecord(...)
        ...
```

C1 (speeches) and D1/D2 (papers) are inherited — only implement the native listings.

## Important notes

- **Selectors need a live check.** Parsers target the documented markup of each site but
  could not be validated against live HTML in the build environment (network restricted).
  Every parser is an isolated, unit-tested pure function (`parse_*`) so re-pointing is a
  one-line change. Run a small `--since` window first and confirm counts.
- **RePEc handles** in `sources/repec.py::SERIES` are a seed list for the majors — verify on
  IDEAS and extend toward all 63.
- **Long-tail domains** flagged `verify=True` in `banks.py` should be confirmed on first run.
- **Languages**: non-English originals are kept as-is (no machine translation).

## Layout

```
cb_corpus/
  taxonomy.py        DocType A1..G3, FULL_SCOPE (A–F)
  banks.py           the 63 BIS members
  models.py          DocRecord
  config.py          settings
  http.py            polite fetcher (robots, rate limit, retries)
  storage.py         manifest, dedup, official-domain guard
  completeness.py    expected-vs-downloaded matrix
  pipeline.py        orchestration
  cli.py             command line
  sources/
    bis_speeches.py  BIS speech index  -> C1
    repec.py         RePEc discovery   -> D1/D2 (official domain only)
  adapters/
    base.py          BankAdapter ABC + registry + GenericAdapter
    fed.py           example (FOMC minutes, SEP)
    ecb.py           example (accounts, Economic Bulletin)
tests/               13 tests over taxonomy, registry, parsers, guard, matrix
```

Run tests: `PYTHONPATH=. python -m pytest -q`
