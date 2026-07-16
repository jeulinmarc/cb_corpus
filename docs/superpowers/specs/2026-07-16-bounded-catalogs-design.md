# Bounded nightly catalogs (fast sync) + weekly full sweep — design

Date: 2026-07-16
Status: direction approved by Marc 2026-07-15 ("ok go"), spec for implementation

## Context: where the 3 h 52 catalog phase goes

Production evidence (validation sync of 2026-07-15, container logs):

- **RePEc is the bulk.** `us: {'skip': 4212}` means 4 212 IDEAS paper pages were
  FETCHED, then discarded as already-known by `save_many` — dedup happens after
  the per-paper fetch. Summed over all series-wired banks (~14 000 papers) at
  ≥ 0.5 s/request ≈ **2 h 30+ per night of requests that conclude "known"**.
  `RePEcDiscovery.discover_bank` has no pre-fetch skip and no pagination bound.
- **BIS already has the right hook** (`skip_url=storage.is_known_url` wired in
  `run_bis_sitemap`, skips known speeches before their detail fetch). Remaining
  cost: every yearly sitemap since 1996 is re-walked, and speeches from
  institutions never saved (non-matching) are re-fetched every run forever.
- The IDEAS paper-page URL is stored in each repec row's `source_url`, but
  `Storage._load_existing` indexes only `pdf_url` + `alt_urls` — so the crawler
  cannot recognize a paper from its listing entry without fetching its page.

## Goal

Nightly catalogs in **minutes** instead of hours, with **zero coverage loss**:
late backfills and corrections are still caught by a weekly unbounded sweep.
Dedup stays on stable keys (handle/URL/sha256) — dates only bound the WALK,
never identity ([[dedup-never-by-date]]).

## Design

### 1. Storage: recognize papers by their source page

- `Storage._load_existing` additionally builds `self._source_urls` from each
  row's `source_url` (own set — deliberately NOT merged into `_urls`, whose
  semantics other skip hooks rely on).
- New method `is_known_source_url(url) -> bool`.

### 2. RePEc: pre-fetch skip + stop-on-known pagination

`RePEcDiscovery.discover_bank(code, skip_url=None, stop_on_known=False)`:

- For each paper-page URL from `parse_series_page`: if `skip_url(url)` → count
  it (no fetch, no yield) and continue.
- If `stop_on_known` and an entire listing page produced zero unknown papers →
  stop paginating THAT series (IDEAS lists newest first; an all-known page
  means the older tail is known too). A page with ≥ 1 unknown paper keeps
  pagination going, so a mid-list backfill still pulls the walk deeper.
- `pipeline.run_repec(..., incremental=False)`: when True, wires
  `skip_url=storage.is_known_source_url` and `stop_on_known=True`.
- CLI: `repec --incremental`.
- Count semantics: pre-fetch skips never reach `save_many`, so the per-bank
  log line of an incremental night reports only NEW work (plus errors). The
  weekly full pass keeps today's full audit counts. Documented in the runbook.

### 3. BIS: bound the walked years (existing plumbing)

No Python change: `bis-sitemap --years A-B` already exists, and the per-speech
skip hook already prevents re-fetching known speeches. The job wrapper computes
the year range from the freshness window (window start year → current year, so
a January window correctly spans two years).

### 4. `run-job.sh`: freshness window + `sync full`

- New env `SYNC_WINDOW_DAYS` (compose default `90`; unset/empty = unbounded).
- `sync` (bounded, when the window is set):
  - `bis-sitemap --years <year(today - WINDOW)>-<year(today)> --download`
  - `repec --incremental --download`
  - native fan-out unchanged.
- `sync full` (new optional argument), or `sync` with no window set: exactly
  today's unbounded behavior (no `--years`, no `--incremental`).
- Log line: `[sync] START` becomes `[sync] START (window <n>d)` /
  `[sync] START (full)` so `nas_runs.log` records which mode ran.

### 5. Schedule

```
0 1 * * 1-6 /app/deploy/run-job.sh sync
0 1 * * 0   /app/deploy/run-job.sh sync full
```

Nightly bounded Monday–Saturday; unbounded sweep Sunday (catches late
backfills, corrections, and anything a window would miss).

### 6. Expected effect

- RePEc incremental: ~1-2 listing pages per series (~40 series) + new papers
  only → minutes.
- BIS windowed: current year's sitemap(s) only; known speeches already skip.
- Nightly catalogs ≈ **10-20 min**; native fan-out unchanged (~10-40 min);
  Sunday full ≈ today's ~4 h.

## Testing

- pytest: storage `_source_urls`/`is_known_source_url`; repec `skip_url`
  (no fetch for known URLs — assert via a spy fetcher), `stop_on_known`
  (all-known page stops pagination; page with one unknown continues);
  `run_repec(incremental=True)` wiring.
- bash suite: bounded sync passes `--years` (computed range) and
  `--incremental`; `sync full` and window-unset omit both; START-line mode
  markers; crontab content; everything else from the existing suite unchanged.

## Deployment

Compose gains `SYNC_WINDOW_DAYS: "90"`. Image rebuild (crontab changed),
Dockge Update (pull!) + recreate — reminder: recreate without pull runs the
old image (bitten twice: 2026-07-15 cb-campaign, 2026-07-15/16 cb-refresh).
