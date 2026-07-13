#!/bin/bash
# Wrapper de job : verrou global + exécution + journal + auto-commit d'état.
# Usage : run-job.sh refresh | discover | campaign <sous-commande cb_corpus...>
set -uo pipefail

JOB="${1:-}"; shift || true
APP_DIR="${CB_APP_DIR:-/app}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
LOCK="$DATA_DIR/.cb.lock"
LOG="$DATA_DIR/reports/nas_runs.log"
STATUS="$DATA_DIR/reports/last_run_status"

mkdir -p "$DATA_DIR/reports"
cd "$APP_DIR"   # le crawler écrit dans ./data (relatif)

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) [$JOB] $*" >> "$LOG"; }

# Garde anti-écrasement : un volume sans manifests = seed manquant ou chemin
# de dataset erroné. On refuse de crawler (et donc de committer un état
# partiel par-dessus le vrai). CB_ALLOW_EMPTY_DATA=1 pour un bootstrap voulu.
if [ "${CB_ALLOW_EMPTY_DATA:-0}" != "1" ]; then
  if ! ls "$DATA_DIR"/manifest/*.jsonl >/dev/null 2>&1; then
    log "REFUSED (volume sans manifests — seed manquant ? CB_ALLOW_EMPTY_DATA=1 pour forcer)"
    echo "$(ts) REFUSED [$JOB]" > "$STATUS"
    exit 3
  fi
fi

case "$JOB" in
  refresh|discover|campaign) ;;
  *) echo "run-job: job inconnu '$JOB'" >&2; exit 2 ;;
esac

run_job() {
  case "$JOB" in
    refresh)
      python -m cb_corpus bis-sitemap --download \
        && python -m cb_corpus repec --download ;;
    discover)
      if [ -z "${DISCOVER_ARGS:-}" ]; then
        echo "run-job: DISCOVER_ARGS non défini — refus d'un discover complet A-F implicite" >&2
        return 2
      fi
      # DISCOVER_ARGS est volontairement splitté (liste d'options).
      # shellcheck disable=SC2086
      python -m cb_corpus discover ${DISCOVER_ARGS:-} --download ;;
    campaign)
      python -m cb_corpus "$@" ;;
  esac
}

exec 9>"$LOCK"
if [ "$JOB" = "campaign" ]; then
  flock 9   # une campagne attend son tour (refresh en cours, etc.)
else
  if ! flock -n 9; then
    log "SKIPPED (lock occupé)"
    exit 0
  fi
fi

log "START"
if run_job "$@"; then
  log "OK"
  echo "$(ts) OK [$JOB]" > "$STATUS"
  if [ "${AUTOCOMMIT:-1}" = "1" ]; then
    "${AUTOCOMMIT_BIN:-/app/deploy/autocommit.sh}" "$JOB" >> "$LOG" 2>&1 \
      || log "AUTOCOMMIT FAILED (état local intact, retentera au prochain run)"
  fi
  exit 0
else
  rc=$?
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
