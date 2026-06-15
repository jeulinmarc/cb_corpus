# Scaling native discovery to all banks × all types (A–F)

Captures the planning discussion of 2026-06-15: what it would take to extend
bank-native discovery (today: **5 banks, working papers only**) to **all 63 banks
across every doc type A–F**, and how parallel Claude Code agents change the
estimate. This is forward-looking planning — nothing here is implemented yet.

## The goal

Today native discovery covers D1/D2 (working/occasional papers) for 5 banks
(`ecb`/`us`/`jp`/`gb`/`de`) plus the existing A/B/E/F natives on the hand-coded
majors. "All banks × A–F" means a native scraper for **every doc type on every
bank's own site**, with RePEc/BIS demoted to audit/fallback everywhere (the
inversion [WP_ARCHITECTURE_V3.md](WP_ARCHITECTURE_V3.md) applied to all types).

## Why there is no economy of scale

~80 % of per-bank effort does **not** amortise across banks:

- **live investigation** — find the source, structure, date format, URL patterns, quirks;
- **bugs that only surface in a real run** — Fed alone had 4 (IFDP naming, `r1`
  revisions, precision, the `econresdata` path);
- **reverse-engineering JS sites** — ecb `foedb` DB; de `bbksearch` endpoint found via chrome-devtools;
- **crawl wall-clock** — network-throttled, minutes → ~40 min per bank.

Calibration from the 5 done: effort **×1 (jp, inline-date table) → ×4 (de, JS
pagination)**. And these 5 are the *best-documented*: the long tail is harder
(obscure / non-English / anti-bot sites), and **~1/3 of small banks have no
scrapable native series at all** → they stay on RePEc/BIS by necessity, not choice.

## Estimates

| Scenario | Calendar |
|---|---|
| Solo, sequential | **~6–12 months** |
| ~10–16 Claude Code agents in git worktrees | **~1–2 months** (bulk in ~3–6 weeks + integration/QA) |

Parallel speedup is **~4–6×, not ~15×** — bounded by:

- **Amdahl** — serial fraction ~15–20 % (integration, shared-file merges,
  cross-bank consistency, the hardest-bank critical path). `1/(0.2 + 0.8/12) ≈ 4.3×`.
- **Concurrency cap** — Claude Code runs at most ~`min(16, cores−2)` agents at once.

What parallelises well: per-bank dev / test / migration (independent), and the
migration crawls (each hits a different host). What does **not**: the archive.org
Wayback day-recovery (single host → run as a separate cron), and the final
integration + full-test-suite runs.

## The unblocker: refactor to auto-registration FIRST (~1–2 days)

Today each new bank edits **shared files** — `wp_migrate._NATIVE` /
`_KEY_FROM_PDF` / `_KEY_FROM_HANDLE` dicts and `adapters/__init__.py` — so parallel
branches collide on merge (the serial fraction above). Refactor so **each bank is
one self-contained, auto-discovered file** (registry / decorator pattern; zero
edits to shared files). Then branches are near-conflict-free and the parallel
speedup approaches its ceiling. **Do this before any fan-out** — it is the single
change that most increases the achievable parallelism.

## Orchestration shape

- **Workflow fan-out**, one git worktree per bank (`isolation: 'worktree'`).
- **Per-bank agent chain:** investigate → implement scraper(s) → tests → migration
  dry-run → verify (zero re-download, the load-bearing invariant) → structured report.
- **Adversarial-verification layer per bank** — don't ship Fed-style bugs ×63
  unreviewed. Costs extra agents but is mandatory (eats into the speedup).
- **Wayback day-recovery stays a separate scheduled cron** (shared host).

## Caveats (honest)

- **Token cost** scales with the fan-out — dozens of multi-hour agents.
- **Quality** requires the verify-before-write discipline held at scale, not dropped for speed.
- **~1/3 dead-ends** — small banks with no native source; agents spend time
  confirming the impasse and falling back to RePEc.
- A few **"wall" sites** (anti-bot, opaque, non-English) will need human judgment
  on the critical path.

## Recommended phasing

1. **Auto-registration refactor** (~1–2 days) — the unblocker above.
2. **8 remaining RePEc-WP banks** (it/es/fr/ca/ch/se/nl/au) native WP — **~1 week,
   highest ROI**: gives day-precision to papers *already in the corpus* (vs RePEc
   month). This is the recommended next concrete step.
3. **(Optional) full A–F fan-out** for all 63 — the ~1–2 month parallel project
   above. Pursue only if the marginal coverage justifies the cost — RePEc/BIS
   already covers most of what it would add.

---

See [WP_V3_SUMMARY.md](WP_V3_SUMMARY.md) for what shipped, and
[REPEC_AS_CHECK.md](REPEC_AS_CHECK.md) for the RePEc-as-audit model that this
would extend from working papers to all doc types.
