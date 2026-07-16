# Bounded Nightly Catalogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-16-bounded-catalogs-design.md`: RePEc pre-fetch skip + stop-on-known pagination (`--incremental`), a `SYNC_WINDOW_DAYS` freshness window in the sync job (BIS `--years`), and the Mon–Sat bounded / Sunday full schedule.

**Architecture:** Python layer first (storage source-url index, repec generator refactor, pipeline/CLI wiring, pytest), then the job wrapper + schedule + docs (bash tests in Docker).

**Tech Stack:** Python 3.13 + pytest (`python3.13 -m pytest tests/ -q`); bash suite in Docker (`docker run --rm -v "$PWD/deploy:/app/deploy:ro" -v "$PWD/tests/deploy:/app/tests/deploy:ro" cb_corpus:test bash /app/tests/deploy/test_run_job.sh` → `RUN_JOB_OK`; full `tests/deploy/run_tests.sh` → `ALL_DEPLOY_TESTS_OK`).

## Global Constraints

- English only; never Co-Authored-By/"Generated with Claude"; placeholders only, no real infra values.
- Dedup identity stays on stable keys (handle/URL/sha256); dates/windows bound the WALK only.
- Default behavior unchanged: `repec` without `--incremental`, `run_repec(incremental=False)`, `discover_bank()` with default args, and `sync` when `SYNC_WINDOW_DAYS` is unset must behave byte-identically to today.
- Log/status strings are test-grepped — script and tests change together.
- `is_known_source_url` uses its OWN index (`_source_urls`), never merged into `_urls`.

---

### Task 1: RePEc incremental mode (storage + source + pipeline + CLI)

**Files:**
- Modify: `cb_corpus/storage.py`
- Modify: `cb_corpus/sources/repec.py`
- Modify: `cb_corpus/pipeline.py` (`run_repec`)
- Modify: `cb_corpus/cli.py` (repec parser + dispatch)
- Test: `tests/test_repec_incremental.py` (new)

