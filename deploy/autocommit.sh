#!/bin/bash
# Pushes the versionable state (manifests + wp_dates_index) to the GitHub repo.
# Always run under run-job.sh's lock -> never concurrent.
set -euo pipefail

JOB="${1:-run}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
REPO_URL="${STATE_REPO_URL:-git@github.com:jeulinmarc/cb_corpus.git}"
BRANCH="${STATE_BRANCH:-master}"
KEY="${GIT_SSH_KEY:-/run/secrets/deploy_key}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Git identity via env: also applies to the rebase (not just the commit),
# and avoids git's getpwuid() under a UID without a passwd entry.
export GIT_AUTHOR_NAME="cb-corpus-nas" GIT_AUTHOR_EMAIL="jeulinmarc@gmail.com"
export GIT_COMMITTER_NAME="cb-corpus-nas" GIT_COMMITTER_EMAIL="jeulinmarc@gmail.com"

# Arbitrary UID (compose `user: PUID:PGID`): OpenSSH requires a passwd entry
# (getpwuid) — we fabricate one via nss_wrapper if it's missing.
if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
  printf 'cbcorpus:x:%s:%s:cb-corpus:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" > "$TMP/passwd"
  printf 'cbcorpus:x:%s:\n' "$(id -g)" > "$TMP/group"
  wrapper=$(ls /usr/lib/*/libnss_wrapper.so 2>/dev/null | head -1 || true)
  if [ -n "$wrapper" ]; then
    export LD_PRELOAD="$wrapper" NSS_WRAPPER_PASSWD="$TMP/passwd" NSS_WRAPPER_GROUP="$TMP/group"
  fi
fi

# Copy the key as 0600: a key mounted too openly (0644) would be refused
# by ssh; if unreadable by the UID, we fail here with a clear message.
if [ -f "$KEY" ]; then
  install -m 600 "$KEY" "$TMP/deploy_key" || { echo "autocommit: key $KEY unreadable by uid $(id -u)" >&2; exit 1; }
  KEY="$TMP/deploy_key"
fi

export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/known_hosts"

# --depth 1: sufficient to stack a state commit (ignored for a local test
# remote — no effect).
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

# Refuse to push a torn/corrupt manifest (audit H1): validate every COPIED
# manifest/*.jsonl with the container's python before staging anything. A
# SIGKILL/ENOSPC mid-write can leave a malformed final line on the volume;
# cb_corpus.storage repairs that in-place on the NEXT local run, but until it
# does, this push must not ship the corrupt file to the public repo.
shopt -s nullglob
copied_manifests=("$TMP/repo/data/manifest"/*.jsonl)
shopt -u nullglob
for mf in "${copied_manifests[@]}"; do
  if ! python -c '
import json, sys
with open(sys.argv[1], "rb") as fh:
    for line in fh:
        s = line.strip()
        if s:
            json.loads(s)
' "$mf" >/dev/null 2>&1; then
    echo "autocommit: REFUSED (malformed manifest: $mf)" >&2
    exit 1
  fi
done

cd "$TMP/repo"
git add data/manifest
[ -f data/wp_dates_index.jsonl ] && git add data/wp_dates_index.jsonl

if git diff --cached --quiet; then
  echo "autocommit: no state changes"
  exit 0
fi

git -c user.name="cb-corpus-nas" -c user.email="jeulinmarc@gmail.com" \
  commit -qm "data: NAS $JOB $(date -u +%Y-%m-%d)"
git pull -q --rebase origin "$BRANCH"
git push -q origin "HEAD:$BRANCH"
echo "autocommit: state pushed ($JOB)"
