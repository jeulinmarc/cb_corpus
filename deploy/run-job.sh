#!/bin/bash
# Job wrapper: global lock + execution + log + state auto-commit.
# Usage: run-job.sh sync | campaign <cb_corpus sub-command...>
set -uo pipefail

JOB="${1:-}"; shift || true

SYNC_MODE="full"
if [ "$JOB" = "sync" ]; then
  if [ "${1:-}" = "full" ]; then
    shift
  elif [ -n "${SYNC_WINDOW_DAYS:-}" ]; then
    SYNC_MODE="window"
  fi
fi

APP_DIR="${CB_APP_DIR:-/app}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
LOCK="$DATA_DIR/.cb.lock"
LOG="$DATA_DIR/reports/nas_runs.log"
STATUS="$DATA_DIR/reports/last_run_status"

mkdir -p "$DATA_DIR/reports"
cd "$APP_DIR" || exit 1   # the crawler writes to ./data (relative)

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
  sync|campaign) ;;
  *) echo "run-job: unknown job '$JOB'" >&2; exit 2 ;;
esac

# Cheap fail: a malformed SYNC_WINDOW_DAYS must not reach the lock or spawn
# any python — caught here, before the lock is taken.
if [ "$SYNC_MODE" = "window" ]; then
  case "$SYNC_WINDOW_DAYS" in
    *[!0-9]*|'')
      log "FAILED (invalid SYNC_WINDOW_DAYS)"
      echo "$(ts) FAILED [$JOB] rc=2" > "$STATUS"
      exit 2 ;;
  esac
fi

# ---- discover: parallel per-bank fan-out ------------------------------------
# One bank = one process = one host: the 0.5s per-host politeness throttle is
# untouched, and manifest/raw writes normally target one bank per process.
# They are NOT fully disjoint though: any Storage init's torn-manifest loader
# may repair another bank's torn tail (a leftover from a killed prior run), so
# it can touch any bank file, not just the one this process is discovering.
# Storage._append() and the repair path both take an exclusive fcntl flock on
# the target manifest file, so a same-host append/repair race is safe.
# data/discovery_errors.jsonl is shared across banks: O_APPEND line-sized
# writes, accepted limitation.

resolve_banks() {
  if [ "$DISCOVER_BANKS" = "all" ]; then
    # list-banks output ends with a blank line and an "N banks" footer
    # (cb_corpus/cli.py); real bank codes are 2-4 lowercase letters, so
    # filter on that shape to keep the footer out of the bank list. The
    # {2,4} POSIX interval is mawk-version-dependent (older mawk builds
    # treat it literally instead of as a repeat count), so this is spelled
    # out letter-by-letter instead -- identical accept/reject sets, no
    # dependency on interval support.
    python -m cb_corpus list-banks | awk 'NF && $1 ~ /^[a-z][a-z][a-z]?[a-z]?$/ {print $1}'
  else
    echo "$DISCOVER_BANKS" | tr -d '[:space:]' | tr ',' '\n' | sed '/^$/d'
  fi
}

discover_one() {
  bank="$1"
  set -- python -m cb_corpus discover --banks "$bank"
  if [ "$DISCOVER_TYPES" != "full" ]; then
    set -- "$@" --types "$DISCOVER_TYPES"
  fi
  set -- "$@" --rounds "$DISCOVER_ROUNDS" --native-only --download
  # Per-bank wall-clock bound so one wedged bank cannot hold the global lock
  # forever: SIGTERM at DISCOVER_BANK_TIMEOUT, SIGKILL 60s later if still alive.
  if timeout -k 60 "${DISCOVER_BANK_TIMEOUT:-10800}" "$@" > "$DISCOVER_LOG_DIR/$bank.log" 2>&1; then
    echo "$bank" >> "$DISCOVER_LOG_DIR/.ok"
  else
    echo "$bank" >> "$DISCOVER_LOG_DIR/.failed"
  fi
  return 0   # one failing bank must not abort the batch
}
export -f discover_one

JOB_SUMMARY=""

