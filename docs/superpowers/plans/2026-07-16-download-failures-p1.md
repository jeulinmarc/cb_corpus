# Download Failures Phase 1 Implementation Plan (audit + source dedup + reconcile)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement phase 1 of `docs/superpowers/specs/2026-07-16-download-failures-design.md`: durable download-failure audit file, `save()` dedup by known `source_url`, and the one-shot `repec-reconcile` command (strict, dry-run-first).

**Architecture:** Two small storage-layer tasks (audit line on the error path; `skip:known-source` short-circuit), then the reconcile command as a sibling of `run_repec_check` in `cb_corpus/repec_check.py` reusing its coverage machinery, but tracking WHICH row matched (uniqueness) instead of set membership, and writing via the existing atomic `rewrite_manifest`.

**Tech Stack:** Python 3.13 + pytest (`python3.13 -m pytest tests/ -q`; 133 currently green). No bash-suite change (all below the job layer); run `tests/deploy/run_tests.sh` once at the end as a regression gate.

## Global Constraints

- English only; never Co-Authored-By/"Generated with Claude"; no real infra values.
- Dedup/matching on stable keys only (handle, number key, normalized URL, exact normalized title) — never dates, never fuzzy/substring title matching.
- Zero-error write discipline: `repec-reconcile` stamps ONLY unique matches onto rows whose `source_url` is empty; everything else is reported, not written; dry-run is the default; writes go through `rewrite_manifest` only.
- Counts/log lines of `save_many` unchanged (the audit line is additive).
- Existing behavior unchanged everywhere else: 133 tests must stay green.

---

### Task 1: Download-failure audit file

**Files:**
- Modify: `cb_corpus/storage.py` (`save_many`, plus a small `_record_download_error` helper)
- Test: `tests/test_download_audit.py` (new)

**Interfaces:**
- Produces: `data/download_errors.jsonl`, one JSON line per failed save:
  `{"ts": "<UTC ISO seconds>", "label": "<save_many label>", "bank_code", "doc_type", "title", "pdf_url", "alt_urls", "source_url", "error": "<Type: message, single line>"}`.

- [ ] **Step 1: Failing tests** — `tests/test_download_audit.py`:

```python
"""Download failures are persisted to data/download_errors.jsonl."""
import json

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.storage import Storage
from cb_corpus.taxonomy import DocType


class _BoomFetcher:
    def get_bytes(self, url):
        raise RuntimeError("HTTP 404: gone")


def _rec(**kw):
    base = dict(bank_code="gb", doc_type=DocType.D1, title="t",
                pdf_url="https://x.test/dead.pdf", source_url="https://ideas.test/p/1.html",
                provenance="repec_discovery")
    base.update(kw)
    return DocRecord(**base)


def _mk_storage(tmp_path):
    (tmp_path / "manifest").mkdir(parents=True)
    return Storage(Config(data_dir=tmp_path), _BoomFetcher())


def test_failed_download_writes_one_audit_line(tmp_path):
    st = _mk_storage(tmp_path)
    counts = st.save_many([_rec()], dry_run=False, label="repec:gb")
    assert counts == {"error": 1}
    lines = (tmp_path / "download_errors.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["label"] == "repec:gb"
    assert row["bank_code"] == "gb"
    assert row["pdf_url"] == "https://x.test/dead.pdf"
    assert row["source_url"] == "https://ideas.test/p/1.html"
    assert "RuntimeError" in row["error"] and "\n" not in row["error"]
    assert row["ts"].endswith("Z") or "+" in row["ts"]


def test_dry_run_writes_no_audit_line(tmp_path):
    st = _mk_storage(tmp_path)
    st.save_many([_rec()], dry_run=True, label="repec:gb")
    assert not (tmp_path / "download_errors.jsonl").exists()


def test_successful_saves_write_no_audit_line(tmp_path):
    class _OkFetcher:
        def get_bytes(self, url):
            return b"%PDF-fake", "application/pdf"
    (tmp_path / "manifest").mkdir(parents=True)
    st = Storage(Config(data_dir=tmp_path), _OkFetcher())
    counts = st.save_many([_rec()], dry_run=False, label="repec:gb")
    assert counts.get("error") is None
    assert not (tmp_path / "download_errors.jsonl").exists()
```

Adapt constructor plumbing (Storage/Config signatures, DocRecord required fields — read `cb_corpus/models.py` and `tests/test_framework.py` first); the three behaviors are the requirement. If `save()` needs a `date` on the record, add one fixed date to `_rec` (any constant — dates play no role here).

- [ ] **Step 2: RED** — `python3.13 -m pytest tests/test_download_audit.py -q`; expected: audit file missing → assertion failures.

- [ ] **Step 3: Implement** — in `cb_corpus/storage.py`:

