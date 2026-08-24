# Silent Series Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** ecb D3 recurring discovery from the live master blog listing; native Riksbank WP source for se D1; ecb C2 wired with honest interim Wayback fallback.

**Constraints:** Spec `docs/superpowers/specs/2026-08-24-silent-series-design.md` = contract, read first. Worktree /Users/marc/Desktop/All CODING/GENERALI/cb_corpus-pr3, branch fix/silent-series (checked out). English only. Fixtures for unit tests (live network only to CAPTURE fixtures, gently). `python3.13 -m pytest tests/ -q` green before each commit. Study before coding: `cb_corpus/adapters/ecb.py` (+ `adapters/base.py` supported_types/discover_all), `cb_corpus/pipeline.py` run_ecb_pub_recovery, `cb_corpus/sources/ecb_pub.py`, an existing native WP source for the Riksbank template.

### Task 1: ecb D3 native discovery
Fixture: capture `/press/blog/html/index.en.html`. Failing tests: anchors→DocRecords (D3, day precision where the listing provides it, provenance bank_site), malformed anchor skipped w/ warning, native_types now contains D3 and discover_all reaches it (registry test). Implement per spec A. Commit `feat(ecb): D3 blog discovery from the live master listing`.

### Task 2: se native Riksbank WP source
Fixtures: WP listing page(s). Failing tests: number/title/date extraction, scraped PDF URLs (incl. an odd filename row), dual-source dedup expectation (same WP number as a RePEc-era row → alt_urls guard, no re-download), wiring smoke. Implement per spec B (new `sources/riksbank_wp.py` + registration). Commit `feat(se): native Riksbank working-paper source`.

### Task 3: ecb C2 interim wiring
Fixture: a 404 response for a dead year + a live ≤2024 include page. Failing tests: C2 in native_types/recurring path; per-year 404 triggers the existing `cdx_fallback_prefix` path; docstring states the interim limitation. Implement per spec C. Commit `feat(ecb): C2 interviews wired with interim Wayback fallback`.

### Final wave
Full suite + grep gates → requesting-code-review + fixes + re-review → PR (body: verdicts incl. the three false alarms and the monitor-methodology handoff to the cadence watchdog; expected backfill via Sunday sweep) → CI watched. Note in PR: potential wiring-file overlap with feat/fr-native-wp (PR2) — merge sequentially, rebase whichever lands second.
