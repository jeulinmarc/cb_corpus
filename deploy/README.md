# NAS Deployment (Dockge) — runbook

Spec: `docs/superpowers/specs/2026-07-12-nas-docker-deploy-design.md`
on the `documentation` branch (process artifacts don't live on master).
Absolute rule: **no real infra value** (IP, hostname, real /mnt paths,
UID) must ever be committed — values live in Dockge and in untracked
local notes (`*.local.md`).

## 0. Prerequisites (one-time)

1. **Deploy key** (on the Mac):
   `ssh-keygen -t ed25519 -f nas_deploy_key -N "" -C "cb-corpus-nas-state"`
   GitHub → repo → Settings → Deploy keys → "Add deploy key", paste
   `nas_deploy_key.pub`, **check "Allow write access"**.
2. **GHCR visibility**: after the first CI build, GitHub → profile → Packages →
   `cb_corpus` → Package settings → Change visibility → **Public**
   (otherwise the NAS can't pull without authentication).

## 1. Path/UID discovery (disposable stack)

Dockge → new stack `cb-probe` → paste `compose.discover-ids.example.yml`
→ Deploy → read the logs: note the `/mnt/<pool>/<dataset>` path of the
SMB share and the owning UID/GID (files created via the SMB share). Delete the stack.

## 2. Initial seed (MANDATORY before the first run)

Without a seed, the first run would re-download ~38,000 documents and the
non-recrawlable Wayback state would be lost. From the Mac, with the SMB share mounted:

```bash
# adjust the destination to the share mounted in Finder
DST="/Volumes/<share>/<dataset_path>"
rsync -rt --progress "data/manifest" "$DST/"
rsync -rt --progress "data/wp_dates_index.jsonl" "$DST/"
rsync -rt --progress "data/raw" "$DST/"      # 8.2 GB — several hours

# integrity check (counts must match)
find data/raw -type f | wc -l
find "$DST/raw" -type f | wc -l
ls data/manifest/*.jsonl | wc -l
ls "$DST/manifest/"*.jsonl | wc -l
```

After the seed: the Mac **stops crawling**; its `data/` becomes an archive
(do not delete without an explicit decision).

The container **refuses** to run on a volume without manifests (status
`REFUSED` in `nas_runs.log`) — this is the missing-seed protection;
`CB_ALLOW_EMPTY_DATA=1` for a deliberately empty bootstrap.

## 3. `cb-refresh` stack

Dockge → new stack `cb-refresh` → paste `compose.refresh.example.yml` →
replace `POOL/DATASET/PUID/PGID` → drop the private key `nas_deploy_key`
into the stack's folder under the name `deploy_key` (Dockge file editor)
→ Deploy.

The `deploy_key` file must be **readable** by the PUID user:
autocommit copies the key as 0600 into a private temp directory (0700, removed at the end of the run), so a 0644 key works;
a root:0600 key will fail with a clear message in `nas_runs.log`.

Schedule: one sync nightly at 01:00 (Paris). Each sync catalogs once for all
banks (bis-sitemap, repec), then proceeds to parallel native bank-site
discovery. Scope for the native phase is controlled by env vars (Dockge, no
rebuild needed): `DISCOVER_BANKS` (`all` or comma list — required, the job
refuses to run without it), `DISCOVER_TYPES` (`full` = whole A–F scope, or
comma list), `DISCOVER_ROUNDS` (1 = incremental), `DISCOVER_WORKERS` (parallel
banks, default 6 — the single knob bounding CPU/RAM/bandwidth),
`DISCOVER_BANK_TIMEOUT` (seconds per bank before the crawl is killed and the
bank is counted as failed, default 10800). **Migration:** stacks created before
2026-07-15 used the refresh/discover job pair — the crontab and job names
changed; recreate the stack after re-pulling the image.

## 4. `cb-campaign` stack (on demand)

Dockge → stack `cb-campaign` → paste `compose.campaign.example.yml` →
replace the placeholders and the `command:` line → drop the private key `nas_deploy_key`
into the stack's folder under the name `deploy_key` → Deploy. The container
waits for any running sync or campaign to finish (lock), runs, pushes the state, stops.
To launch another campaign: re-edit `command:` + Deploy.

## 5. Sanity checks

- `data/reports/nas_runs.log` and `last_run_status` visible in Finder (SMB).
- A recent PDF appears under `raw/<bank>/...` in Finder.
- A `data: NAS sync <date>` commit appears on GitHub after a useful run, with `[sync] catalogs OK` in the log output.
- Files created by the container belong to you via SMB (otherwise revisit PUID/PGID).
- A Dockge stop/redeploy mid-run kills the current job (status `FAILED` or
  absent) — the lock is released automatically and the next cron tick
  picks up again; this is expected.
- After the nightly sync: per-bank discovery logs under `data/reports/discover/<date>/`,
  one summary line `OK n/n` or `PARTIAL k/n FAILED: <codes>` in `nas_runs.log`,
  and a `data: NAS sync <date>` commit on GitHub when discovery changed.
- A `PARTIAL` status is not an emergency: failed banks are retried the next
  night. Investigate a bank only when it fails several nights in a row
  (its log under `reports/discover/<date>/<code>.log` has the traceback).

## 6. Code updates

Push to master → CI rebuilds `ghcr.io/.../cb_corpus:latest` → in Dockge:
re-pull the image and redeploy the stacks.
