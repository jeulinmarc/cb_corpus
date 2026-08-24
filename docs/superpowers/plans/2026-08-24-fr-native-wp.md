# fr Native WP Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** `cb_corpus/sources/bdf_wp.py` — native BdF D1 discovery over both publication systems, wired into the standard native-discovery path.

**Architecture:** two walkers (legacy year-pages 1994-2023, new paginated listing 2018-present) merged and deduped on the continuous WP number, new-system preferred in the overlap. Fixture-driven TDD, no live network in unit tests.

**Constraints:** Spec `docs/superpowers/specs/2026-08-24-fr-native-wp-design.md` is the contract — read first. English only. Templates to FOLLOW (style, error-handling, interface): `cb_corpus/sources/boj_wp.py` (legacy walker), `cb_corpus/sources/buba_wp.py` (paginated + detail-page walker). Never derive PDF filenames. `python3.13 -m pytest tests/ -q` green before every commit. Branch: `feat/fr-native-wp` (create from master at task 1).

### Task 0 (part of Task 1): Fetcher-vs-WAF probe + fixtures
Verify the repo `Fetcher`'s headers pass both hosts (one GET each on the listing URLs, assert 200 + expected marker string). If 403: per-source header override following any existing per-source pattern (grep for header customization in sources/) — never a silent global change. Capture fixtures into `tests/fixtures/bdf/`: one legacy year page (2010 — has prefix variants), one legacy year page mostly-empty, new listing page 0 + a mid page, two new detail pages (one with `Working Paper Series no.` in og:description, one where only the body has it). Strip nothing; store as-is.

### Task 1: Legacy walker + tests
`_iter_legacy(fetcher, years=None) -> Iterator[DocRecord]` per spec (month precision day-01, content-based row parsing, graceful missing years, scraped PDF links incl. revised-version variants). Tests from fixtures incl. adversarial list in spec. Commit `feat(fr): legacy BdF working-paper walker`.

### Task 2: New-system walker + tests
`_iter_new(fetcher, since=None) -> Iterator[DocRecord]` per spec (pagination, early stop on `since` when a whole page is older, detail-page/og:description number+authors extraction with body fallback, skip-with-warning when no number anywhere, day precision). Tests from fixtures. Commit `feat(fr): new-site BdF working-paper walker`.

### Task 3: Merge + wiring + tests
`discover_fr_wp(fetcher, since=None, years=None)` merges both walkers keyed on WP number (new system wins in overlap); register fr D1 exactly where/how buba_wp is registered (pipeline/native registry — study the call sites), bounded nightly vs full-sweep behavior inherited from that path. Tests: overlap dedup preference, since-bounded call skips legacy entirely, registry smoke (fr appears in the native discovery map). Full suite green. Commit `feat(fr): native D1 discovery wired into the pipeline`.

### Final wave
Full suite + grep gates (no infra, English) → requesting-code-review pass + fixes + re-review → PR (body announces the expected Sunday backfill volume ~600-1000 docs so the jump isn't read as an anomaly) → CI watched.

**Rollout (post-merge, no manual data ops):** nightly bounded runs pick fresh papers; the Sunday full sweep performs the historical backfill on the NAS (or trigger one manual `sync full` via Dockge if we don't want to wait).
