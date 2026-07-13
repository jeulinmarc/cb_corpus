#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

WORK=$(mktemp -d)

# Fixture origin : bare repo local avec un état initial (us.jsonl + index v1).
git init -q --bare -b master "$WORK/origin.git"
SEED=$(mktemp -d)
git clone -q "$WORK/origin.git" "$SEED/clone"
mkdir -p "$SEED/clone/data/manifest"
echo '{"doc_id":"a"}' > "$SEED/clone/data/manifest/us.jsonl"
echo '{"idx":1}' > "$SEED/clone/data/wp_dates_index.jsonl"
git -C "$SEED/clone" add -A
git -C "$SEED/clone" -c user.name=t -c user.email=t@t commit -qm init
git -C "$SEED/clone" push -q origin master

# État courant du volume : fr.jsonl nouveau, index modifié.
D=$(mktemp -d)
mkdir -p "$D/manifest"
echo '{"doc_id":"a"}' > "$D/manifest/us.jsonl"
echo '{"doc_id":"b"}' > "$D/manifest/fr.jsonl"
echo '{"idx":2}' > "$D/wp_dates_index.jsonl"

export CB_DATA_DIR="$D" STATE_REPO_URL="$WORK/origin.git" GIT_SSH_KEY=/dev/null

/app/deploy/autocommit.sh refresh

CHECK=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK/c"
grep -q '"doc_id":"b"' "$CHECK/c/data/manifest/fr.jsonl" || fail "fr.jsonl non poussé"
grep -q '"idx":2' "$CHECK/c/data/wp_dates_index.jsonl" || fail "index non poussé"
git -C "$CHECK/c" log -1 --pretty=%s | grep -q "^data: NAS refresh " || fail "message de commit"
if git -C "$CHECK/c" log -1 --pretty=%B | grep -qi "co-authored"; then fail "co-author interdit"; fi
git -C "$CHECK/c" log -1 --pretty=%an | grep -q "cb-corpus-nas" || fail "auteur"

# Idempotence : second run sans changement -> aucun nouveau commit.
N1=$(git -C "$CHECK/c" rev-list --count HEAD)
/app/deploy/autocommit.sh refresh
CHECK2=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK2/c"
N2=$(git -C "$CHECK2/c" rev-list --count HEAD)
[ "$N1" = "$N2" ] || fail "commit vide créé"

# Adversarial : wp_dates_index absent du volume -> ne doit ni crasher ni le supprimer du repo.
D2=$(mktemp -d)
mkdir -p "$D2/manifest"
echo '{"doc_id":"c"}' > "$D2/manifest/us.jsonl"
export CB_DATA_DIR="$D2"
/app/deploy/autocommit.sh discover
CHECK3=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK3/c"
grep -q '"doc_id":"c"' "$CHECK3/c/data/manifest/us.jsonl" || fail "us.jsonl non mis à jour"
[ -f "$CHECK3/c/data/wp_dates_index.jsonl" ] || fail "index supprimé à tort"

# Adversarial : dossier manifest vide -> no-op propre (exit 0), pas de crash.
D3=$(mktemp -d)
mkdir -p "$D3/manifest"
export CB_DATA_DIR="$D3"
# Baseline after all prior changes (discover call has already incremented origin)
CHECK4_BEFORE=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK4_BEFORE/c"
N_BASELINE=$(git -C "$CHECK4_BEFORE/c" rev-list --count HEAD)
OUT=$(/app/deploy/autocommit.sh refresh) || fail "manifest vide ne doit pas crasher"
echo "$OUT" | grep -q "aucun changement" || fail "no-op attendu sur manifest vide"
CHECK4=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK4/c"
N3=$(git -C "$CHECK4/c" rev-list --count HEAD)
[ "$N_BASELINE" = "$N3" ] || fail "commit créé sur manifest vide"

echo "AUTOCOMMIT_OK"
