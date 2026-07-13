#!/bin/bash
# Runner host : builde l'image puis exécute chaque test dedans.
# deploy/ et tests/ sont montés par-dessus l'image pour itérer sans rebuild.
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
