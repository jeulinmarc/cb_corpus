# Cadence Watchdog (PR 4) — Combined Spec + Plan

**Date:** 2026-08-24 · **Scope:** one small PR (Marc-approved single-doc format) ·
**Origin:** the production review's manual audit, automated; output designed for
the RAGDataOrchestrator dashboard's existing "Upcoming & overdue" section.

## Problem

Nothing in the system notices when a series goes SILENT (fr D1: 213 days
unnoticed; ecb D3: 83). Errors are logged; silence is not. The manual audit
that caught these must become a weekly job.

## Design decisions

1. **Method: median-gap ONLY.** For each `(bank_code, doc_type)` with ≥6 docs
   in the last 3 years: `interval_days` = median gap between consecutive
   publication dates; `next_expected = last + interval_days`; `status`:
   `overdue` (today > next_expected + 7d), `soon` (next_expected ≤ 60d away),
   else `on-track`. NO flat docs-per-window statistic — it false-alarmed on
   lumpy quarterly series (ca E1, it/es D1) in the review. A lumpy series'
   median gap absorbs its rhythm naturally. Thresholds mirror the
   orchestrator's existing vocabulary exactly.
2. **Output: `data/cadence.jsonl`** (data root, beside `manifest`/
   `download_errors.jsonl` — the orchestrator's file-discovery convention).
   One JSON line per series:
   `{bank_code, doc_type, last, interval_days, next_expected, days_until,
   status, expected_per_year, n_3y}` — lowercase bank codes, doc-type CODES,
   ISO dates (the orchestrator parses `value[:10]` as `%Y-%m-%d`).
   `expected_per_year = round(365/interval_days)`. Gitignored (derived
   operational state, regenerated weekly; add explicit .gitignore entry).
3. **Alert only on NEW silences.** State file `data/cadence_state.jsonl`
   (append-only, latest-per-series wins, same conventions as quarantine):
   remembers which series are already known-overdue; a run logs
   `cadence: N overdue (M new)` to stderr plus ONE loud line per NEW overdue
   series; a series that recovers writes a release line and logs it.
   Corrupt lines tolerated per-line (house resilience style).
4. **Scheduling:** new job `cadence` in `deploy/run-job.sh` running
   `python -m cb_corpus.cli cadence-watch --write`; crontab line
   `30 6 * * 0` (Sunday 06:30 Paris — after the ~01:00+4.5h full sweep).
   CLI: dry-run by default prints the table; `--write` emits the JSONL +
   updates state. Thresholds env-overridable
   (`CADENCE_OVERDUE_GRACE_DAYS=7`, `CADENCE_SOON_DAYS=60`,
   `CADENCE_MIN_DOCS=6`, `CADENCE_LOOKBACK_YEARS=3`) — no hard-coded
   out-of-spec constants.
5. **Known-closed series** (e.g. a future `ecb G2` decision) can be silenced
   via a `{"bank_code","doc_type","muted": true, "reason"}` line in the state
   file (seeded manually) — muted series are still WRITTEN to cadence.jsonl
   with their real status (dashboard honesty) but never alerted.

## Plan (tasks — subagent-driven, TDD)

**Task 1 — `cb_corpus/cadence.py` + CLI.** Pure computation from
`iter_manifest_rows` (no network): series stats, statuses, JSONL writer,
state handling (new-overdue detection, recovery, mute), stderr summary.
CLI subcommand `cadence-watch` (argparse pattern per cli.py). Tests
(`tests/test_cadence.py`, fixture manifests, adversarial): lumpy quarterly
series with a window-empty period → NOT overdue (the ca E1 regression case);
steady series gone silent → overdue; exactly-at-boundary (+7d) cases; new vs
already-known overdue; recovery release; muted series written-not-alerted;
dateless rows ignored; corrupt state line tolerated; env threshold overrides.
Commit: `feat(cadence): median-gap watchdog with new-silence alerting`.

**Task 2 — deploy wiring.** `run-job.sh` `cadence` job + crontab
`30 6 * * 0` line + a short deploy/README paragraph (what it does, where the
output lands, the mute mechanism). `bash -n` + grep verification + a
deploy-test if the existing `tests/deploy/` harness covers job dispatch.
Commit: `feat(deploy): weekly cadence watchdog after the Sunday sweep`.

**Final wave.** Full suite + grep gates → requesting-code-review pass +
fixes + re-review → PR (body links the orchestrator integration as the
documented follow-up: ~15-line `load_cadence()` in catalog.py replacing the
`EXPECTED_PER_YEAR` stub — a separate tiny PR in THAT repo) → CI watched.

## Out of scope

The orchestrator-side loader (separate repo, separate small PR); any
dashboard/UI work; replacing the adapters' `expected_per_year` config; the
ecb G2 decision itself (but the mute mechanism is how it will be recorded).