**Interfaces:**
- Produces: `Storage.is_known_source_url(url) -> bool` (backed by `self._source_urls`, maintained in `_load_existing`, the dry-run/save index updates, and `rewrite_manifest`'s clear/rebuild); `RePEcDiscovery.discover_bank(code, skip_url=None, stop_on_known=False)`; `RePEcDiscovery._series_paper_pages(handle) -> Iterator[list[str]]` (replaces `_series_paper_urls`, no external callers); `pipeline.run_repec(..., incremental: bool = False)`; CLI `repec --incremental`.
- Task 2 relies on: `python -m cb_corpus repec --incremental --download` being valid.

- [ ] **Step 1: Write the failing tests** — `tests/test_repec_incremental.py`:

```python
"""RePEc incremental mode: pre-fetch skip + stop-on-known pagination."""
from typing import Iterator

import pytest

from cb_corpus.sources.repec import RePEcDiscovery, IDEAS


SERIES_HANDLE = "hhs:rbnkwp"
BASE = f"{IDEAS}/s/hhs/rbnkwp"


def _series_html(paper_ids):
    links = "".join(f'<li><a href="/p/hhs/rbnkwp/{p}.html">P{p}</a></li>'
                    for p in paper_ids)
    return f"<html><body><ul>{links}</ul></body></html>"


def _paper_html(pid):
    return (f'<html><head><title>Paper {pid}</title></head><body>'
            f'<h1>Paper {pid}</h1>'
            f'<a href="https://bank.test/wp/{pid}.pdf">Full text</a>'
            f'</body></html>')


class FakeFetcher:
    """Serves canned series/paper pages and records every URL fetched."""
    def __init__(self, pages):
        # pages: {url: html}; anything else raises (like a 404)
        self.pages = pages
        self.fetched = []

    def get_text(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError(f"404 {url}")
        return self.pages[url]


def _mk(pages):
    d = RePEcDiscovery.__new__(RePEcDiscovery)
    d.fetcher = FakeFetcher(pages)
    d.max_pages = 80
    return d


def _paper_url(pid):
    return f"{IDEAS}/p/hhs/rbnkwp/{pid}.html"


def test_skip_url_prevents_paper_page_fetch(monkeypatch):
    from cb_corpus.sources import repec as R
    monkeypatch.setitem(R.SERIES, "se", [(SERIES_HANDLE, __import__("cb_corpus.taxonomy", fromlist=["DocType"]).DocType.D1)])
    pages = {f"{BASE}.html": _series_html(["0001", "0002"]),
             _paper_url("0002"): _paper_html("0002")}
    d = _mk(pages)
    known = {_paper_url("0001")}
    recs = list(d.discover_bank("se", skip_url=lambda u: u in known))
    assert [r.source_url for r in recs] == [_paper_url("0002")]
    assert _paper_url("0001") not in d.fetcher.fetched   # never fetched


def test_stop_on_known_stops_after_all_known_page(monkeypatch):
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType
    monkeypatch.setitem(R.SERIES, "se", [(SERIES_HANDLE, DocType.D1)])
    pages = {f"{BASE}.html": _series_html(["0010", "0011"]),
             f"{BASE}2.html": _series_html(["0001", "0002"])}
    d = _mk(pages)
    known = {_paper_url(p) for p in ("0010", "0011", "0001", "0002")}
    list(d.discover_bank("se", skip_url=lambda u: u in known, stop_on_known=True))
    # page 1 is fully known -> pagination must stop; page 2 never requested
    assert f"{BASE}2.html" not in d.fetcher.fetched


def test_page_with_one_unknown_keeps_paginating(monkeypatch):
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType
    monkeypatch.setitem(R.SERIES, "se", [(SERIES_HANDLE, DocType.D1)])
    pages = {f"{BASE}.html": _series_html(["0010", "0011"]),
             f"{BASE}2.html": _series_html(["0001"]),
             _paper_url("0011"): _paper_html("0011"),
             _paper_url("0001"): _paper_html("0001")}
    d = _mk(pages)
    known = {_paper_url("0010")}
    recs = list(d.discover_bank("se", skip_url=lambda u: u in known, stop_on_known=True))
    assert f"{BASE}2.html" in d.fetcher.fetched          # kept going
    assert {r.source_url for r in recs} == {_paper_url("0011"), _paper_url("0001")}


def test_default_behavior_unchanged(monkeypatch):
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType
    monkeypatch.setitem(R.SERIES, "se", [(SERIES_HANDLE, DocType.D1)])
    pages = {f"{BASE}.html": _series_html(["0001"]),
             _paper_url("0001"): _paper_html("0001")}
    d = _mk(pages)
    recs = list(d.discover_bank("se"))                   # no new kwargs
    assert len(recs) == 1 and recs[0].pdf_url == "https://bank.test/wp/0001.pdf"


def test_storage_is_known_source_url(tmp_path):
    import json
    from cb_corpus.config import Config
    from cb_corpus.storage import Storage
    data = tmp_path / "data"
    (data / "manifest").mkdir(parents=True)
    row = {"bank_code": "se", "doc_type": "D1", "title": "t",
           "pdf_url": "https://bank.test/wp/1.pdf",
           "source_url": _paper_url("0001"),
           "date": "2026-01-01", "language": "en", "provenance": "repec_discovery",
           "mime_type": "application/pdf", "sha256": "x", "local_path": "y",
           "doc_id": "abc123", "year": 2026}
    (data / "manifest" / "se.jsonl").write_text(json.dumps(row) + "\n")
    st = Storage(Config(data_dir=data))
    assert st.is_known_source_url(_paper_url("0001"))
    assert not st.is_known_source_url(_paper_url("9999"))
    # deliberately separate semantics: source pages are NOT pdf-url-known
    assert not st.is_known_url(_paper_url("0001"))


def test_run_repec_incremental_wiring(monkeypatch, tmp_path):
    """incremental=True wires storage.is_known_source_url + stop_on_known."""
    import cb_corpus.pipeline as P
    captured = {}

    class _FakeRep:
        def __init__(self, fetcher): pass
        def discover_bank(self, code, skip_url=None, stop_on_known=False):
            captured["skip_url"] = skip_url
            captured["stop_on_known"] = stop_on_known
            return iter(())

    monkeypatch.setattr("cb_corpus.sources.repec.RePEcDiscovery", _FakeRep)
    from cb_corpus.config import Config
    (tmp_path / "manifest").mkdir(parents=True)
    P.run_repec(bank_codes=["se"], dry_run=True,
                config=Config(data_dir=tmp_path), incremental=True)
    assert captured["stop_on_known"] is True
    assert callable(captured["skip_url"])
```

Adapt plumbing to the real modules (e.g., how `run_repec` imports `RePEcDiscovery` — the monkeypatch target must match the import site; `Storage`/`Config` constructor details; `DocRecord` fields) after reading them — the asserted behaviors are the requirement. `Config(data_dir=...)` may need the manifest dir pre-created (see `test_framework.py` for the house pattern).

- [ ] **Step 2: Run to verify failure**

`python3.13 -m pytest tests/test_repec_incremental.py -q`
Expected: TypeError (`discover_bank() got an unexpected keyword argument 'skip_url'`) and AttributeError (`is_known_source_url`).

- [ ] **Step 3: Implement**

`cb_corpus/storage.py`:
- In `__init__` (next to `self._urls`): `self._source_urls: set[str] = set()`.
- In `_load_existing`, after the alt_urls block:

```python
            src = rec.get("source_url")
            if src:
                self._source_urls.add(src)
```

- Next to `is_known_url`:

```python
    def is_known_source_url(self, url: str) -> bool:
        """True if a record with this source_url is already in the manifest.

        Own index, deliberately separate from is_known_url(): source pages
        (e.g. IDEAS paper pages) identify a listing entry BEFORE its PDF is
        known — used by incremental catalog walks to skip the per-item fetch.
        """
        return url in self._source_urls
```

- In `rewrite_manifest`, add `self._source_urls.clear()` alongside the other clears (reload repopulates it).
- In `save()`: in the dry-run branch and at the point where a successful save registers `rec.pdf_url` into `self._urls`, also `self._source_urls.add(rec.source_url)` when `rec.source_url` is truthy (keeps a long-lived Storage consistent).

`cb_corpus/sources/repec.py` — replace `_series_paper_urls` with a per-page generator and rework `discover_bank`:

```python
    def _series_paper_pages(self, handle: str) -> Iterator[list[str]]:
        """Per-page paper-page URLs for a series, following IDEAS pagination.

        IDEAS caps a series listing at ~200 items per page; older papers live
        on numbered pages (`<handle>2.html`, ...). Pages are yielded newest
        first; the walk ends when a page yields nothing new (last page repeats
        / 404s) or the cap is hit.
        """
        base = f"{IDEAS}/s/{handle.replace(':', '/')}"
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = f"{base}.html" if page == 1 else f"{base}{page}.html"
            try:
                html = self.fetcher.get_text(url)
            except Exception:
                break
            new = [u for u in parse_series_page(html) if u not in seen]
            if not new:
                break
            seen.update(new)
            yield new
```

```python
    def discover_bank(self, bank_code: str,
                      skip_url: Optional[Callable[[str], bool]] = None,
                      stop_on_known: bool = False) -> Iterator[DocRecord]:
        """Yield D1/D2 records for a bank's wired series.

        `skip_url(paper_page_url)` short-circuits BEFORE the per-paper fetch
        (mirror of the BIS hook). With `stop_on_known`, a listing page whose
        papers are ALL skipped ends that series' pagination — IDEAS lists
        newest first, so an all-known page means the older tail is known too;
        a page with any unknown paper keeps the walk going (mid-list backfills
        still pull it deeper). Dates play no role here: identity stays on
        stable keys.
        """
        bank = get_bank(bank_code)
        for handle, doc_type in SERIES.get(bank_code, []):
            for page_urls in self._series_paper_pages(handle):
                unknown_on_page = 0
                for paper_url in page_urls:
                    if skip_url is not None and skip_url(paper_url):
                        continue
                    unknown_on_page += 1
                    try:
                        paper_html = self.fetcher.get_text(paper_url)
                    except Exception:
                        continue
                    cands = extract_pdf_candidates(paper_html, bank.homepage)
                    if not cands:
                        continue
                    title, pub_date = _paper_meta(paper_html)
                    yield DocRecord(
                        bank_code=bank_code,
                        doc_type=doc_type,
                        title=title,
                        pdf_url=cands[0],
                        alt_urls=cands[1:],
                        source_url=paper_url,
                        date=pub_date,
                        provenance="repec_discovery",
                        mime_type="application/pdf",
                        date_precision="month",
                        date_source="repec",
                    )
                if stop_on_known and unknown_on_page == 0:
                    break
```

(Add `Callable` to the module's typing imports.)

`cb_corpus/pipeline.py` — `run_repec` gains `incremental: bool = False`; the save call becomes:

```python
        results[code] = storage.save_many(
            rep.discover_bank(
                code,
                skip_url=storage.is_known_source_url if incremental else None,
                stop_on_known=incremental),
            dry_run=dry_run, label=f"repec:{code}")
```

`cb_corpus/cli.py` — repec parser:

```python
    rp.add_argument("--incremental", action="store_true",
                    help="skip papers already known by their IDEAS page and stop "
                         "each series at the first fully-known listing page "
                         "(nightly mode; the weekly full sweep omits this)")
```

and pass `incremental=args.incremental` in the repec dispatch.

- [ ] **Step 4: Run tests**

`python3.13 -m pytest tests/ -q` — expected: all pass (existing 125 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add cb_corpus/storage.py cb_corpus/sources/repec.py cb_corpus/pipeline.py cb_corpus/cli.py tests/test_repec_incremental.py
git commit -m "feat(repec): incremental mode — pre-fetch skip by source URL + stop-on-known pagination"
```

---

### Task 2: Sync freshness window, weekly full sweep, schedule and docs

**Files:**
- Modify: `deploy/run-job.sh`
- Modify: `deploy/crontab`
- Modify: `deploy/compose.refresh.example.yml`
- Modify: `deploy/README.md`
- Test: `tests/deploy/test_run_job.sh`

**Interfaces:**
- Consumes: `repec --incremental` (Task 1), existing `bis-sitemap --years A-B`.
- Produces: env `SYNC_WINDOW_DAYS` (compose `"90"`; unset/empty = full); job arg `sync full`; log lines `[sync] START (window <n>d)` / `[sync] START (full)`.

- [ ] **Step 1: Add the failing tests** — in `tests/deploy/test_run_job.sh`, insert after the existing T1 block:

```bash
# T1b — bounded sync (SYNC_WINDOW_DAYS set): windowed years + incremental repec.
newdir; export DISCOVER_BANKS="us" SYNC_WINDOW_DAYS=90
/app/deploy/run-job.sh sync
Y1=$(date -u +%Y)
Y0=$(date -u -d "-90 days" +%Y)
grep -q "PYARGS:-m cb_corpus bis-sitemap --years ${Y0}-${Y1} --download" "$PY_LOG" \
  || fail "bounded sync must pass --years ${Y0}-${Y1}"
grep -q "PYARGS:-m cb_corpus repec --incremental --download" "$PY_LOG" \
  || fail "bounded sync must pass --incremental"
grep -q "\[sync\] START (window 90d)" "$D/reports/nas_runs.log" || fail "window START marker missing"
unset DISCOVER_BANKS SYNC_WINDOW_DAYS

# T1c — 'sync full' ignores the window: unbounded catalogs.
newdir; export DISCOVER_BANKS="us" SYNC_WINDOW_DAYS=90
/app/deploy/run-job.sh sync full
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "full sync must omit --years"
if grep -q "\-\-incremental" "$PY_LOG"; then fail "full sync must omit --incremental"; fi
grep -q "\[sync\] START (full)" "$D/reports/nas_runs.log" || fail "full START marker missing"
unset DISCOVER_BANKS SYNC_WINDOW_DAYS

# T1d — window unset: sync behaves as full.
newdir; export DISCOVER_BANKS="us"
/app/deploy/run-job.sh sync
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "unset window must run full"
if grep -q "\-\-incremental" "$PY_LOG"; then fail "unset window must omit --incremental"; fi
grep -q "\[sync\] START (full)" "$D/reports/nas_runs.log" || fail "full START marker missing (unset window)"
unset DISCOVER_BANKS
```

Note: the pre-existing T1 runs `sync` WITHOUT `SYNC_WINDOW_DAYS`, so its
assertions (`bis-sitemap --download`, plain `repec --download`) keep passing
untouched — full is the default. Do not modify T1 beyond what already exists.

- [ ] **Step 2: Run to verify failure**

Docker run of the test file. Expected: T1b fails (`bounded sync must pass --years ...`). Must NOT print `RUN_JOB_OK`.

- [ ] **Step 3: Implement in `deploy/run-job.sh`**

1. After the `JOB="${1:-}"; shift || true` line:

```bash
SYNC_MODE="full"
if [ "$JOB" = "sync" ]; then
  if [ "${1:-}" = "full" ]; then
    shift
  elif [ -n "${SYNC_WINDOW_DAYS:-}" ]; then
    SYNC_MODE="window"
  fi
fi
```

2. Replace the single `log "START"` with:

```bash
if [ "$JOB" = "sync" ]; then
  if [ "$SYNC_MODE" = "window" ]; then
    log "START (window ${SYNC_WINDOW_DAYS}d)"
  else
    log "START (full)"
  fi
else
  log "START"
fi
```

3. In `run_sync`, replace the two catalog invocations with:

```bash
  if [ "$SYNC_MODE" = "window" ]; then
    # Bound the WALK only — identity/dedup stays on stable keys.
    local y0 y1
    y0=$(date -u -d "-${SYNC_WINDOW_DAYS} days" +%Y)
    y1=$(date -u +%Y)
    python -m cb_corpus bis-sitemap --years "${y0}-${y1}" --download || return $?
    python -m cb_corpus repec --incremental --download || return $?
  else
    python -m cb_corpus bis-sitemap --download || return $?
    python -m cb_corpus repec --download || return $?
  fi
```

- [ ] **Step 4: Run the bash tests** — expected `RUN_JOB_OK`.

- [ ] **Step 5: Schedule + compose + README**

`deploy/crontab` (full new content):

```
# Nightly bounded sync Mon-Sat (freshness window, ~30-60 min); unbounded full
# sweep on Sunday (~4-4.5 h) so late backfills and corrections are never lost.
# Schedule = parameter: edit here (image rebuild) or override the service
# command in Dockge to point to a crontab mounted as a volume.
0 1 * * 1-6 /app/deploy/run-job.sh sync
0 1 * * 0 /app/deploy/run-job.sh sync full
```

`deploy/compose.refresh.example.yml`: after `DISCOVER_WORKERS` add:

```yaml
      SYNC_WINDOW_DAYS: "90"   # catalog freshness window Mon-Sat; Sunday sweeps full
```

`deploy/README.md`: in the section-3 env paragraph, document `SYNC_WINDOW_DAYS` (bounds the BIS years walked and switches RePEc to incremental — skip known papers by their IDEAS page, stop each series at the first fully-known listing page; unset = every night full) and the Mon–Sat/Sunday schedule; note the count-semantics change (incremental nights log only NEW work per series; the Sunday sweep keeps full audit counts). In section 5, adjust the schedule mention.

- [ ] **Step 6: Full regression + commit**

`tests/deploy/run_tests.sh` → `ALL_DEPLOY_TESTS_OK`; `python3.13 -m pytest tests/ -q` → all pass.

```bash
git add deploy/run-job.sh deploy/crontab deploy/compose.refresh.example.yml deploy/README.md tests/deploy/test_run_job.sh
git commit -m "feat(deploy): SYNC_WINDOW_DAYS bounded nightly sync + Sunday full sweep"
```

---

## Post-implementation

Final whole-branch review (most capable model) + fix wave + post-fix pass; move spec/plan to the `documentation` branch and untrack; push; PR for Marc's review. Deployment after merge: image Update (PULL, not just recreate — bitten twice) + compose env addition.
