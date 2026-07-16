# Recovery phase 2: wayback recovery + human-approved reconciliation — design

Date: 2026-07-16
Status: for Marc's review (phase 2 of the 2026-07-16 download-failures spec)

## Inputs (real data, verified today)

- **fr audit entries: dry-run outcome on the 15 reconstructed entries — 6
  recoverable / 7 unrecoverable / 2 converged.** Probed empirically: the
  IDEAS page for fr WP981 lists ONLY the 403-blocked banque-france.fr URL (no
  EconStor/SSRN mirror → the mirror route is dead for fr), but the Wayback
  Machine holds the PDF (200 snapshot, 2025-02-07) — one of the 6 recoverable.
  The 7 unrecoverable ones have no snapshot under any known URL and are left
  honestly listed rather than guessed at; the 2 converged ones were already
  picked up by the nightly retry since the failure was logged. Note: this is
  the dry-run classification of the 15 RECONSTRUCTED entries used to probe
  the tooling, not the full live inventory — the NAS's live
  `download_errors.jsonl` may recover more once `--download` is run against
  it, since some of those entries carry `alt_urls` (original mirrors) this
  reconstructed sample didn't exercise. The corpus already has the right
  convention (`sources/wayback.py`): download the `<ts>id_/` raw snapshot
  (original bytes), keep `pdf_url` = the official bank URL (citation + stable
  doc_id), `provenance="wayback"` for auditability.
