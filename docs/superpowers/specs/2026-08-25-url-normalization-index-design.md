# URL normalization at index time — design spec

**Status: DRAFT — awaiting Marc's validation.**

## Problem

`Storage.is_known_url()` matches URLs by exact string against the in-memory
index (`_urls`: every manifest `pdf_url` + `alt_urls`). Trivially-equivalent URL
forms therefore look unknown: the ECB legacy rows carried
`europa.eu//press/...` (double slash in the path) while the live site serves
`europa.eu/press/...` — 86 rows had to be healed by a one-off **data amendment**
in PR #11 (adding the normalized form to `alt_urls`). Any future variant of the
same defect class would need another data amendment. The known-URL check should
be robust at the **index** level instead.

## Decision (scope — deliberately minimal)

Normalize exactly ONE equivalence class, the only one observed in production:
**duplicate slashes in the URL path**. `https://a.eu//press//x.pdf` ≡
`https://a.eu/press/x.pdf`. The scheme separator (`https://`) is untouched.

Explicitly NOT normalized (each can change what a server returns; the honest
default is exact matching): trailing slashes, http↔https, `www.` prefixes,
query-string order, percent-encoding, case. If a new defect class is ever
proven in production, `normalize_url()` is the single place to extend — with a
test and a spec note, never silently.

## Design

1. **`normalize_url(url: str) -> str`** — module-level helper in
   `cb_corpus/storage.py`: split on `://` once (tolerate scheme-less strings),
   collapse `//+` → `/` in the remainder. Pure, no network, total (returns its
   input on empty/odd strings, never raises).
2. **Index writes**: everywhere `_urls` / `_source_urls` gain a member
   (`_load_existing`, `save`, `reindex`, and the alt_urls additions in
   `stamp_alt_urls`' rewrite path via re-load), store the **normalized** form.
3. **Index reads**: `is_known_url()` / `is_known_source_url()` normalize the
   query before lookup.
4. **Manifest data untouched**: rows keep the URL exactly as served
   (provenance honesty). Normalization exists only inside the in-memory index.
   The 86 PR #11 alt_urls amendments stay (historical record); they become
   redundant with the index normalization, which is fine.
5. `_hash_docid`, doc_id computation, quarantine keys: **unchanged** —
   doc_id is derived from the recorded pdf_url and must stay stable
   (doc_id ⊥ normalization; normalizing doc_ids would re-identify 39k docs).
   Quarantine keys stay raw URLs (they mirror download attempts, which use the
   raw URL).

## Consequence

A discovery source that re-lists a known document under a slash-variant URL is
skipped before download by `_skip_known_url` — no re-fetch, no duplicate row,
no data amendment. Existing double-slash rows in manifests keep matching both
forms automatically.

## Tests

- Unit: `normalize_url` (scheme preserved, path collapsed, multi-slash, no
  scheme, empty string, idempotent).
- `is_known_url` returns True for the slash-variant of an indexed URL and vice
  versa (both directions), including via `alt_urls` and after `reindex`.
- `is_known_source_url` same, one case.
- Pipeline-level: a discovery record whose pdf_url is a slash-variant of an
  indexed row is skipped by the `_skip_known_url` flow filter (regression test
  mirroring the ECB D3 legacy case).
- Negative: `http://a/x.pdf` vs `https://a/x.pdf` still NOT equal (scope guard).

Suite green: `python3.13 -m pytest tests/ -q`.
