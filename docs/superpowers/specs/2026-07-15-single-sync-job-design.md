# Single nightly sync job + native-only discover — design

Date: 2026-07-15
Status: approved direction (Marc), spec for implementation review

## Context: what the 2026-07-14 production run taught us

The nightly all-banks parallel discover (PR #3) failed its first real runs: every
bank hit the 3 h per-bank timeout. Root cause, established by instrumentation
(455/455 requests of a 4-minute `se` scan went to www.bis.org, 0.52 s avg = the
politeness throttle; container network/DNS/IPv6 all measured healthy):

1. `GenericAdapter.discover_all` re-walks the ENTIRE shared BIS speech index per
   bank (`adapters/base.py:90`, `only_banks={code}`) and RePEc per bank
   (`base.py:111`). 63 banks = 63 full walks of the same catalogs per night.
2. Those catalogs are exactly what the `refresh` job (`bis-sitemap` + `repec`)
   already collects for all 63 banks in one pass, twice a day. The generic
   discover path is 100% redundant with it.
3. The parallel fan-out's "one bank = one host" politeness assumption is FALSE
   for these shared-catalog strategies: N workers = N concurrent walkers on
   bis.org from one IP (observed 3-4× server-side slowdown in the container).

Only 9 banks have native/TOML site coverage today (au, ca, ch, de, ecb, fr, gb,
jp, us — `ADAPTERS` + `INSTANCE_FACTORIES`); the other 54 fall back to
GenericAdapter (= shared catalogs only). Mac prototype of a native-only pass
(shared sources neutralized, dry-run): us 34 min (566 known skipped, 32 new
candidates), fr 18 s, se/at 0.1 s each.

## Goal

One permanent container, ONE scheduled job (`sync`, nightly) that:
- reads each shared catalog exactly once for all banks (BIS speeches, RePEc),
- then scans the banks' own sites in parallel, native sources only,
- commits once at the end.
No coverage loss versus refresh + discover; nightly wall-clock ~4 h (catalogs
~3 h 50 + native ~40 min) instead of an unbounded 63×-redundant crawl.

## Non-goals

- No change to what is collected (same sources, same documents).
- No async refactor; the per-bank process fan-out from PR #3 is kept for the
  native phase (hosts genuinely disjoint there).
- `campaign` job unchanged (on-demand stack, not a permanent container).
- No new native adapters (separate SCALING_TO_ALL_BANKS effort).

## Design

### 1. CLI/pipeline: `discover --native-only`

- `cb_corpus discover` gains `--native-only` (default off: full behavior
  unchanged for manual/campaign use).
- `pipeline.run(..., native_only=False)` forwards to
  `adapter.discover_all(scope=..., since=..., native_only=...)`.
- In `BankAdapter.discover_all`, `native_only=True` skips the shared-catalog
  fallback branches (`self._bis.discover(...)` for the C group and
  `self._repec.discover_bank(...)` for the D group). Native/TOML sources run
  exactly as today. A GenericAdapter bank therefore yields nothing (fast no-op).
- Unit tests (pytest): with `native_only=True`, the shared sources are never
  invoked; with the default, behavior is unchanged; a native adapter still
  yields its native types under `native_only=True`.

### 2. `run-job.sh`: jobs become `sync | campaign`

`refresh` and `discover` are REMOVED (breaking, deliberate — the whole point is
one job; `campaign` covers manual runs, e.g.
`campaign discover --banks fr --native-only --download`).

`sync` runs, under the single global lock:

1. `python -m cb_corpus bis-sitemap --download`
2. `python -m cb_corpus repec --download`
3. the per-bank native fan-out from PR #3, unchanged except each per-bank call
   gains `--native-only`
   (`discover --banks <code> [--types ...] --rounds <n> --native-only --download`).

- Phases 1-2 failing → job FAILED (rc of the failing phase), phase 3 not run.
- Phase 3 semantics unchanged from PR #3: per-bank logs in
  `reports/discover/<UTC-date>/`, `.ok`/`.failed` markers, summary
  `OK n/n` / `PARTIAL k/n FAILED: <codes>` / `FAILED 0/n banks: <codes>`,
  PARTIAL exits 0, all-failed exits 1.
- Log lines: `[sync] START`, `[sync] catalogs OK` after phase 2, then the
  summary line; `last_run_status` carries the summary verdict as today.
- ONE autocommit at the end of a successful (OK or PARTIAL) sync. A FAILED sync
  does not commit; local state is intact and the next sync picks it up
  (existing convention).
- Locking: `sync` keeps `flock -n` (a nightly job that finds the lock busy is
  itself the anomaly; skipping is correct). `campaign` keeps the blocking
  `flock`. `DISCOVER_LOCK_TIMEOUT` is REMOVED (no second scheduled job to wait
  for). `DISCOVER_BANK_TIMEOUT` (default 10800 s) stays on the native fan-out.

### 3. Schedule and environment

`deploy/crontab`:

```
0 1 * * * /app/deploy/run-job.sh sync
```

(01:00 Paris nightly; expected end ~05:00. The 12 h refresh line disappears.)

Environment (compose): unchanged from PR #3 minus `DISCOVER_LOCK_TIMEOUT`:
`DISCOVER_BANKS` (required, `all` or comma list — scopes the native phase),
`DISCOVER_TYPES` (`full` or list), `DISCOVER_ROUNDS` (default 1),
`DISCOVER_WORKERS` (default 6), optional `DISCOVER_BANK_TIMEOUT`, plus
`TZ`/`AUTOCOMMIT`.

### 4. Expected volumes/latency (for the runbook)

- Freshness: shared catalogs and native sites at most 24 h old (vs 12 h before
  for catalogs — accepted trade-off, Marc's call 2026-07-15).
- Native phase today: 9 active banks, longest us ≈ 35 min; 54 banks no-op.
  Politeness: one process per bank site; catalogs read once, sequentially.

### 5. Testing

- pytest: `--native-only` unit tests on the adapter layer (see §1).
- Bash suite (`tests/deploy/test_run_job.sh`), reworked from the PR #3 tests:
  - `sync` calls, in order: bis-sitemap, repec, then one discover per bank with
    `--native-only` and the expected args;
  - catalog failure aborts (no native phase, FAILED status, no autocommit);
  - native partial → PARTIAL + autocommit; native all-failed → FAILED exit 1;
  - refusal when `DISCOVER_BANKS` unset; `all` resolution via `list-banks`
    (footer filtered); bounded parallelism; per-bank timeout kill;
  - lock: second `sync` skips while one runs; `campaign` still waits;
  - `refresh`/`discover` job names now rejected (`unknown job`).
- Real validation (NAS, after merge + Dockge migration): one `campaign`-style
  sync on a subset, then the nightly cron watched via `nas_runs.log`.

### 6. Deployment notes

- Dockge `cb-refresh` stack: same env block minus `DISCOVER_LOCK_TIMEOUT`;
  image re-pull + recreate. FIRST fix the `deploy_key` mount: the stack folder
  currently has a `deploy_key` DIRECTORY (Docker created it when the file was
  missing at recreate time, 2026-07-14 — autocommit has been failing with
  "Load key ...: Is a directory" since); delete it and recreate the FILE via
  the Dockge file editor before deploying.
- The stack name `cb-refresh` is kept (renaming a Dockge stack is manual ops
  churn for zero functional gain).
