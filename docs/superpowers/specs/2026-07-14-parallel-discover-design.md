# Parallel daily discover across all banks — design

Date: 2026-07-14
Status: approved approach (Option A — shell-level parallelism), spec pending review

## Context

The NAS deployment (Dockge stack `cb-refresh`) currently runs two cron jobs inside
the container (`deploy/crontab`):

- `refresh` every 12 h (00:00 / 12:00 Paris): `bis-sitemap --download` + `repec --download`.
  Observed duration in production: ~3 h 52 (2026-07-13 22:00Z → 2026-07-14 01:52Z).
- `discover` weekly (Sunday 03:00): `discover ${DISCOVER_ARGS} --download`, currently
  scoped to `--banks us,ecb --types A3 --rounds 1`.

The crawler is sequential (one bank after another) with a 0.5 s per-host minimum
delay (`FetchConfig.min_delay_seconds`). The 63 banks live on 63 distinct hosts, so a
sequential incremental A–F pass over all banks would take hours (est. 2–5 h), while
the per-host politeness budget would allow crawling banks concurrently with no extra
load on any single site.

## Goal

Run an **incremental** (`--rounds 1`) **discover with download** over **all 63 banks**
and the **full A–F scope**, **nightly**, in a reasonable wall-clock time (~30–90 min),
without violating per-host politeness and without a Python async refactor.

## Non-goals

- No full-history recrawl (the historical corpus is already seeded).
- No in-process (thread/async) parallelism — that is the separate refactoring
  item #4 and is out of scope here.
- No change to the `refresh` job's content or schedule.
- No per-bank native-connector work (separate SCALING_TO_ALL_BANKS effort).

## Design (Option A — shell-level fan-out in `run-job.sh`)

### 1. Configuration interface (environment variables)

`DISCOVER_ARGS` (free-form string) is **removed** and replaced by dedicated
variables, because the job now needs to decompose the bank list itself:

| Variable | Compose value | Script default | Meaning |
|---|---|---|---|
| `DISCOVER_BANKS` | `all` | *(none — required)* | `all` or a comma list of bank codes (`us,ecb,fr`) |
| `DISCOVER_TYPES` | `full` | `full` | `full` (= omit `--types`, CLI defaults to full A–F) or a comma list (`A3,E2`) |
| `DISCOVER_ROUNDS` | `1` | `1` | passed as `--rounds` |
| `DISCOVER_WORKERS` | `6` | `6` | number of banks crawled concurrently |

Guard preserved: `DISCOVER_BANKS` has **no script default** — if unset/empty the
job refuses to run (same spirit as today's refusal when `DISCOVER_ARGS` is
empty: an implicit full crawl must be an explicit choice, here
`DISCOVER_BANKS=all` in the compose).

**Breaking change (deliberate):** the live Dockge stack must switch from
`DISCOVER_ARGS` to the new variables and be recreated. Documented in
`deploy/README.md` with the exact env block to paste.

### 2. Parallel discover job (`run-job.sh`)

1. Resolve the bank list: `all` → `python -m cb_corpus list-banks | awk '{print $1}'`
   (first column is the bank code); otherwise split the comma list.
2. Fan out with `xargs -P "$DISCOVER_WORKERS"`, one process per bank:
   `python -m cb_corpus discover --banks <code> [--types <list>] --rounds <n> --download`.
3. Per-bank log: `reports/discover/<UTC date>/<code>.log` (stdout+stderr of that
   bank's process). `nas_runs.log` keeps one summary line:
   `OK 63/63` or `PARTIAL 58/63 FAILED: xx,yy,zz`.
4. **Partial failures are non-blocking**: a failing bank does not stop the others
   and does not prevent the final autocommit (per-bank state is consistent; the
   failed bank is simply retried the next night). `last_run_status` reflects
   `OK` (all banks) or `PARTIAL` (some failed, listing the codes). The job exits
   non-zero only if **all** banks failed (systemic problem: network down, bad
   image, refused lock…).
5. **Single autocommit at the end**, exactly as today (one commit
   `data: NAS discover <date>` per night).

Write-safety analysis (why per-bank processes don't conflict):

- `data/manifest/<bank>.jsonl` and `data/raw/<bank>/…` are per-bank by
  construction — disjoint between processes.
- `data/discovery_errors.jsonl` is a shared append-only audit file. Concurrent
  line appends in `O_APPEND` mode are atomic for reasonably sized lines;
  accepted limitation, documented in a comment. (If it ever bites, switch to
  per-bank error files merged at the end — not done now, YAGNI.)
- The HTTP fetcher throttle is per-host and per-process; each process crawls
  one bank = one host, so politeness is preserved (0.5 s per host unchanged).

### 3. Scheduling and locking

`deploy/crontab` becomes:

```
0 */12 * * * /app/deploy/run-job.sh refresh
0 4 * * *    /app/deploy/run-job.sh discover
```

- The **Sunday 03:00 line is removed** (replaced by the nightly job).
- Discover is scheduled at 04:00 Paris, right after the 00:00 refresh typically
  ends (~03:52 observed).
- Lock behavior change: `discover` moves from `flock -n` (silent skip when busy)
  to **`flock -w 7200`** (wait up to 2 h for an overrunning refresh, then give
  up with a `SKIPPED (lock timeout)` log line). `refresh` keeps `flock -n`
  (if a discover is still running at noon, the refresh skips cleanly — next one
  is 12 h later).
- The whole fan-out runs **under the single global lock** (the lock protects
  the dataset against concurrent *jobs*, not concurrent banks within one job).

### 4. Resource bounds

- Wall-clock estimate: 63 banks × 3–10 min ÷ 6 workers ≈ **30–90 min**.
- RAM peak: ~6 × (~150 MB Python + ~300 MB headless Chromium when a page needs
  rendering) ≈ **2–3 GB worst case**, usually much less (Chromium is spawned
  only for render-requiring pages). Acceptable on a TrueNAS box; ZFS ARC yields
  cache under memory pressure.
- `DISCOVER_WORKERS` is the single knob bounding CPU (Chromium), RAM and
  aggregate bandwidth; tunable in Dockge without rebuilding the image.

### 5. Testing

- Bash tests for the fan-out using a fake `python` shim on `PATH` that records
  its invocations, verifying:
  - one invocation per bank with the exact expected arguments
    (incl. `--download`, `--rounds`, types omitted when `full`);
  - bounded parallelism (never more than `DISCOVER_WORKERS` concurrent);
  - partial-failure summary (`PARTIAL n/m FAILED: codes`) and exit codes
    (0 on partial, non-zero when all banks fail);
  - single autocommit invocation after the fan-out;
  - refusal when `DISCOVER_BANKS` is empty; `all` resolution via `list-banks`.
- Real validation on the NAS before enabling the nightly cron in confidence:
  one manual `cb-campaign`-style run on a subset (`DISCOVER_BANKS=nl,at,se`),
  then one full run (`all`), checking `reports/discover/<date>/` logs, the
  summary line, the commit, and wall-clock duration.

### 6. Deployment notes (for `deploy/README.md`)

- New env block for the `cb-refresh` stack (replaces `DISCOVER_ARGS`):

  ```yaml
  environment:
    TZ: Europe/Paris
    DISCOVER_BANKS: all
    DISCOVER_TYPES: full
    DISCOVER_ROUNDS: "1"
    DISCOVER_WORKERS: "6"
    AUTOCOMMIT: "1"
  ```

- Rebuild + push the image (crontab changed), update the stack env in Dockge,
  recreate the stack.
- First nightly run should be watched via `reports/nas_runs.log` and
  `reports/discover/<date>/`.
