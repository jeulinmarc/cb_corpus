#!/bin/bash
# Reproduit l'environnement NAS : UID arbitraire sans entrée passwd.
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

if [ "$(id -u)" = "0" ] && [ "${1:-}" != "inner" ]; then
  exec setpriv --reuid 12345 --regid 12345 --clear-groups bash "$0" inner
fi
[ "${1:-}" = "inner" ] || fail "doit démarrer root puis dropper"
getent passwd 12345 >/dev/null && fail "l'UID 12345 ne devrait pas exister dans passwd"

export HOME=/tmp

# 1) autocommit complet sous UID arbitraire (remote local : couvre git+identité+HOME)
WORK=$(mktemp -d)
git init -q --bare -b master "$WORK/origin.git"
SEED=$(mktemp -d)
git clone -q "$WORK/origin.git" "$SEED/c"
mkdir -p "$SEED/c/data/manifest"
echo '{"doc_id":"a"}' > "$SEED/c/data/manifest/us.jsonl"
git -C "$SEED/c" add -A
git -C "$SEED/c" -c user.name=t -c user.email=t@t commit -qm init
git -C "$SEED/c" push -q origin master
D=$(mktemp -d)
mkdir -p "$D/manifest"
echo '{"doc_id":"z"}' > "$D/manifest/us.jsonl"
export CB_DATA_DIR="$D" STATE_REPO_URL="$WORK/origin.git" GIT_SSH_KEY=/dev/null
/app/deploy/autocommit.sh refresh || fail "autocommit sous UID arbitraire"
CHECK=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK/c"
grep -q '"doc_id":"z"' "$CHECK/c/data/manifest/us.jsonl" || fail "état non poussé sous UID arbitraire"

# 2) ssh doit être utilisable avec l'env nss_wrapper qu'autocommit fabrique
wrapper=$(ls /usr/lib/*/libnss_wrapper.so | head -1)
[ -n "$wrapper" ] || fail "libnss_wrapper.so absent de l'image"
NSSD=$(mktemp -d)
printf 'cbcorpus:x:%s:%s:cb:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" > "$NSSD/passwd"
printf 'cbcorpus:x:%s:\n' "$(id -g)" > "$NSSD/group"
LD_PRELOAD="$wrapper" NSS_WRAPPER_PASSWD="$NSSD/passwd" NSS_WRAPPER_GROUP="$NSSD/group" ssh -V \
  || fail "ssh inutilisable même avec nss_wrapper"
if ssh -V 2>/dev/null; then fail "ssh aurait dû échouer sans nss_wrapper (le test ne prouve plus rien)"; fi

echo "UID_OK"