```python
    def _record_download_error(self, rec: DocRecord, exc: Exception, label: str) -> None:
        """Append one line to data/download_errors.jsonl (durable audit of every
        failed download — stdout scrolls away, this file doesn't). Append-only,
        O_APPEND line writes; never read by the crawler itself."""
        import datetime as _dt
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "label": label,
            "bank_code": rec.bank_code,
            "doc_type": rec.doc_type.code,
            "title": rec.title,
            "pdf_url": rec.pdf_url,
            "alt_urls": rec.alt_urls or [],
            "source_url": rec.source_url,
            "error": f"{type(exc).__name__}: {exc}".replace("\n", " ")[:500],
        }
        path = self.cfg.data_dir / "download_errors.jsonl"
        with path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

and in `save_many`, replace the bare `except Exception:` with:

```python
            except Exception as exc:
                status = "error"
                if not dry_run:
                    try:
                        self._record_download_error(rec, exc, label)
                    except Exception:
                        pass  # auditing must never break the crawl
```

(Match the existing import style; `json` is already imported in storage.py.)

- [ ] **Step 4: GREEN** — `python3.13 -m pytest tests/ -q`; all pass (136 expected).

- [ ] **Step 5: Commit** — `git add cb_corpus/storage.py tests/test_download_audit.py && git commit -m "feat(storage): persist download failures to download_errors.jsonl"`

---

### Task 2: `save()` dedup by known source page

**Files:**
- Modify: `cb_corpus/storage.py` (`save`)
- Test: `tests/test_download_audit.py` (extend) or new `tests/test_source_dedup.py`

**Interfaces:**
- Produces: `save()` returns `"skip:known-source"` (counted as `skip` by save_many's `.split(":")[0]`) when `rec.source_url` is non-empty and already indexed in `_source_urls` — BEFORE the dry-run branch and BEFORE any fetch.

- [ ] **Step 1: Failing tests** (new file `tests/test_source_dedup.py`):

```python
"""save() short-circuits on a known source_url — no fetch, both modes."""
import json

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.storage import Storage
from cb_corpus.taxonomy import DocType


class _SpyFetcher:
    def __init__(self):
        self.calls = 0
    def get_bytes(self, url):
        self.calls += 1
        return b"%PDF-fake", "application/pdf"


def _manifest_row(source_url):
    return {"bank_code": "gb", "doc_type": "D1", "title": "old paper",
            "pdf_url": "https://boe.test/modern/slug.pdf", "source_url": source_url,
            "date": "2005-01-01", "language": "en", "provenance": "bank_site",
            "mime_type": "application/pdf", "sha256": "aa", "local_path": "x",
            "doc_id": "deadbeef00000001", "year": 2005}


def _mk(tmp_path, source_url="https://ideas.test/p/boe/1.html"):
    (tmp_path / "manifest").mkdir(parents=True)
    (tmp_path / "manifest" / "gb.jsonl").write_text(json.dumps(_manifest_row(source_url)) + "\n")
    f = _SpyFetcher()
    return Storage(Config(data_dir=tmp_path), f), f


def _rec(source_url):
    return DocRecord(bank_code="gb", doc_type=DocType.D1, title="old paper",
                     pdf_url="https://boe.test/DEAD/old-url.pdf",
                     source_url=source_url, provenance="repec_discovery")


