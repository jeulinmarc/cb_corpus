# PR #13 follow-up minors — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** four small hardenings on recover's self-heal path (crash-window, converged labeling, honest logging, deterministic order).

**Architecture:** all changes in `cb_corpus/recover.py` + `cb_corpus/storage.py` (`stamp_alt_urls` return type). Spec: `docs/superpowers/specs/2026-08-25-pr13-minors-design.md`.

## Global Constraints

- Dry-run contract untouched (stamps only built when `download=True`).
- `stamp_alt_urls` still only ADDS alt_urls, one `rewrite_manifest` max per call.
- English only; no new deps; `python3.13 -m pytest tests/ -q` green (382 pre-existing).

---

### Task 1: deterministic stamp order (spec §4) + tuple return (spec §3)

Grouped: both touch the same collection/apply pipeline; the tuple return changes every `stamps`-related assertion once.

**Files:** Modify `cb_corpus/storage.py` (`stamp_alt_urls`), `cb_corpus/recover.py` (stamp collection sites + `_apply_stamps`); Test `tests/test_recover_candidates.py`, `tests/test_framework.py`.

**Interfaces:** `Storage.stamp_alt_urls(stamps) -> tuple[int, int]` (urls_stamped, rows_modified); `stamps` values become **ordered lists**.

- [ ] **Step 1: failing tests**

```python
def test_stamp_alt_urls_returns_urls_and_rows_modified(...):
    # 2 urls onto one row -> (2, 1); repeat same call -> (0, 0) and no rewrite
    assert storage.stamp_alt_urls({doc_id: [u1, u2]}) == (2, 1)
    assert storage.stamp_alt_urls({doc_id: [u1, u2]}) == (0, 0)

def test_candidates_duplicate_stamp_order_dead_then_final(...):
    # duplicate-content candidate -> canonical row's alt_urls end with
    # exactly [dead_url, final_url] in that order
    assert rows[0]["alt_urls"][-2:] == [dead_url, final_url]
```

- [ ] **Step 2: run** targeted tests → FAIL
- [ ] **Step 3: implement**

`storage.py` — `stamp_alt_urls`: track rows and return the pair (docstring updated accordingly):

```python
        rows_modified = 0
        ...
            if alt_urls != (row.get("alt_urls") or []):
                row["alt_urls"] = alt_urls
                rows_modified += 1
                touched_banks.add(bank)
        if stamped == 0:
            return (0, 0)
        rows_to_write = [r for bank in touched_banks for r in by_bank[bank]]
        self.rewrite_manifest(rows_to_write)
        return (stamped, rows_modified)
```

(both early-return sites become `return (0, 0)`; annotation `-> tuple[int, int]`.)

`recover.py` — every stamp collection site switches from set to ordered list, dead URL first (grep `stamps.setdefault`); the duplicate-content candidates site collects `[dead_url, final_url]`:

```python
def _add_stamp(stamps: dict[str, list[str]], doc_id: str, *urls: str) -> None:
    """Ordered, deduped stamp collection: dead URL first (provenance
    history), then any live alternative. Order is part of the contract --
    a set here made alt_urls append order vary across runs (hash
    randomization), churning autocommitted manifest diffs for nothing."""
    bucket = stamps.setdefault(doc_id, [])
    for url in urls:
        if url and url not in bucket:
            bucket.append(url)
```

`_apply_stamps` prints the honest pair:

```python
    urls_stamped, rows_modified = storage.stamp_alt_urls(stamps)
    print(f"[recover] stamped {urls_stamped} alt_url(s) on {rows_modified} row(s)",
          file=sys.stderr, flush=True)
```

- [ ] **Step 4: run** `python3.13 -m pytest tests/test_recover_candidates.py tests/test_recover_downloads.py tests/test_framework.py -q` → PASS (update any existing assertion on the old int return — grep `stamp_alt_urls` in tests/)
- [ ] **Step 5: commit** `git commit -am "recover: deterministic stamp order + honest (urls, rows) stamp accounting"`

