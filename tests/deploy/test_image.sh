#!/bin/bash
# Smoke test — runs INSIDE the image (via run_tests.sh).
set -euo pipefail
command -v chromium >/dev/null || { echo "FAIL: chromium missing"; exit 1; }
command -v supercronic >/dev/null || { echo "FAIL: supercronic missing"; exit 1; }
command -v flock >/dev/null || { echo "FAIL: flock missing"; exit 1; }
command -v git >/dev/null || { echo "FAIL: git missing"; exit 1; }
command -v ssh >/dev/null || { echo "FAIL: ssh missing"; exit 1; }
command -v rsync >/dev/null || { echo "FAIL: rsync missing"; exit 1; }
python -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version" \
  || { echo "FAIL: python != 3.13"; exit 1; }
python -m cb_corpus list-banks | grep -q "ecb" || { echo "FAIL: list-banks"; exit 1; }
[ "$HOME" = "/tmp" ] || { echo "FAIL: HOME != /tmp"; exit 1; }
bash -n /app/deploy/entrypoint.sh || { echo "FAIL: entrypoint syntax"; exit 1; }

# Registry-vs-filter: run-job.sh's resolve_banks() (DISCOVER_BANKS=all) filters
# `list-banks` output with `awk 'NF && $1 ~ /^[a-z]{2,4}$/'` to drop the blank
# line + "N banks" footer. Against the LIVE 63-bank registry (not a hand-rolled
# mirror fixture) that filter must drop EXACTLY the footer, nothing else — i.e.
# it must count the same banks as a simple "line starts with a lowercase
# letter" grep. A regression here (e.g. a bank code that doesn't match
# [a-z]{2,4}, or a footer that slips through) would silently shrink or inflate
# the "all banks" sync fan-out.
AWK_COUNT=$(python -m cb_corpus list-banks | awk 'NF && $1 ~ /^[a-z]{2,4}$/' | wc -l | tr -d ' ')
GREP_COUNT=$(python -m cb_corpus list-banks | grep -c '^[a-z]' || true)
[ -n "$AWK_COUNT" ] && [ "$AWK_COUNT" -gt 0 ] || { echo "FAIL: registry-vs-filter awk count empty/zero"; exit 1; }
[ "$AWK_COUNT" = "$GREP_COUNT" ] \
  || { echo "FAIL: registry-vs-filter mismatch (awk=$AWK_COUNT grep=$GREP_COUNT)"; exit 1; }

echo "IMAGE_OK"
