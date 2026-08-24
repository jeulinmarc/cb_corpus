# Recover-the-76 + Download Quarantine — Design

**Date:** 2026-08-24 · **Status:** approved direction (Marc, production-review follow-up) · **Scope:** one PR

## Context

One month of production left exactly 76 unique documents failing every night
(dead RePEc-sourced PDF URLs; `download_errors.jsonl` audit). A hunt with
Wayback + current bank sites + legitimate mirrors recovered 74+/76 as verified
local PDFs. This PR (a) injects externally-recovered files into the corpus
through the existing storage discipline, (b) stops the nightly re-hammering of
dead URLs via a quarantine, (c) documents the residue honestly.

## Decisions

1. **`recover-downloads --candidates <results.jsonl>`** — new optional input:
   externally-found recoveries, one JSON per line:
   `{dead_pdf_url, file_path (verified local PDF), recovered_from:
   "wayback"|"bank_site"|"mirror", final_url, title?}`. For each entry the
   command matches the audit-trail entry by `dead_pdf_url`, refreshes
   title/date from the IDEAS page (existing `_refresh_metadata`), builds the
   `DocRecord`, copies the file to `Storage.target_path(rec)` and registers it
   via `Storage.reindex` (sha256 dedup, manifest append — never bypassing
   storage). Dry-run by default; `--download` writes (consistent with the
   command's existing contract).
2. **Provenance rules** (honest description of where bytes come from):
   - Wayback snapshot → `provenance="wayback"`, `pdf_url` = original official
     URL, snapshot raw URL first in `alt_urls` (existing recover.py rule).
   - Relocated live official URL → `provenance="bank_site"`, `pdf_url` = the
     NEW live URL (points at reality), original dead URL kept in `alt_urls`.
   - Legitimate mirror / co-publication (EconStor, publisher, sister bank) →
     `provenance="mirror"`, `pdf_url` = original official URL (the WP's
     identity), mirror URL in `alt_urls`.
   Recovered-as-revised/retitled editions keep the refreshed official title —
   the recovery report (CSV in `data/reports/`) records the nuance per doc.
3. **Quarantine** — in the nightly path that consumes candidate URLs:
   a state file `data/download_quarantine.jsonl` (append-only, latest line per
   URL wins) tracks per-URL consecutive *distinct failing nights*. Once
   `QUARANTINE_AFTER_NIGHTS` (env, default **5**) is reached, the URL is
   skipped by bounded (Mon–Sat) syncs — one summary log line
   `quarantine: skipped N url(s)` — and retried ONLY by the Sunday full sweep
   (`sync full`). Any success removes the URL from quarantine (tombstone
   line). Corrupt/torn state file → warn and treat as empty (same resilience
   stance as the manifests). Docs that remain unrecoverable after the manual
   hunt are seeded into quarantine so the nightly noise stops immediately.
4. **Unrecoverables are documented, not hidden**:
   `data/reports/recover_unrecoverable.jsonl` (JSONL, not .md — the repo's
   5-committed-.md rule) lists each with the full evidence trail of the failed
   hunt: every technique tried, where the paper was last seen alive.
5. **Out of scope**: the 14 dateless docs (traced separately if quick, else
   next PR), fr-native source, ecb D3/C2/ca E1 investigations (PR 2/3 of the
   maintenance wave).

## Testing

TDD. Unit: candidates parsing (bad lines, missing file, unknown dead_pdf_url,
duplicate content), provenance mapping per rule, quarantine state round-trip
(N-nights counting on DISTINCT nights, success tombstone, corrupt tail,
threshold boundary, Sunday-bypass flag). Integration-style: inject a fake
candidate into a tmp corpus via reindex and assert manifest row + file layout.

## Rollout

Injection runs locally (Mac) against the git checkout; manifests committed on
the branch; recovered raw files rsync'd to the NAS via SMB (same layout). The
quarantine STATE file is operational data living beside
`download_errors.jsonl` on the deployment data dir (NAS) — never committed;
the unrecoverable seed is written there via SMB. The vault ingests the new
rows on its next hourly run with zero action.