### Task 2: stamps survive a mid-pass crash (spec §1)

**Files:** Modify `cb_corpus/recover.py` (both passes); Test `tests/test_recover_candidates.py`.

- [ ] **Step 1: failing test**

```python
def test_stamps_applied_even_if_pass_dies_mid_loop(monkeypatch, ...):
    # two duplicate candidates; storage.reindex raises RuntimeError on the
    # SECOND candidate's probe -> the pass propagates/records the error, but
    # the FIRST candidate's stamp must already be persisted in the manifest.
```

(Implementer: monkeypatch the second call using a side-effect counter, mirror
the file's existing monkeypatch style; assert via re-read manifest rows.)

- [ ] **Step 2: run** → FAIL (stamp lost when loop aborts)
- [ ] **Step 3: implement** — in `_run_candidates_pass` and in the CDX pass of `run_recover_downloads`, wrap the per-entry loop:

```python
    try:
        for cand in cands:          # (resp. `for entry in inventory:`)
            ...existing body...
    finally:
        # Quarantine releases are durable per-entry inside the loop, while
        # stamps applied only after it -- a mid-pass crash (SIGKILL aside)
        # would leave released-but-unstamped dead URLs that the nightly
        # sync resumes hammering. finally shrinks that window to the
        # entry being processed (PR #13 final review, Minor 1).
        _apply_stamps(storage, stamps)
```

(The existing end-of-pass `_apply_stamps` calls at recover.py:521/:700 move into these `finally` blocks — no double apply; CSV writing stays outside.)

- [ ] **Step 4: run** `python3.13 -m pytest tests/test_recover_candidates.py tests/test_recover_downloads.py -q` → PASS
- [ ] **Step 5: commit** `git commit -am "recover: apply collected stamps even when a pass dies mid-loop"`

### Task 3: candidates-mode `converged` short-circuit (spec §2)

**Files:** Modify `cb_corpus/recover.py` (`_run_candidates_pass`); Test `tests/test_recover_candidates.py`.

- [ ] **Step 1: failing test**

```python
def test_candidates_rerun_over_healed_corpus_reports_converged(...):
    # run a bank_site candidate once (duplicate + stamp), then run the SAME
    # candidates file again: summary counts converged=1 duplicate=0, the CSV
    # row's action is "converged", and the quarantine file gained no new line.
```

- [ ] **Step 2: run** → FAIL (second run says duplicate)
- [ ] **Step 3: implement** — in `_run_candidates_pass`, right after the audit
entry is matched (`entry = by_url.get(dead_url)` and the bank is resolved,
before the file/doc-type validations):

```python
        if _is_converged(storage, entry):
            # The index already knows every URL of this entry (typically via
            # a stamp from a previous pass): "the corpus already has it" is
            # a different truth than "nothing left to recover" (duplicate),
            # and the CDX pass already reports it as such -- keep the two
            # modes' vocabulary consistent (PR #13 final review, prior
            # Minor 1). No stamping, no quarantine call.
            _bump(bank, "converged")
            csv_rows.append({"bank": bank, "pdf_url": dead_url,
                             "action": "converged", "snapshot_ts": "",
                             "title": cand.get("title") or entry.get("title") or ""})
            continue
```

- [ ] **Step 4: run** `python3.13 -m pytest tests/test_recover_candidates.py -q` → PASS, then full suite `python3.13 -m pytest tests/ -q` → green
- [ ] **Step 5: commit** `git commit -am "recover: candidates re-run reports converged, matching the CDX pass"`

## Self-review notes

- Spec §§1-4 ↔ Tasks 2, 3, 1, 1 — full coverage, no placeholders (two test bodies delegated to existing fixture style by explicit instruction, assertions specified).
- Interface consistency: `(urls_stamped, rows_modified)` tuple used in T1 code and T1 tests; `_add_stamp` ordered-list contract consumed by T2/T3 unchanged.
- T2's `finally` keeps the dry-run contract: `stamps` stays empty unless `download=True`, and `_apply_stamps` is a no-op on empty.