run_discover() {
  export DISCOVER_TYPES="${DISCOVER_TYPES:-full}"
  export DISCOVER_ROUNDS="${DISCOVER_ROUNDS:-1}"
  export DISCOVER_BANK_TIMEOUT="${DISCOVER_BANK_TIMEOUT:-10800}"
  # DISCOVER_LOG_DIR is set (and created) by the caller (run_sync), before
  # the catalog phases, so their tee'd output lands in the same directory.
  rm -f "$DISCOVER_LOG_DIR/.ok" "$DISCOVER_LOG_DIR/.failed"

  local banks total ok failed
  banks=$(resolve_banks)
  if [ -z "$banks" ]; then
    echo "run-job: empty bank list" >&2
    return 2
  fi
  echo "$banks" | xargs -n1 -P "${DISCOVER_WORKERS:-6}" bash -c 'discover_one "$1"' _

  total=$(echo "$banks" | wc -l | tr -d ' ')
  ok=0
  if [ -f "$DISCOVER_LOG_DIR/.ok" ]; then ok=$(wc -l < "$DISCOVER_LOG_DIR/.ok" | tr -d ' '); fi
  failed=$(sort "$DISCOVER_LOG_DIR/.failed" 2>/dev/null | paste -sd, - || true)
  if [ "$ok" -eq "$total" ]; then
    JOB_SUMMARY="OK $ok/$total"
    return 0
  elif [ "$ok" -gt 0 ]; then
    JOB_SUMMARY="PARTIAL $ok/$total FAILED: $failed"
    return 0
  else
    JOB_SUMMARY="FAILED 0/$total banks: $failed"
    return 1
  fi
}

run_sync() {
  if [ -z "${DISCOVER_BANKS:-}" ]; then
    echo "run-job: DISCOVER_BANKS not set — refusing an implicit all-banks sync" >&2
    return 2
  fi
  # Container-LOCAL date (TZ=Europe/Paris in prod; UTC in the test image
  # where TZ is unset): a UTC date here would make Monday-01:00-CEST
  # resolve to Sunday's dir and clobber the Sunday full-sweep logs.
  export DISCOVER_LOG_DIR="$DATA_DIR/reports/discover/$(date +%Y-%m-%d)"
  mkdir -p "$DISCOVER_LOG_DIR"   # created up front: the catalog phases tee here too.
  # Phase 1+2: shared catalogs, read once for all banks (the whole point:
  # per-bank discover no longer re-walks them — see the 2026-07-15 spec).
  # Tee'd to disk (catalogs.log) so a Dockge Update mid-run can no longer
  # erase the only copy of a catalog traceback.
  if [ "$SYNC_MODE" = "window" ]; then
    # Bound the WALK only — identity/dedup stays on stable keys.
    local y0 y1
    y0=$(date -u -d "-${SYNC_WINDOW_DAYS} days" +%Y)
    y1=$(date -u +%Y)
    python -m cb_corpus bis-sitemap --years "${y0}-${y1}" --download 2>&1 \
      | tee -a "$DISCOVER_LOG_DIR/catalogs.log" || return $?
    log "bis-sitemap OK"
    python -m cb_corpus repec --incremental --download 2>&1 \
      | tee -a "$DISCOVER_LOG_DIR/catalogs.log" || return $?
  else
    python -m cb_corpus bis-sitemap --download 2>&1 \
      | tee -a "$DISCOVER_LOG_DIR/catalogs.log" || return $?
    log "bis-sitemap OK"
    python -m cb_corpus repec --download 2>&1 \
      | tee -a "$DISCOVER_LOG_DIR/catalogs.log" || return $?
  fi
  log "catalogs OK"
  # Phase 3: native bank-site fan-out (unchanged mechanics from PR #3).
  run_discover
}

run_job() {
  case "$JOB" in
    sync)     run_sync ;;
    campaign) python -m cb_corpus "$@" ;;
  esac
}

exec 9>"$LOCK"
case "$JOB" in
  campaign)
    # a campaign waits its turn (sync or another campaign in progress);
    # log once, visibly, before blocking so an operator watching
    # nas_runs.log isn't left guessing why nothing is happening.
    flock -n 9 || { log "WAITING (lock busy)"; flock 9; } ;;
  *)
    if ! flock -n 9; then
      log "SKIPPED (lock busy)"
      echo "$(ts) SKIPPED [$JOB]" > "$STATUS"
      exit 0
    fi ;;
esac

if [ "$JOB" = "sync" ]; then
  if [ "$SYNC_MODE" = "window" ]; then
    log "START (window ${SYNC_WINDOW_DAYS}d)"
  else
    log "START (full)"
  fi
else
  log "START"
fi
if run_job "$@"; then
  log "${JOB_SUMMARY:-OK}"
  echo "$(ts) ${JOB_SUMMARY:-OK} [$JOB]" > "$STATUS"
  if [ "${AUTOCOMMIT:-1}" = "1" ]; then
    "${AUTOCOMMIT_BIN:-/app/deploy/autocommit.sh}" "$JOB" >> "$LOG" 2>&1 \
      || log "AUTOCOMMIT FAILED (local state intact, will retry on next run)"
  fi
  exit 0
else
  rc=$?
  if [ -n "$JOB_SUMMARY" ]; then log "$JOB_SUMMARY"; fi
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
