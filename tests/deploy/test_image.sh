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
# `list-banks` output with an awk program to drop the blank line + "N banks"
# footer. Against the LIVE 63-bank registry (not a hand-rolled mirror
# fixture) that filter must drop EXACTLY the footer, nothing else — i.e. it
# must count the same banks as a simple "line starts with a lowercase
# letter" grep. The awk program is EXTRACTED from the real run-job.sh (not
# retyped here) so an edit to the production filter is what this assertion
# actually exercises -- a hand-retyped copy would keep passing even if
# resolve_banks() drifted, since it would just be comparing itself to itself.
FILTER_LINE=$(grep "list-banks | awk" /app/deploy/run-job.sh | head -1)
[ -n "$FILTER_LINE" ] || { echo "FAIL: could not find the list-banks | awk line in run-job.sh resolve_banks()"; exit 1; }
FILTER=$(printf '%s' "$FILTER_LINE" | grep -oE "awk '[^']+'")
[ -n "$FILTER" ] || { echo "FAIL: could not extract awk filter from run-job.sh resolve_banks()"; exit 1; }
AWK_COUNT=$(python -m cb_corpus list-banks | eval "$FILTER" | wc -l | tr -d ' ')
GREP_COUNT=$(python -m cb_corpus list-banks | grep -c '^[a-z]' || true)
[ -n "$AWK_COUNT" ] && [ "$AWK_COUNT" -gt 0 ] || { echo "FAIL: registry-vs-filter awk count empty/zero (filter: $FILTER)"; exit 1; }
[ "$AWK_COUNT" = "$GREP_COUNT" ] \
  || { echo "FAIL: registry-vs-filter mismatch (awk=$AWK_COUNT grep=$GREP_COUNT, filter: $FILTER)"; exit 1; }

echo "IMAGE_OK"
