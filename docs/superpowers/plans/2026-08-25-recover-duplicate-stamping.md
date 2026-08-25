# Recover duplicate-path self-healing — spec + plan (small PR)

**Problem (observed in production, 2026-08-25):** `recover-downloads` classifies a dead
URL as `duplicate` when its bytes (or doc_id) match a document already in the corpus —
but it leaves **no trace**: no `alt_urls` stamp on the canonical row, no quarantine
release. The dead URL therefore keeps failing nightly, re-enters every future recover
pass, and masquerades as a *new* failure (observed: the 31 gb leftovers of the 76-hunt).
The recovered path already self-heals (`record_success` + manifest row); the duplicate
path must too.

**Goal:** any `duplicate` verdict permanently resolves its dead URL: the canonical
manifest row gains the dead URL (and, in candidates mode, the live `final_url`) in
`alt_urls` — so `_skip_known_url` / `_is_converged` see it forever — and the dead URL's
quarantine entry is released. A re-run must report `converged`, not `duplicate`.

## Design

1. **`Storage`: sha256 → doc_id map.** Replace `self._hashes: set[str]` with
   `self._hash_docid: dict[str, str]` (sha256 → doc_id), built in `_load_existing`,
   maintained at the two write sites (`save`, `reindex`), cleared in `rewrite_manifest`'s
   index reset. Membership tests (`digest in self._hashes`) become `digest in
   self._hash_docid` — same semantics.

2. **Duplicate statuses carry the matched doc_id.** `save()` and `reindex()` return
   `f"skip:duplicate-content:{self._hash_docid[digest]}"` instead of the bare string.
   `skip:already-indexed` stays bare (the matched doc_id is `rec.doc_id` itself — the
   caller already has it). `save_many` tallies by `.split(":")[0]` — unaffected. Existing
   tests that compare the exact bare string are updated.

3. **New `Storage.stamp_alt_urls(stamps) -> int`.** `stamps: Mapping[str, Iterable[str]]`
   (doc_id → URLs to add). One pass over `iter_manifest_rows`, appends each URL that is
   neither the row's `pdf_url` nor already in its `alt_urls`; if anything changed, ONE
   `rewrite_manifest` call; returns the number of URLs actually stamped (0 = no rewrite).
   Unknown doc_ids are ignored (defensive; caller logs totals).

4. **`recover.py`: wire all three duplicate sites.** Collect during the pass, apply once
   at the end (never during dry-run):
   - CDX-walk path (`storage.save`): `skip:already-indexed` → stamp `dead_url` on
     `rec.doc_id`; `skip:duplicate-content:<id>` → stamp `dead_url` on `<id>`.
   - Candidates probe (`skip:already-indexed`): stamp `dead_url` on `rec.doc_id`
     (rec.pdf_url is the candidate's final_url for bank_site — already the row's own URL).
   - Candidates `skip:duplicate-content:<id>`: stamp `dead_url` **and** `final_url` on `<id>`.
   Every stamped dead URL also gets `storage.quarantine.record_success(dead_url)`.
   End of pass: `storage.stamp_alt_urls(...)`, print
   `[recover] stamped {n} alt_url(s) on {m} row(s)` to stderr.
   **CSV**: add a `canonical_doc_id` column (filled for `duplicate` rows, empty otherwise)
   — audit trail so an operator can spot a wrong pairing (the WP 1093 lesson: a wrong
   candidate file would stamp the wrong row; the pairing must be visible, `_CSV_FIELDS`
   updated accordingly).
   **Dry-run contract preserved**: no stamping, no quarantine release, no manifest write.

## Global constraints

- English only (code, comments, log messages). No new dependencies. `python3.13`.
- Dedup stays by stable keys; stamping only ADDS alt_urls, never rewrites pdf_url/doc_id.
- Honest reporting: `duplicate` action name unchanged; suffix parsing must tolerate the
  bare legacy string (a status without doc_id → skip stamping for that row, never crash).

## Tasks (TDD each: failing test → minimal code → green → commit)

1. **Storage map + status suffix** — `tests/test_framework.py` additions: (a) after
   `save`/`reindex` of doc A, saving different-doc-id B with same bytes returns
   `skip:duplicate-content:<A.doc_id>`; (b) map survives `_load_existing` (fresh Storage
   over same data dir). Update existing exact-string assertions. Modify
   `storage.py` (`_load_existing`, `save`, `reindex`, `rewrite_manifest`).
2. **`stamp_alt_urls`** — new tests: stamps missing URL, skips URL equal to pdf_url,
   skips already-present alt, returns 0 / no rewrite on no-op, ignores unknown doc_id.
3. **recover wiring** — extend `tests/test_recover_downloads.py` /
   `tests/test_recover_candidates.py`: the three duplicate paths each leave (i) dead URL
   in canonical row's alt_urls, (ii) quarantine released (`record_success` observable via
   quarantine file/`Quarantine` state), (iii) `canonical_doc_id` in the CSV row; dry-run
   mutates nothing; re-run reports `converged`. Legacy bare `skip:duplicate-content`
   from a stubbed save → no stamp, no crash.
4. **Docs** — one paragraph in `deploy/README.md` (quarantine section): duplicate
   verdicts now self-heal (stamp + release).

Suite must stay green: `python3.13 -m pytest tests/ -q` (371 + new).
