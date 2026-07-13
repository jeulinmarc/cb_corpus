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
echo "IMAGE_OK"
