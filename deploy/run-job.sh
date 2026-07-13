#!/bin/bash
# Job wrapper: global lock + execution + log + state auto-commit.
# Usage: run-job.sh refresh | discover | campaign <cb_corpus sub-command...>
set -uo pipefail

JOB="${1:-}"; shift || true
APP_DIR="${CB_APP_DIR:-/app}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
LOCK="$DATA_DIR/.cb.lock"
LOG="$DATA_DIR/reports/nas_runs.log"
STATUS="$DATA_DIR/reports/last_run_status"

mkdir -p "$DATA_DIR/reports"
cd "$APP_DIR"   # the crawler writes to ./data (relative)

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) [$JOB] $*" >> "$LOG"; }

# Anti-overwrite guard: a volume without manifests means a missing seed or a
# wrong dataset path. We refuse to crawl (and thus to commit a partial state
# over the real one). CB_ALLOW_EMPTY_DATA=1 for a deliberate bootstrap.
if [ "${CB_ALLOW_EMPTY_DATA:-0}" != "1" ]; then
  if ! ls "$DATA_DIR"/manifest/*.jsonl >/dev/null 2>&1; then
    log "REFUSED (volume without manifests — missing seed? CB_ALLOW_EMPTY_DATA=1 to force)"
    echo "$(ts) REFUSED [$JOB]" > "$STATUS"
    exit 3
  fi
fi

case "$JOB" in
  refresh|discover|campaign) ;;
  *) echo "run-job: unknown job '$JOB'" >&2; exit 2 ;;
esac

run_job() {
  case "$JOB" in
    refresh)
      python -m cb_corpus bis-sitemap --download \
        && python -m cb_corpus repec --download ;;
    discover)
      if [ -z "${DISCOVER_ARGS:-}" ]; then
        echo "run-job: DISCOVER_ARGS not set — refusing an implicit full A-F discover" >&2
        return 2
      fi
      # DISCOVER_ARGS is deliberately word-split (list of options).
      # shellcheck disable=SC2086
      python -m cb_corpus discover ${DISCOVER_ARGS:-} --download ;;
    campaign)
      python -m cb_corpus "$@" ;;
  esac
}

exec 9>"$LOCK"
if [ "$JOB" = "campaign" ]; then
  flock 9   # a campaign waits its turn (refresh in progress, etc.)
else
  if ! flock -n 9; then
    log "SKIPPED (lock busy)"
    exit 0
  fi
fi

log "START"
if run_job "$@"; then
  log "OK"
  echo "$(ts) OK [$JOB]" > "$STATUS"
  if [ "${AUTOCOMMIT:-1}" = "1" ]; then
    "${AUTOCOMMIT_BIN:-/app/deploy/autocommit.sh}" "$JOB" >> "$LOG" 2>&1 \
      || log "AUTOCOMMIT FAILED (local state intact, will retry on next run)"
  fi
  exit 0
else
  rc=$?
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
