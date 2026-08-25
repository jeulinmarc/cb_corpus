# URL normalization at index time — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** make `Storage`'s known-URL indexes robust to duplicate-slash URL variants, index-side only.

**Architecture:** one pure helper `normalize_url()` in `cb_corpus/storage.py`; every write into `_urls`/`_source_urls` stores the normalized form; both `is_known_*` reads normalize the query. Manifest data, doc_ids, quarantine keys untouched.

**Tech stack:** stdlib only (`re`). Spec: `docs/superpowers/specs/2026-08-25-url-normalization-index-design.md`.

## Global Constraints

- Normalize ONLY duplicate slashes in the path; scheme separator `://` preserved; http↔https / trailing slash / www / query / case explicitly NOT equal.
- Manifest rows keep URLs exactly as served; doc_id computation and quarantine keys unchanged.
- English only; no new dependencies; `python3.13 -m pytest tests/ -q` green.

---

### Task 1: `normalize_url` helper

**Files:** Modify `cb_corpus/storage.py` (top, after imports); Test `tests/test_framework.py` (append).

- [ ] **Step 1: failing tests**

```python
def test_normalize_url_collapses_path_slashes_only():
    from cb_corpus.storage import normalize_url
    assert normalize_url("https://a.eu//press//x.pdf") == "https://a.eu/press/x.pdf"
    assert normalize_url("https://a.eu/press/x.pdf") == "https://a.eu/press/x.pdf"
    assert normalize_url("http://a.eu///b////c") == "http://a.eu/b/c"
    # scheme separator untouched, idempotent, total on odd inputs
    assert normalize_url("no-scheme//path") == "no-scheme/path"
    assert normalize_url("") == ""
    assert normalize_url(normalize_url("https://a.eu//x")) == "https://a.eu/x"

def test_normalize_url_does_not_equalize_out_of_scope_variants():
    from cb_corpus.storage import normalize_url
    assert normalize_url("http://a.eu/x.pdf") != normalize_url("https://a.eu/x.pdf")
    assert normalize_url("https://a.eu/x/") != normalize_url("https://a.eu/x")
    assert normalize_url("https://www.a.eu/x") != normalize_url("https://a.eu/x")
```

- [ ] **Step 2: run** `python3.13 -m pytest tests/test_framework.py -q -k normalize_url` → FAIL (ImportError)
- [ ] **Step 3: implement** — in `cb_corpus/storage.py`, ensure `import re` is present, then add near the other module-level helpers:

```python
_MULTI_SLASH = re.compile(r"/{2,}")


def normalize_url(url: str) -> str:
    """Canonical form for the known-URL indexes: duplicate slashes in the
    path collapsed to one (`a.eu//press` == `a.eu/press` — the one variant
    class observed in production, ECB legacy rows, PR #11). The scheme
    separator is preserved. Deliberately nothing else: trailing slashes,
    http vs https, `www.`, query order and case can all change what a
    server returns, so they stay significant (spec
    2026-08-25-url-normalization-index-design). Pure and total: returns
    its input shape for empty/scheme-less strings, never raises."""
    if not url:
        return url
    scheme, sep, rest = url.partition("://")
    if not sep:
        return _MULTI_SLASH.sub("/", url)
    return scheme + sep + _MULTI_SLASH.sub("/", rest)
```

- [ ] **Step 4: run** same command → PASS
- [ ] **Step 5: commit** `git add cb_corpus/storage.py tests/test_framework.py && git commit -m "storage: add normalize_url (path duplicate-slash collapse only)"`

### Task 2: normalized index writes + reads

**Files:** Modify `cb_corpus/storage.py` (`_load_existing`, `save`, `reindex`, `is_known_url`, `is_known_source_url`); Test `tests/test_framework.py`.

**Interfaces:** consumes Task 1's `normalize_url`. Produces: `is_known_url`/`is_known_source_url` slash-variant-insensitive (behavior relied on by Task 3's pipeline test).

- [ ] **Step 1: failing tests**

```python
def test_is_known_url_matches_slash_variants_both_directions(tmp_path):
    # a row indexed under a double-slash URL is known under the clean form...
    st = _fresh_storage(tmp_path)          # reuse the file's existing fixture helper
    rec = _make_rec(pdf_url="https://e.eu//press//a.pdf")   # existing helper
    st.save(rec, ...)                       # or reindex with a stub file, per fixture style
    assert st.is_known_url("https://e.eu/press/a.pdf")
    # ...and vice versa: clean-indexed row matches a double-slash query
    rec2 = _make_rec(pdf_url="https://e.eu/press/b.pdf")
    st.save(rec2, ...)
    assert st.is_known_url("https://e.eu//press/b.pdf")

def test_known_url_variants_cover_alt_urls_reload_and_source_urls(tmp_path):
    # alt_urls entry with doubled slash matches clean query after a fresh
    # Storage() over the same data dir (_load_existing path), and
    # is_known_source_url normalizes the same way.
```

(Implementer: use the file's existing Storage test fixtures — `tests/test_framework.py` already builds Storage instances over `tmp_path` with stub records; follow that pattern exactly, asserting through the public API only.)

- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** — apply `normalize_url` at every index write and both reads:

```python
# _load_existing
self._urls.add(normalize_url(url))
...
self._urls.add(normalize_url(alt))
...
self._source_urls.add(normalize_url(src))

# save() and reindex() (their post-write index updates)
self._urls.add(normalize_url(rec.pdf_url))
if rec.source_url:
    self._source_urls.add(normalize_url(rec.source_url))

# reads
def is_known_url(self, url: str) -> bool:
    return normalize_url(url) in self._urls

def is_known_source_url(self, url: str) -> bool:
    return normalize_url(url) in self._source_urls
```

(`rewrite_manifest` re-populates via `_load_existing` — covered automatically; verify and say so in the report.)

- [ ] **Step 4: run** `python3.13 -m pytest tests/test_framework.py -q` → PASS
- [ ] **Step 5: commit** `git commit -am "storage: known-URL indexes normalize duplicate-slash variants"`

### Task 3: pipeline regression test (ECB-legacy scenario) + full suite

**Files:** Test `tests/test_wp_v3.py` or `tests/test_framework.py` (wherever the existing `_skip_known_url` flow-filter tests live — follow suit).

- [ ] **Step 1: failing-or-passing check written as a test** — a native discovery record whose `pdf_url` is the double-slash variant of an already-indexed row is filtered out before download by the `_skip_known_url` hook (mirror the existing flow-filter test, changing only the URL forms). Expected: PASS already via Task 2 (the hook calls `is_known_url`) — the test pins the end-to-end behavior regardless.
- [ ] **Step 2: run full suite** `python3.13 -m pytest tests/ -q` → all green.
- [ ] **Step 3: commit** `git commit -am "tests: pin slash-variant skip at the discovery flow filter"`

## Self-review notes

- Spec coverage: helper (T1), writes+reads (T2), pipeline consequence (T3) — full.
- No placeholder steps; T2's second test intentionally described (fixture-dependent) with explicit instruction to follow existing fixtures and assert via public API only.
- Type consistency: `normalize_url(str) -> str` used identically at all six sites.