def test_known_source_skips_without_fetch(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/1.html")) == "skip:known-source"
    assert f.calls == 0


def test_known_source_skips_in_dry_run_too(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/1.html"), dry_run=True) == "skip:known-source"
    assert f.calls == 0


def test_unknown_or_empty_source_does_not_skip(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/2.html")) != "skip:known-source"
    assert st.save(_rec("")) != "skip:known-source"
```

Same plumbing note as Task 1 (adapt row/record fields to the real models).

- [ ] **Step 2: RED** — expected: returns a non-skip status and `f.calls > 0`.

- [ ] **Step 3: Implement** — in `save()`, immediately after the `if rec.doc_id in self._ids:` block:

```python
        if rec.source_url and rec.source_url in self._source_urls:
            # The document behind this source page is already in the corpus
            # (possibly under a different pdf_url => different doc_id). Source
            # pages are stable identity keys, same family as pdf_url/sha256 —
            # this is what stops re-listed variants (e.g. a dead RePEc URL for
            # a natively-crawled paper) from triggering download attempts.
            return "skip:known-source"
```

- [ ] **Step 4: GREEN** — full pytest (139 expected).

- [ ] **Step 5: Commit** — `git add cb_corpus/storage.py tests/test_source_dedup.py && git commit -m "feat(storage): dedup by known source_url before download"`

---

### Task 3: `repec-reconcile` command (strict one-shot stamping)

**Files:**
- Modify: `cb_corpus/repec_check.py` (new `run_repec_reconcile` alongside `run_repec_check`)
- Modify: `cb_corpus/cli.py` (new subcommand)
- Test: `tests/test_repec_reconcile.py` (new)

**Interfaces:**
- Produces: `run_repec_reconcile(bank_codes=None, write=False, csv_path=None, config=None, fetcher=None) -> dict[str, dict]` returning per-bank `{"stamped": n, "ambiguous": n, "already": n, "unmatched": n}`; CSV rows `{bank, ideas_url, action, doc_id, title}` with action ∈ `stamp|ambiguous|already|unmatched`; CLI `repec-reconcile [--banks gb] [--write] [--csv path]`.

- [ ] **Step 1: Failing tests** — `tests/test_repec_reconcile.py`. Build tmp manifests + a fake fetcher serving one series listing page and the leftovers' IDEAS paper pages (reuse the fixture style of `tests/test_repec_incremental.py`). Required cases (adversarial fixtures on purpose — singletons, duplicated titles, imperfect data):

1. **Unique title match, empty source_url** → dry-run: reported as `stamp`, manifest file byte-identical after the run; `--write`: row's `source_url` becomes the IDEAS paper URL, rewrite goes through `rewrite_manifest` (assert the OTHER rows of the bank are preserved verbatim), and the return counts say `stamped == 1`.
2. **Two manifest rows sharing the same normalized title** → `ambiguous`, zero writes even with `--write`.
3. **Matched row already carrying a non-empty source_url** (e.g. its own bank landing page) → action `already`, zero writes.
4. **Listing entry matching nothing** → `unmatched`, zero writes; appears in the CSV so phase 2 can consume it.
5. **Entry whose IDEAS URL already appears as a row's source_url** → counted `already`, no page fetch for it (assert via the spy fetcher: its paper page is never requested).
6. **Idempotence**: run `--write` twice; second run stamps 0.
7. **Match via second-pass PDF-candidate URL** (IDEAS page lists the row's exact modern pdf_url): stamped — this is the strongest stable-key match for slug-URL banks like gb.

- [ ] **Step 2: RED** — import error (`run_repec_reconcile` missing).

- [ ] **Step 3: Implement `run_repec_reconcile`** — reuse `run_repec_check`'s building blocks with row-level maps instead of sets:

- Per bank, iterate `storage.iter_manifest(bank)` over D1/D2 rows building:
  `by_handle: dict[handle, list[row]]`, `by_key`, `by_title: dict[normalized_title, list[row]]`,
  `by_url: dict[normalized_url, list[row]]` (pdf_url + alt_urls), and
  `known_sources: set[source_url]`.
- For each `(pid, title)` from `enumerate_series(fetcher, handle)`:
  - `ideas_url = f"{IDEAS}/p/{arch}/{series}/{pid}.html"`; if `ideas_url in known_sources` (or the full handle maps to a row whose source_url is set) → `already`, continue (no page fetch).
  - Candidate rows := unique union of `by_handle[full_handle]`, `by_key[key]`, `by_title[normalize_title(title)]`.
  - If no candidate: second pass — fetch the IDEAS page (`_paper_meta`, `extract_pdf_candidates`), retry `by_url` on the candidates and `by_title` on the fetched canonical title. Still nothing → `unmatched`.
  - Exactly ONE candidate row AND its `source_url` empty → action `stamp` (record doc_id + ideas_url). One candidate with non-empty source_url → `already`. More than one → `ambiguous`.
- Writes: only with `write=True`; group stamps per bank, load the bank's full row list, apply `row["source_url"] = ideas_url` to the stamped doc_ids, call `storage.rewrite_manifest(rows)`. Never touch alt_urls; never write on ambiguous/unmatched.
- CSV (always written, dry-run included) + stdout per-bank summary line; return the counts dict.
- CLI wiring in `cli.py` next to the `repec-check` subcommand (mirror its arg style):

```python
    rr = sub.add_parser("repec-reconcile",
                        help="Stamp IDEAS source_urls onto uniquely-matched manifest "
                             "rows (dry-run by default; --write applies).")
    rr.add_argument("--banks", default="")
    rr.add_argument("--write", action="store_true")
    rr.add_argument("--csv", default="", help="CSV output path (default data/reports/repec_reconcile.csv)")
```

- [ ] **Step 4: GREEN** — full pytest (146 expected).

- [ ] **Step 5: Real-data dry-run sanity check (read-only, from the Mac)**

Run: `python3.13 -m cb_corpus repec-reconcile --banks gb` (NO --write)
Expected: summary in the vicinity of `stamped(candidate) ≈ 370+, ambiguous ≈ 0-5, unmatched ≈ 0-5`; CSV written under data/reports/. Record the actual numbers in the report — Marc reviews this CSV before any `--write` happens (on the NAS, post-merge). If the dry-run output deviates wildly (e.g. hundreds of ambiguous), STOP and report DONE_WITH_CONCERNS.

- [ ] **Step 6: Commit** — `git add cb_corpus/repec_check.py cb_corpus/cli.py tests/test_repec_reconcile.py && git commit -m "feat(repec): repec-reconcile — stamp IDEAS source_urls on uniquely-matched rows"`

---

## Post-implementation

Final whole-branch review (most capable model; extra attention: `rewrite_manifest` write-path safety, the strictness of the uniqueness rule, no date-based logic anywhere) + fix wave + post-fix pass; `tests/deploy/run_tests.sh` regression gate; docs to `documentation` branch; push; PR for Marc's review. Rollout per spec §5 (dry-run CSV reviewed by Marc before `--write` on the NAS).
