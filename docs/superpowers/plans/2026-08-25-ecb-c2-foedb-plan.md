# ECB C2 live source (foedb) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** replace the interim Wayback-based ECB C2 discovery with a live read of the ECB's foedb publications DB (type "ECB Interview"), plus a one-shot enrichment of the 604 existing C2 rows.

**Architecture:** new `discover_ecb_interviews()` in `cb_corpus/sources/ecb_foedb.py` mirroring `discover_ecb_wp` (same versions→metadata→chunks walk, early-stop on `since`); adapter's C2 branch rewired; interim code deleted; new `cb_corpus/c2_migrate.py` one-shot (wp_migrate style) run in-branch.

**Tech stack:** stdlib only; existing `Fetcher`/foedb helpers. Spec: `docs/superpowers/specs/2026-08-25-ecb-c2-foedb-design.md` (validated, EN-only confirmed by Marc).

## Global Constraints

- EN-only: records whose single `documentTypes` entry does not end in `.en.html` are excluded, **counted and logged** (one stderr line per run, never silent) — documented exclusion in the module docstring.
- Type id resolved dynamically from the `publications_types` DB ("ECB Interview"); missing/renamed → raise (fail loudly, no silent empty harvest).
- foedb `pub_timestamp` (Europe/Berlin) wins over URL-encoded dates.
- Enrichment NEVER changes `pdf_url`/`doc_id`; only `title`, `date`/`date_precision`/`date_source`, `alt_urls` additions.
- English only in code; no new deps; `python3.13 -m pytest tests/ -q` green.

---

### Task 1: foedb interview discovery

**Files:** Modify `cb_corpus/sources/ecb_foedb.py`; Test `tests/test_ecb_foedb.py` (same file that tests `discover_ecb_wp` — follow its fetcher-stub fixture style).

**Interfaces (produced):**
- `TYPES_DB = ECB + "/foedb/dbs/foedb/publications_types"`
- `resolve_interview_type_id(fetcher: Fetcher) -> int`
- `interview_from_record(rec: dict, type_id: int) -> Optional[tuple[str, Optional[date], str]] | "SKIP_NON_EN"` (see code — returns a sentinel for the counted exclusion)
- `discover_ecb_interviews(fetcher: Fetcher, since: Optional[date] = None) -> Iterator[DocRecord]`

