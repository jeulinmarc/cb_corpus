#!/bin/bash
# Pousse l'état versionnable (manifests + wp_dates_index) vers le repo GitHub.
# Toujours exécuté sous le lock de run-job.sh -> jamais concurrent.
set -euo pipefail

JOB="${1:-run}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
REPO_URL="${STATE_REPO_URL:-git@github.com:jeulinmarc/cb_corpus.git}"
BRANCH="${STATE_BRANCH:-master}"
KEY="${GIT_SSH_KEY:-/run/secrets/deploy_key}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Identité git via env : vaut aussi pour le rebase (pas seulement le commit),
# et évite le getpwuid() de git sous un UID sans entrée passwd.
export GIT_AUTHOR_NAME="cb-corpus-nas" GIT_AUTHOR_EMAIL="jeulinmarc@gmail.com"
export GIT_COMMITTER_NAME="cb-corpus-nas" GIT_COMMITTER_EMAIL="jeulinmarc@gmail.com"

# UID arbitraire (compose `user: PUID:PGID`) : OpenSSH exige une entrée passwd
# (getpwuid) — on en fabrique une via nss_wrapper si elle manque.
if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
  printf 'cbcorpus:x:%s:%s:cb-corpus:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" > "$TMP/passwd"
  printf 'cbcorpus:x:%s:\n' "$(id -g)" > "$TMP/group"
  wrapper=$(ls /usr/lib/*/libnss_wrapper.so 2>/dev/null | head -1)
  if [ -n "$wrapper" ]; then
    export LD_PRELOAD="$wrapper" NSS_WRAPPER_PASSWD="$TMP/passwd" NSS_WRAPPER_GROUP="$TMP/group"
  fi
fi

# Copie de la clé en 0600 : une clé montée trop ouverte (0644) serait refusée
# par ssh ; illisible par l'UID, on échoue ici avec un message clair.
if [ -f "$KEY" ]; then
  install -m 600 "$KEY" "$TMP/deploy_key" || { echo "autocommit: clé $KEY illisible par uid $(id -u)" >&2; exit 1; }
  KEY="$TMP/deploy_key"
fi

export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/known_hosts"

# --depth 1 : suffisant pour empiler un commit d'état (ignoré pour un remote
# local en test — sans incidence).
git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$TMP/repo"

mkdir -p "$TMP/repo/data/manifest"
shopt -s nullglob
manifests=("$DATA_DIR"/manifest/*.jsonl)
shopt -u nullglob
if [ "${#manifests[@]}" -gt 0 ]; then
  cp "${manifests[@]}" "$TMP/repo/data/manifest/"
fi
if [ -f "$DATA_DIR/wp_dates_index.jsonl" ]; then
  cp "$DATA_DIR/wp_dates_index.jsonl" "$TMP/repo/data/"
fi

cd "$TMP/repo"
git add data/manifest
[ -f data/wp_dates_index.jsonl ] && git add data/wp_dates_index.jsonl

if git diff --cached --quiet; then
  echo "autocommit: aucun changement d'état"
  exit 0
fi

git -c user.name="cb-corpus-nas" -c user.email="jeulinmarc@gmail.com" \
  commit -qm "data: NAS $JOB $(date -u +%Y-%m-%d)"
git pull -q --rebase origin "$BRANCH"
git push -q origin "HEAD:$BRANCH"
echo "autocommit: état poussé ($JOB)"
