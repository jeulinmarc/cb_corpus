#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

WORK=$(mktemp -d)

# Origin fixture: local bare repo with an initial state (us.jsonl + index v1).
git init -q --bare -b master "$WORK/origin.git"
SEED=$(mktemp -d)
git clone -q "$WORK/origin.git" "$SEED/clone"
mkdir -p "$SEED/clone/data/manifest"
echo '{"doc_id":"a"}' > "$SEED/clone/data/manifest/us.jsonl"
echo '{"idx":1}' > "$SEED/clone/data/wp_dates_index.jsonl"
git -C "$SEED/clone" add -A
git -C "$SEED/clone" -c user.name=t -c user.email=t@t commit -qm init
git -C "$SEED/clone" push -q origin master

# Current volume state: new fr.jsonl, modified index.
D=$(mktemp -d)
mkdir -p "$D/manifest"
echo '{"doc_id":"a"}' > "$D/manifest/us.jsonl"
echo '{"doc_id":"b"}' > "$D/manifest/fr.jsonl"
echo '{"idx":2}' > "$D/wp_dates_index.jsonl"

export CB_DATA_DIR="$D" STATE_REPO_URL="$WORK/origin.git" GIT_SSH_KEY=/dev/null

/app/deploy/autocommit.sh refresh

CHECK=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK/c"
grep -q '"doc_id":"b"' "$CHECK/c/data/manifest/fr.jsonl" || fail "fr.jsonl not pushed"
grep -q '"idx":2' "$CHECK/c/data/wp_dates_index.jsonl" || fail "index not pushed"
git -C "$CHECK/c" log -1 --pretty=%s | grep -q "^data: NAS refresh " || fail "commit message"
if git -C "$CHECK/c" log -1 --pretty=%B | grep -qi "co-authored"; then fail "co-author forbidden"; fi
git -C "$CHECK/c" log -1 --pretty=%an | grep -q "cb-corpus-nas" || fail "author"

# Idempotence: second run with no changes -> no new commit.
N1=$(git -C "$CHECK/c" rev-list --count HEAD)
/app/deploy/autocommit.sh refresh
CHECK2=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK2/c"
N2=$(git -C "$CHECK2/c" rev-list --count HEAD)
[ "$N1" = "$N2" ] || fail "empty commit created"

# Adversarial: wp_dates_index missing from the volume -> must neither crash nor delete it from the repo.
D2=$(mktemp -d)
mkdir -p "$D2/manifest"
echo '{"doc_id":"c"}' > "$D2/manifest/us.jsonl"
export CB_DATA_DIR="$D2"
/app/deploy/autocommit.sh discover
CHECK3=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK3/c"
grep -q '"doc_id":"c"' "$CHECK3/c/data/manifest/us.jsonl" || fail "us.jsonl not updated"
[ -f "$CHECK3/c/data/wp_dates_index.jsonl" ] || fail "index wrongly deleted"

# Adversarial: empty manifest folder -> clean no-op (exit 0), no crash.
D3=$(mktemp -d)
mkdir -p "$D3/manifest"
export CB_DATA_DIR="$D3"
# Baseline after all prior changes (discover call has already incremented origin)
CHECK4_BEFORE=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK4_BEFORE/c"
N_BASELINE=$(git -C "$CHECK4_BEFORE/c" rev-list --count HEAD)
OUT=$(/app/deploy/autocommit.sh refresh) || fail "empty manifest must not crash"
echo "$OUT" | grep -q "no state changes" || fail "no-op expected on empty manifest"
CHECK4=$(mktemp -d)
git clone -q "$WORK/origin.git" "$CHECK4/c"
N3=$(git -C "$CHECK4/c" rev-list --count HEAD)
[ "$N_BASELINE" = "$N3" ] || fail "commit created on empty manifest"

echo "AUTOCOMMIT_OK"
