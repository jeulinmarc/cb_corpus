#!/bin/bash
# Host runner: builds the image then runs each test inside it.
# deploy/ and tests/ are mounted over the image so we can iterate without rebuilding.
set -euo pipefail
cd "$(dirname "$0")/../.."
docker build -f deploy/Dockerfile -t cb_corpus:test .
for t in tests/deploy/test_*.sh; do
  echo "=== $t ==="
  docker run --rm \
    -v "$PWD/deploy:/app/deploy:ro" \
    -v "$PWD/tests/deploy:/app/tests/deploy:ro" \
    cb_corpus:test bash "/app/$t"
done
echo "ALL_DEPLOY_TESTS_OK"