- **gb 32: NOT missing — RePEc↔published title drift** (verified row by row in
  the PR #7 review: retitles, RePEc typos, data-entry artifacts). No stable key
  can match them; the exact-match rule correctly refuses. The corpus HAS these
  papers; only the RePEc listing entry remains unreconciled, causing ~32
  dead-download attempts every Sunday.
- **se 22 / it 6 / jp 3 + anything new**: will land in `download_errors.jsonl`
  (live since PR #6) — first real harvest expected at the next full sweep
  (Sunday). The tooling below consumes that file, so classification (class C)
  and recovery (class B) need no per-bank code.

## Design

### B1. `recover-downloads` — inventory-driven Wayback recovery

New CLI command `recover-downloads [--banks a,b] [--download] [--csv path]`:

1. **Inventory**: read `data/download_errors.jsonl`, dedup by `pdf_url`
   (keep the most recent entry), optional `--banks` filter.
2. **Skip the converged**: drop entries whose `pdf_url`/`alt_urls` is now
   known (`is_known_url`) or whose `source_url` is known
   (`is_known_source_url`) — the nightly retry already got them.
3. **Refresh metadata from the source page** when `source_url` is an IDEAS
   page: fetch it once, take title/date/candidates via the existing
   `_paper_meta`/`extract_pdf_candidates` (month precision, `date_source=
   "repec"` — same honesty as repec discovery). Otherwise reuse the audit
   entry's fields.
4. **Wayback lookup**: latest HTTP-200 `application/pdf` snapshot for the
   EXACT `pdf_url` (new small helper `latest_capture` beside the existing
   `first_capture` in `sources/wayback.py`); on miss, try each alt URL.
5. **Outcome per entry** (CSV `{bank, pdf_url, action, snapshot_ts, title}`):
   - `recoverable` → with `--download`: build the DocRecord with
     `pdf_url` = official URL, `alt_urls` = [raw snapshot] + prior alts,
     `provenance="wayback"`, and hand it to `storage.save()` — the normal
     fallback chain downloads the snapshot bytes (official URL is tried
     first and may even succeed if the bank unblocked it — fine either way);
     sha256/doc_id dedup as everywhere.
   - `recovered` → `storage.save()` returned `saved`: the document is now on
     disk and in the manifest.
   - `duplicate` → `storage.save()` returned a `skip:*` status (the
     snapshot's bytes hash-match a document already in the corpus, or the
     doc_id was already indexed): nothing was left to recover, so the CSV
     honestly says `duplicate` rather than `recoverable` — the latter would
     be a lie (nothing recoverable remains) and would re-download the same
     PDF on every subsequent run for no gain.
   - `unrecoverable` (no snapshot anywhere) → report line; this is the
     honest residue, never guessed at.
   - `converged` → the corpus already has the document (step 2); never
     touches the network.
6. Dry-run default (`--download` mirrors the discover convention): without
   it, only the CSV is written — nothing downloaded, nothing saved.
7. Politeness: web.archive.org goes through the standard per-host throttle;
   one CDX query + at most one download per entry.
8. **Not scheduled**: a manual campaign command, run after Sundays while the
   inventory is fresh (a cron can come later if the volume justifies it — YAGNI).

### B2. Human-approved reconciliation for title drift (`--propose` / `--apply-csv`)

The gb 32 (and future drift cases) cannot be matched by stable keys, and we
never fuzzy-match automatically. The human becomes the key, explicitly:

- `repec-reconcile --propose [--banks gb] [--csv path]`: for each `unmatched`
  listing entry, list up to 3 CANDIDATE native rows (same bank, D1/D2,
  empty `source_url`) ranked by a similarity score (difflib ratio on
  normalized titles — used for ORDERING THE PROPOSAL ONLY, never for
  writing). CSV columns: `{bank, ideas_url, repec_title, candidate_doc_id,
  candidate_title, score, approve}` with `approve` left EMPTY. Zero writes,
  ever, in this mode.
- Marc edits the CSV: puts `x` in `approve` on the correct pairs (or leaves
  them all empty).
- `repec-reconcile --apply-csv <path> --write`: stamps EXACTLY the approved
  pairs, re-enforcing every phase-1 invariant at apply time (target row
  still exists, `source_url` still empty, one stamp per doc_id per run, one
  stamp per ideas_url; violations → reported, skipped). Idempotent; goes
  through `rewrite_manifest`; CSV report of applied/skipped. Skip reasons
  (`skip_reason` column, first-match wins): `not-approved`, `bad-ideas-url`
  (the CSV is untrusted human input — an `ideas_url` that doesn't look like
  an IDEAS paper page, including an empty string, is rejected before any
  manifest lookup so a re-apply can't stamp `source_url=""` forever),
  `row-gone`, `bad-doc-type` (target row isn't D1/D2 — a hand-typed
  `candidate_doc_id` could otherwise point outside scope), `source-not-empty`,
  `duplicate-doc-id`, `duplicate-ideas-url`.
- Auditability: applied stamps are indistinguishable in the manifest from
  phase-1 stamps (same field, same semantics); the approved CSV is the human
  decision record — Marc keeps it (e.g. under `data/reports/`, gitignored,
  NAS-persisted).

### Class C — classification for free

After the next full sweep, `recover-downloads` (dry-run) over the full
inventory IS the classification: `recoverable` vs `unrecoverable` vs
already-converged, per bank, in one CSV. No separate tooling.

## Testing (adversarial fixtures, house rules — UTF-8 titles included)

- Inventory: dedup by pdf_url keeps the latest; converged entries skipped
  (both by pdf_url and by source_url); `--banks` filter.
- Wayback helper: latest-200-pdf snapshot parsing (CDX fixture), miss → None;
  exact-URL semantics (no prefix bleed).
- Recovery record: official URL stays `pdf_url`; raw snapshot lands in
  `alt_urls`; `provenance="wayback"`; with a fetcher stub where the official
  URL 403s and the snapshot 200s → document saved via fallback; dry-run
  downloads nothing (spy).
- Propose: candidates ranked, capped at 3, approve column empty; zero writes.
- Apply: approved pair stamped; unapproved untouched; stale CSV (row's
  source_url no longer empty / doc_id gone) → skipped + reported; double
  approval of one doc_id → first wins, second reported (phase-1 invariant);
  apply without `--write` = dry-run report.
- Full-row-set + atomicity inherited from phase 1 (`rewrite_manifest`).

## Rollout

1. Merge; image rebuild; Update (pull) cb-refresh.
2. `recover-downloads --banks fr` dry-run (campaign) → CSV → Marc reads →
   `--download` run → expect ~6 papers saved with `provenance="wayback"`
   (probe already confirmed ≥1 snapshot exists) + an honest unrecoverable
   list (7, in the reconstructed sample) for what Wayback genuinely doesn't
   hold — the live inventory may do better, since it carries `alt_urls` the
   reconstructed probe sample didn't.
3. `repec-reconcile --propose --banks gb` → Marc approves pairs in the CSV →
   `--apply-csv ... --write` → Sunday gb errors → ~0.
4. After next Sunday: full-inventory dry-run = class-C classification of
   se/it/jp; recover what Wayback holds; the rest is the documented residue.