- [ ] **Step 1: failing tests** (stub fetcher mapping URL→JSON text, per the file's existing pattern):

```python
def test_resolve_interview_type_id_reads_types_db(stub_fetcher):
    # types DB fixture: versions.json -> [{"version":"1","hash":"h"}],
    # metadata.json -> header ["id_publication_type","publication_name"], 1 chunk
    # chunk contains [2,"WP Series",27,"ECB Interview",226,"The ECB Blog"]
    assert resolve_interview_type_id(f) == 27

def test_resolve_interview_type_id_fails_loudly_when_renamed(stub_fetcher):
    # same fixture without any "ECB Interview" row
    with pytest.raises(ValueError):
        resolve_interview_type_id(f)

def test_discover_ecb_interviews_yields_c2_html_rows(stub_fetcher):
    # publications DB fixture: one type-27 record (documentTypes
    # ["/press/inter/date/2026/html/ecb.in260824~x.en.html"], Title "Interview with X",
    # pub_timestamp for 2026-08-24 Berlin) + one type-2 WP record + one type-27
    # record with only a ".it.html" documentType.
    docs = list(discover_ecb_interviews(f))
    assert len(docs) == 1
    d = docs[0]
    assert (d.doc_type, d.bank_code) == (DocType.C2, "ecb")
    assert d.pdf_url == "https://www.ecb.europa.eu/press/inter/date/2026/html/ecb.in260824~x.en.html"
    assert d.title == "Interview with X"
    assert str(d.date) == "2026-08-24" and d.date_precision == "day"
    assert d.mime_type == "text/html" and d.provenance == "bank_site"
    assert d.date_source == "bank_site" and d.source_url == FOEDB_DB

def test_discover_ecb_interviews_since_early_stop(stub_fetcher):
    # two chunks; first record newer than `since`, second older -> the walk
    # never fetches chunk_1 (assert on the stub's requested-URL log)
```

- [ ] **Step 2: run** `python3.13 -m pytest tests/test_ecb_foedb.py -q -k interview` → FAIL
- [ ] **Step 3: implement** in `ecb_foedb.py`:

```python
TYPES_DB = ECB + "/foedb/dbs/foedb/publications_types"

_INTERVIEW_TYPE_NAME = "ECB Interview"
_SKIP_NON_EN = object()   # sentinel: a real interview, excluded by language policy


def resolve_interview_type_id(fetcher: Fetcher) -> int:
    """The numeric `type` value meaning "ECB Interview", read from the ECB's own
    publications_types DB at run time. Resolving instead of hard-coding 27 is a
    honesty guard: if the ECB ever renumbers its types, a hard-coded id would
    silently harvest nothing -- this raises instead."""
    version, db_hash = parse_versions(json.loads(fetcher.get_text(f"{TYPES_DB}/versions.json")))
    base = f"{TYPES_DB}/{version}/{db_hash}"
    total, chunk_size, header = parse_metadata(json.loads(fetcher.get_text(f"{base}/metadata.json")))
    n_chunks = math.ceil(total / chunk_size) if chunk_size else 0
    for i in range(n_chunks):
        flat = json.loads(fetcher.get_text(f"{base}/data/0/chunk_{i}.json"))
        for rec in chunk_records(flat, header):
            if rec.get("publication_name") == _INTERVIEW_TYPE_NAME:
                return int(rec["id_publication_type"])
    raise ValueError(f"ECB publications_types DB has no {_INTERVIEW_TYPE_NAME!r} entry"
                     " -- the type map changed; refusing to harvest C2 silently")


def interview_from_record(rec: dict, type_id: int):
    """(title, date, absolute_url) if `rec` is an English interview; the
    `_SKIP_NON_EN` sentinel if it is an interview without an English version
    (9 known records, 2013-2020 -- EN-only policy, spec 2026-08-25, decision
    Marc); None if it is not an interview at all."""
    try:
        if int(rec.get("type")) != type_id:
            return None
    except (TypeError, ValueError):
        return None
    docs = [u for u in (rec.get("documentTypes") or []) if isinstance(u, str)]
    en = [u for u in docs if u.lower().endswith(".en.html")]
    if not en:
        return _SKIP_NON_EN if docs else None
    title = ((rec.get("publicationProperties") or {}).get("Title") or "").strip()
    return title, _record_date(rec.get("pub_timestamp")), _abs_url(en[0])


def discover_ecb_interviews(fetcher: Fetcher,
                            since: Optional[date] = None) -> Iterator[DocRecord]:
    """Yield ECB interviews/op-eds (C2) from the live foedb DB.

    Same walk as `discover_ecb_wp` (global pub_timestamp DESC -> early stop on
    `since`). Non-English-only interviews are excluded by policy, counted, and
    reported on stderr -- a documented exclusion, never a silent one."""
    type_id = resolve_interview_type_id(fetcher)
    version, db_hash = parse_versions(json.loads(fetcher.get_text(f"{FOEDB_DB}/versions.json")))
    base = f"{FOEDB_DB}/{version}/{db_hash}"
    total, chunk_size, header = parse_metadata(
        json.loads(fetcher.get_text(f"{base}/metadata.json")))
    n_chunks = math.ceil(total / chunk_size) if chunk_size else 0
    skipped_non_en = 0
    try:
        for i in range(n_chunks):
            flat = json.loads(fetcher.get_text(f"{base}/data/0/chunk_{i}.json"))
            for rec in chunk_records(flat, header):
                if since is not None:
                    d = _record_date(rec.get("pub_timestamp"))
                    if d is not None and d < since:
                        return
                got = interview_from_record(rec, type_id)
                if got is _SKIP_NON_EN:
                    skipped_non_en += 1
                    continue
                if got is None:
                    continue
                title, d, url = got
                yield DocRecord(
                    bank_code="ecb", doc_type=DocType.C2,
                    title=title or f"ECB interview {d or ''}".strip(),
                    pdf_url=url, source_url=FOEDB_DB, date=d,
                    provenance="bank_site", mime_type="text/html",
                    date_precision="day", date_source="bank_site",
                )
    finally:
        if skipped_non_en:
            print(f"[ecb-c2] {skipped_non_en} non-English-only interview(s) excluded"
                  " (EN-only policy)", file=sys.stderr, flush=True)
```

(add `import sys` if absent; `DocType` already imported.)

- [ ] **Step 4: run** targeted tests → PASS
- [ ] **Step 5: commit** `git add -A cb_corpus/sources/ecb_foedb.py tests/test_ecb_foedb.py && git commit -m "ecb: live C2 discovery from the foedb publications DB"`

### Task 2: adapter rewiring + interim removal

**Files:** Modify `cb_corpus/adapters/ecb.py`; Test: existing suite (interim tests deleted/adапted).

**Interfaces (consumed):** Task 1's `discover_ecb_interviews`.

- [ ] **Step 1:** in `_discover_native`, replace the C2 branch:

```python
        elif doc_type == DocType.C2:
            from ..sources.ecb_foedb import discover_ecb_interviews
            yield from discover_ecb_interviews(self.fetcher, since)
```

- [ ] **Step 2:** delete `_discover_inter` (lines ~433-530), the `INTER_SECTION`/`INTER_FIRST_YEAR` constants (~lines 62-65) and every INTERIM paragraph about C2 in the module docstring (lines ~7, 19-22); replace with two lines describing the foedb source and the EN-only documented exclusion. Grep the tests for `_discover_inter` / `INTER_` and delete/adjust those interim-specific tests (they tested the Wayback fallback mechanics — obsolete with the source itself; keep any test asserting C2 rows reach the pipeline, now backed by a foedb stub).
- [ ] **Step 3: run** `python3.13 -m pytest tests/ -q` → green (fix any straggler referencing removed symbols)
- [ ] **Step 4: commit** `git commit -am "ecb: C2 wired to live foedb source, interim Wayback path removed"`

### Task 3: one-shot C2 enrichment (`c2_migrate.py`) + CLI

**Files:** Create `cb_corpus/c2_migrate.py`; Modify `cb_corpus/cli.py`; Test `tests/test_c2_migrate.py` (new, mirroring `tests/test_wp_migrate.py` fixtures).

**Interfaces:** `run_c2_migrate(cfg: Config, fetcher: Fetcher, write: bool) -> dict` (summary counts); CLI `c2-migrate [--write]` (dry-run default, wp-migrate convention).

- [ ] **Step 1: failing tests**

```python
def test_c2_migrate_stamps_title_and_date(...):
    # manifest row: pdf_url matches a foedb record (same URL), title "ECB C2 2026-07-15",
    # date 2026-07-16 month-precision -> after run(write=True): title = foedb Title,
    # date = foedb Berlin date, date_precision "day", date_source "bank_site";
    # doc_id and pdf_url unchanged; CSV report row written.

def test_c2_migrate_keeps_nongeneric_titles(...):
    # row whose title does NOT start with "ECB C2" and differs from foedb ->
    # title kept, difference reported in CSV as action "title-diff-kept".

def test_c2_migrate_matches_double_slash_urls(...):
    # row with europa.eu//press/... matches the clean foedb URL (local normalization).

def test_c2_migrate_dry_run_touches_nothing(...):
```

- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** `cb_corpus/c2_migrate.py` (wp_migrate structure: gather foedb records via `discover_ecb_interviews(fetcher)` full walk; index by normalized URL — local helper collapsing path double-slashes; iterate `storage.iter_manifest("ecb")` C2 rows; propose changes; `--write` applies via `Storage.rewrite_manifest` on the full ecb row set):

Rules (from the spec):
```python
# title: overwrite ONLY generic interim titles (empty or startswith "ECB C2");
#        otherwise keep and record "title-diff-kept" in the CSV.
# date:  foedb wins whenever it differs (spec §5); stamp date_precision="day",
#        date_source="bank_site".
# alt_urls, two targeted amendments (recon 2026-08-25):
#   - the corrupted row (pdf_url containing ".en.html/nter/"): stamp the clean
#     foedb URL into alt_urls (pdf_url/doc_id untouched).
#   - the re-hashed pair (old row ecb.in191202~fe0bc873b8.en.html): stamp the
#     new-hash foedb URL (~869aa1e5ad) into alt_urls, closing the churn case
#     preemptively.
# CSV report -> data/reports/c2_migrate.csv (action per row: enriched /
# title-diff-kept / no-match / unchanged).
```

CLI wiring in `cli.py` (mirror wp-migrate at lines ~113 and ~279):
```python
    cm = sub.add_parser("c2-migrate",
                        help="One-shot enrichment of ecb C2 rows from the foedb DB "
                             "(titles/dates/alt_urls). Dry-run by default.")
    cm.add_argument("--write", action="store_true")
...
    if args.cmd == "c2-migrate":
        from .c2_migrate import run_c2_migrate
        ...
```

- [ ] **Step 4: run** `python3.13 -m pytest tests/test_c2_migrate.py -q` then full suite → green
- [ ] **Step 5: commit** `git commit -am "ecb: one-shot C2 enrichment from foedb (titles, dates, alt_urls)"`

### Task 4: run the enrichment in-branch (data amendment)

- [ ] **Step 1:** `python3.13 -m cb_corpus c2-migrate` (dry-run) — eyeball the CSV: expect ~601 matches, majority "enriched", the corrupted row + in191202 alt_urls amendments present, ~3 no-match (documented: re-hash/reclassified/corrupt — all handled or explained).
- [ ] **Step 2:** `python3.13 -m cb_corpus c2-migrate --write`
- [ ] **Step 3:** `python3.13 -m pytest tests/ -q` → green; `git diff --stat data/manifest/ecb.jsonl` sanity (only C2 rows touched, row count unchanged).
- [ ] **Step 4: commit** `git add data/manifest/ecb.jsonl data/reports/c2_migrate.csv && git commit -m "data: ecb C2 enrichment from foedb (real titles, Berlin dates, churn alt_urls)"`

## Self-review notes

- Spec §§1-7 ↔ T1 (walk+guard+EN-only), T2 (rewiring+removal), T3+T4 (enrichment+corrupted row+re-hash pair). Risks section needs no code — verified none slipped in as silent behavior.
- Interfaces consistent: `discover_ecb_interviews` signature used identically in T2 (adapter) and T3 (migrate).
- The `finally`-based skip counter logs on early-stop too (incremental nights: counter usually 0, line printed only when non-zero).
- No dependency on chantier A's `normalize_url` (parallel branch): c2_migrate ships its own local URL normalization for matching only.
