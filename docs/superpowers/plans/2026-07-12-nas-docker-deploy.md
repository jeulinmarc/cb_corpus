# NAS Docker Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Packager cb_corpus en image Docker (GHCR) et l'exploiter sur le NAS via deux stacks Dockge — refresh planifié 12 h + campagnes à la demande — avec verrou global, auto-commit de l'état et runbook de déploiement.

**Architecture:** Une image unique `python:3.13-slim` + Chromium + supercronic. Un wrapper `run-job.sh` (flock global, log de runs, déclenchement d'`autocommit.sh` qui pousse les manifests vers GitHub via deploy key). Deux fichiers compose d'exemple à placeholders (l'infra réelle ne va JAMAIS dans le repo). CI GitHub Actions → `ghcr.io/jeulinmarc/cb_corpus`.

**Tech Stack:** Docker, docker compose (Dockge), supercronic, bash, GitHub Actions, GHCR.

**Spec:** `docs/superpowers/specs/2026-07-12-nas-docker-deploy-design.md`

## Global Constraints

- Python **3.13** exactement (base `python:3.13-slim`) ; ne pas toucher au code du crawler.
- **AUCUNE valeur d'infra réelle** (IP, hostname, port, chemin `/mnt/...` réel, UID) dans un fichier commité — placeholders `POOL/DATASET/PUID/PGID` uniquement. Un hook pre-commit local bloque les valeurs connues ; ne jamais le contourner (`--no-verify` interdit).
- Commits **sans co-author Claude** ; identité des commits d'état NAS : `cb-corpus-nas <jeulinmarc@gmail.com>`.
- Image NAS : `linux/amd64` (buildée par CI) ; les builds/tests locaux sur le Mac (arm64) se font en arch native — même Dockerfile.
- La CI **ignore `data/**`** en trigger (sinon chaque auto-commit d'état du NAS relancerait un build).
- YAGNI explicite (hors scope) : fast-path 5–10 min, healthchecks.io, montage CIFS dans le conteneur.
- Tests shell : ils tournent **dans l'image buildée** (macOS n'a pas `flock`) via `tests/deploy/run_tests.sh`. Docker Desktop doit tourner (`open -a Docker`).
- Répertoire de travail dans le conteneur : `/app` (le code utilise `./data` relatif) ; volume monté sur `/app/data` ; `HOME=/tmp` (conteneur lancé sous UID arbitraire non présent dans /etc/passwd).

## File Structure

```
.dockerignore                          (nouveau — exclut data/ 8,2 GB du contexte de build)
deploy/
  Dockerfile                           (image unique)
  entrypoint.sh                        (stack refresh : exec supercronic)
  crontab                              (12h refresh + discover hebdo)
  run-job.sh                           (lock + exécution + log + autocommit)
  autocommit.sh                        (manifests volume → clone → commit → push)
  compose.refresh.example.yml          (placeholders)
  compose.campaign.example.yml         (placeholders)
  compose.discover-ids.example.yml     (stack jetable UID/chemins)
  README.md                            (runbook : deploy key, seed SMB, Dockge, vérifs)
.github/workflows/docker-image.yml     (build+push GHCR)
tests/deploy/
  run_tests.sh                         (runner host : build image + exécute les tests dedans)
  test_image.sh                        (smoke image : binaires + list-banks)
  test_run_job.sh                      (lock/skip/wait/args/status — stub python)
  test_autocommit.sh                   (fixture bare repo local — push/idempotence/no co-author)
```

---

### Task 1: Image Docker (Dockerfile, entrypoint, crontab) + smoke test

**Files:**
- Create: `.dockerignore`, `deploy/Dockerfile`, `deploy/entrypoint.sh`, `deploy/crontab`, `tests/deploy/run_tests.sh`, `tests/deploy/test_image.sh`

**Interfaces:**
- Produces: image locale `cb_corpus:test` ; binaires `chromium`, `supercronic`, `flock`, `git`, `ssh`, `rsync` dans le PATH ; `WORKDIR /app` ; `ENV HOME=/tmp` ; CMD = `/app/deploy/entrypoint.sh`. Le runner `tests/deploy/run_tests.sh` (re)builde l'image puis lance tous les `test_*.sh` dedans en montant `deploy/` et `tests/` (pas de rebuild entre itérations de scripts).

- [ ] **Step 1: Démarrer Docker Desktop**

Run: `open -a Docker && until docker info >/dev/null 2>&1; do sleep 2; done; docker info --format '{{.ServerVersion}}'`
Expected: une version de serveur s'affiche. (Si Docker Desktop est indisponible, STOP : signaler à Marc — fallback = build via CI et tests sur le NAS.)

- [ ] **Step 2: Récupérer la version + le sha256 de supercronic (ne JAMAIS inventer un hash)**

Run:
```bash
TAG=$(curl -fsSL https://api.github.com/repos/aptible/supercronic/releases/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])')
echo "TAG=$TAG"
curl -fsSL "https://github.com/aptible/supercronic/releases/download/${TAG}/supercronic-linux-amd64" | shasum -a 256
curl -fsSL "https://github.com/aptible/supercronic/releases/download/${TAG}/supercronic-linux-arm64" | shasum -a 256
```
Expected: un tag (ex. `v0.2.34`) et deux hash. Reporter les trois valeurs dans le Dockerfile du Step 4 (`SUPERCRONIC_VERSION`, `SUPERCRONIC_SHA256_AMD64`, `SUPERCRONIC_SHA256_ARM64`).

- [ ] **Step 3: Écrire le smoke test (échoue tant que l'image n'existe pas)**

`tests/deploy/test_image.sh` :
```bash
#!/bin/bash
# Smoke test — s'exécute DANS l'image (via run_tests.sh).
set -euo pipefail
command -v chromium >/dev/null || { echo "FAIL: chromium absent"; exit 1; }
command -v supercronic >/dev/null || { echo "FAIL: supercronic absent"; exit 1; }
command -v flock >/dev/null || { echo "FAIL: flock absent"; exit 1; }
command -v git >/dev/null || { echo "FAIL: git absent"; exit 1; }
command -v ssh >/dev/null || { echo "FAIL: ssh absent"; exit 1; }
command -v rsync >/dev/null || { echo "FAIL: rsync absent"; exit 1; }
python -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version" \
  || { echo "FAIL: python != 3.13"; exit 1; }
python -m cb_corpus list-banks | grep -q "ecb" || { echo "FAIL: list-banks"; exit 1; }
[ "$HOME" = "/tmp" ] || { echo "FAIL: HOME != /tmp"; exit 1; }
bash -n /app/deploy/entrypoint.sh || { echo "FAIL: entrypoint syntaxe"; exit 1; }
echo "IMAGE_OK"
```

`tests/deploy/run_tests.sh` :
```bash
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
```

Run: `chmod +x tests/deploy/*.sh && bash tests/deploy/run_tests.sh`
Expected: FAIL — `deploy/Dockerfile` n'existe pas encore.

- [ ] **Step 4: Écrire `.dockerignore` puis le Dockerfile**

`.dockerignore` (CRITIQUE : sans lui, le contexte de build embarque les 8,2 GB de `data/`) :
```
data
.git
.github
docs
tests
*.md
__pycache__
*.pyc
.venv
venv
```

`deploy/Dockerfile` (remplacer les trois valeurs supercronic par celles du Step 2) :
```dockerfile
FROM python:3.13-slim

ARG SUPERCRONIC_VERSION=v0.2.34
ARG SUPERCRONIC_SHA256_AMD64=REMPLACER_PAR_HASH_STEP2
ARG SUPERCRONIC_SHA256_ARM64=REMPLACER_PAR_HASH_STEP2

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium git openssh-client rsync curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then SHA="$SUPERCRONIC_SHA256_AMD64"; \
       else SHA="$SUPERCRONIC_SHA256_ARM64"; fi \
    && curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${ARCH}" \
    && echo "${SHA}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY cb_corpus /app/cb_corpus
COPY deploy /app/deploy
RUN chmod +x /app/deploy/*.sh || true

# HOME=/tmp : le conteneur tourne sous un UID arbitraire (compose `user:`)
# absent de /etc/passwd ; ssh/git/chromium ont besoin d'un HOME inscriptible.
ENV PYTHONUNBUFFERED=1 HOME=/tmp

CMD ["/app/deploy/entrypoint.sh"]
```

`deploy/entrypoint.sh` :
```bash
#!/bin/bash
# Stack cb-refresh : supercronic pilote les jobs planifiés (crontab).
set -euo pipefail
exec supercronic -passthrough-logs /app/deploy/crontab
```

`deploy/crontab` :
```
# refresh (bis-sitemap + repec) toutes les 12 h ; discover hebdo dimanche 03:00.
# Cadence = paramètre : éditer ici (l'image doit être rebuildée) ou surcharger
# la commande du service dans Dockge pour pointer un crontab monté en volume.
0 */12 * * * /app/deploy/run-job.sh refresh
0 3 * * 0 /app/deploy/run-job.sh discover
```

- [ ] **Step 5: Builder et vérifier le smoke test**

Run: `chmod +x deploy/entrypoint.sh && bash tests/deploy/run_tests.sh`
Expected: build OK puis `IMAGE_OK` et `ALL_DEPLOY_TESTS_OK` (`test_image.sh` ne vérifie que les binaires et l'entrypoint ; `run-job.sh` arrive en Task 2).

- [ ] **Step 6: Commit**

```bash
git add .dockerignore deploy/Dockerfile deploy/entrypoint.sh deploy/crontab tests/deploy/run_tests.sh tests/deploy/test_image.sh
git commit -m "feat(deploy): Docker image (python 3.13 + chromium + supercronic) + smoke test"
```

---

### Task 2: `run-job.sh` — lock global, exécution, log de runs

**Files:**
- Create: `deploy/run-job.sh`, `tests/deploy/test_run_job.sh`

**Interfaces:**
- Consumes: image Task 1 (flock, bash) ; `python -m cb_corpus <cmd>` (CLI existante).
- Produces: `run-job.sh <refresh|discover|campaign> [args...]`. Env : `CB_DATA_DIR` (déf. `/app/data`), `CB_APP_DIR` (déf. `/app`), `DISCOVER_ARGS` (args du discover hebdo), `AUTOCOMMIT` (déf. `1`), `AUTOCOMMIT_BIN` (déf. `/app/deploy/autocommit.sh` — surchargeable pour les tests). Lock : `$CB_DATA_DIR/.cb.lock` — refresh/discover **skippent** si occupé (`flock -n`, exit 0, ligne `SKIPPED` loguée) ; campaign **attend** (flock bloquant). Logs : append `$CB_DATA_DIR/reports/nas_runs.log`, écrase `$CB_DATA_DIR/reports/last_run_status`.

- [ ] **Step 1: Écrire le test (échoue : run-job.sh absent)**

`tests/deploy/test_run_job.sh` :
```bash
#!/bin/bash
set -euo pipefail
fail() { echo "FAIL: $1" >&2; exit 1; }

# Stub `python` : trace les args, code retour pilotable par PY_EXIT.
STUB=$(mktemp -d)
cat > "$STUB/python" <<'EOF'
#!/bin/bash
echo "PYARGS:$*" >> "$PY_LOG"
exit "${PY_EXIT:-0}"
EOF
chmod +x "$STUB/python"
export PATH="$STUB:$PATH"
export CB_APP_DIR=/app

newdir() { D=$(mktemp -d); export CB_DATA_DIR="$D" PY_LOG="$D/py.log"; }

# T1 — refresh succès : bis-sitemap puis repec, log OK, status OK.
newdir; export AUTOCOMMIT=0
/app/deploy/run-job.sh refresh
grep -q "PYARGS:-m cb_corpus bis-sitemap --download" "$PY_LOG" || fail "bis-sitemap non appelé"
grep -q "PYARGS:-m cb_corpus repec --download" "$PY_LOG" || fail "repec non appelé"
grep -q "\[refresh\] OK" "$D/reports/nas_runs.log" || fail "log OK absent"
grep -q "OK \[refresh\]" "$D/reports/last_run_status" || fail "status != OK"

# T2 — refresh échec : status FAILED, exit non nul.
newdir; export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh aurait dû échouer"; fi
grep -q "FAILED \[refresh\]" "$D/reports/last_run_status" || fail "status != FAILED"
unset PY_EXIT

# T3 — lock occupé : refresh skippe (exit 0), python jamais appelé.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 3 ) &
HOLDER=$!
sleep 0.5
/app/deploy/run-job.sh refresh || fail "skip doit sortir en 0"
grep -q "\[refresh\] SKIPPED" "$D/reports/nas_runs.log" || fail "SKIPPED non logué"
if [ -f "$PY_LOG" ] && grep -q PYARGS "$PY_LOG"; then fail "python ne devait pas tourner"; fi
wait "$HOLDER"

# T4 — campaign attend le lock puis exécute avec ses args.
newdir
( exec 9>"$D/.cb.lock"; flock 9; sleep 2 ) &
HOLDER=$!
sleep 0.5
START=$(date +%s)
/app/deploy/run-job.sh campaign discover --banks fr --types A3 --download
END=$(date +%s)
[ $((END - START)) -ge 1 ] || fail "campaign n'a pas attendu le lock"
grep -q "PYARGS:-m cb_corpus discover --banks fr --types A3 --download" "$PY_LOG" \
  || fail "args campaign incorrects"
wait "$HOLDER"

# T5 — discover consomme DISCOVER_ARGS.
newdir; export DISCOVER_ARGS="--banks us --types A3 --rounds 1"
/app/deploy/run-job.sh discover
grep -q "PYARGS:-m cb_corpus discover --banks us --types A3 --rounds 1 --download" "$PY_LOG" \
  || fail "DISCOVER_ARGS non transmis"
unset DISCOVER_ARGS

# T6 — autocommit appelé après succès (AUTOCOMMIT=1), pas après échec.
newdir; export AUTOCOMMIT=1 AC_LOG="$D/ac.log"
cat > "$D/ac.sh" <<'EOF'
#!/bin/bash
echo "AC:$1" >> "$AC_LOG"
EOF
chmod +x "$D/ac.sh"; export AUTOCOMMIT_BIN="$D/ac.sh"
/app/deploy/run-job.sh refresh
grep -q "AC:refresh" "$AC_LOG" || fail "autocommit non appelé après succès"
export PY_EXIT=1
if /app/deploy/run-job.sh refresh; then fail "refresh aurait dû échouer"; fi
[ "$(grep -c "AC:" "$AC_LOG")" = "1" ] || fail "autocommit appelé après échec"
unset PY_EXIT AUTOCOMMIT_BIN

# T7 — job inconnu : erreur.
newdir
if /app/deploy/run-job.sh bogus; then fail "job inconnu accepté"; fi

echo "RUN_JOB_OK"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `chmod +x tests/deploy/test_run_job.sh && bash tests/deploy/run_tests.sh`
Expected: `test_image.sh` OK puis FAIL sur `test_run_job.sh` (`run-job.sh: No such file`).

- [ ] **Step 3: Écrire `deploy/run-job.sh`**

```bash
#!/bin/bash
# Wrapper de job : verrou global + exécution + journal + auto-commit d'état.
# Usage : run-job.sh refresh | discover | campaign <sous-commande cb_corpus...>
set -uo pipefail

JOB="${1:-}"; shift || true
APP_DIR="${CB_APP_DIR:-/app}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
LOCK="$DATA_DIR/.cb.lock"
LOG="$DATA_DIR/reports/nas_runs.log"
STATUS="$DATA_DIR/reports/last_run_status"

mkdir -p "$DATA_DIR/reports"
cd "$APP_DIR"   # le crawler écrit dans ./data (relatif)

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) [$JOB] $*" >> "$LOG"; }

case "$JOB" in
  refresh|discover|campaign) ;;
  *) echo "run-job: job inconnu '$JOB'" >&2; exit 2 ;;
esac

run_job() {
  case "$JOB" in
    refresh)
      python -m cb_corpus bis-sitemap --download \
        && python -m cb_corpus repec --download ;;
    discover)
      # DISCOVER_ARGS est volontairement splitté (liste d'options).
      # shellcheck disable=SC2086
      python -m cb_corpus discover ${DISCOVER_ARGS:-} --download ;;
    campaign)
      python -m cb_corpus "$@" ;;
  esac
}

exec 9>"$LOCK"
if [ "$JOB" = "campaign" ]; then
  flock 9   # une campagne attend son tour (refresh en cours, etc.)
else
  if ! flock -n 9; then
    log "SKIPPED (lock occupé)"
    exit 0
  fi
fi

log "START"
if run_job "$@"; then
  log "OK"
  echo "$(ts) OK [$JOB]" > "$STATUS"
  if [ "${AUTOCOMMIT:-1}" = "1" ]; then
    "${AUTOCOMMIT_BIN:-/app/deploy/autocommit.sh}" "$JOB" >> "$LOG" 2>&1 \
      || log "AUTOCOMMIT FAILED (état local intact, retentera au prochain run)"
  fi
  exit 0
else
  rc=$?
  log "FAILED rc=$rc"
  echo "$(ts) FAILED [$JOB] rc=$rc" > "$STATUS"
  exit "$rc"
fi
```

- [ ] **Step 4: Vérifier le passage**

Run: `chmod +x deploy/run-job.sh && bash tests/deploy/run_tests.sh`
Expected: `RUN_JOB_OK` puis `ALL_DEPLOY_TESTS_OK`.

- [ ] **Step 5: Commit**

```bash
git add deploy/run-job.sh tests/deploy/test_run_job.sh
git commit -m "feat(deploy): run-job.sh — flock global, journal de runs, hook autocommit"
```

---

### Task 3: `autocommit.sh` — état volume → GitHub

**Files:**
- Create: `deploy/autocommit.sh`, `tests/deploy/test_autocommit.sh`

**Interfaces:**
- Consumes: `CB_DATA_DIR` (manifests + `wp_dates_index.jsonl`) ; env `STATE_REPO_URL` (déf. `git@github.com:jeulinmarc/cb_corpus.git`), `GIT_SSH_KEY` (déf. `/run/secrets/deploy_key`), `STATE_BRANCH` (déf. `master`).
- Produces: `autocommit.sh <job>` — clone jetable, copie l'état, commit `data: NAS <job> <date UTC>` (identité `cb-corpus-nas`, **aucun co-author**), `pull --rebase` + push. **No-op silencieux si aucun changement.** Toujours appelé sous le lock de run-job (jamais concurrent).

- [ ] **Step 1: Écrire le test (échoue : autocommit.sh absent)**

`tests/deploy/test_autocommit.sh` :
```bash
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

echo "AUTOCOMMIT_OK"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `chmod +x tests/deploy/test_autocommit.sh && bash tests/deploy/run_tests.sh`
Expected: FAIL sur `test_autocommit.sh` (`autocommit.sh: No such file`).

- [ ] **Step 3: Écrire `deploy/autocommit.sh`**

```bash
#!/bin/bash
# Pousse l'état versionnable (manifests + wp_dates_index) vers le repo GitHub.
# Toujours exécuté sous le lock de run-job.sh -> jamais concurrent.
set -euo pipefail

JOB="${1:-run}"
DATA_DIR="${CB_DATA_DIR:-/app/data}"
REPO_URL="${STATE_REPO_URL:-git@github.com:jeulinmarc/cb_corpus.git}"
BRANCH="${STATE_BRANCH:-master}"
KEY="${GIT_SSH_KEY:-/run/secrets/deploy_key}"

export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/known_hosts"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# --depth 1 : suffisant pour empiler un commit d'état (ignoré pour un remote
# local en test — sans incidence).
git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$TMP/repo"

mkdir -p "$TMP/repo/data/manifest"
cp "$DATA_DIR"/manifest/*.jsonl "$TMP/repo/data/manifest/"
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
```

- [ ] **Step 4: Vérifier le passage**

Run: `chmod +x deploy/autocommit.sh && bash tests/deploy/run_tests.sh`
Expected: `AUTOCOMMIT_OK` puis `ALL_DEPLOY_TESTS_OK`.

- [ ] **Step 5: Commit**

```bash
git add deploy/autocommit.sh tests/deploy/test_autocommit.sh
git commit -m "feat(deploy): autocommit.sh — push de l'état manifests via deploy key"
```

---

### Task 4: CI GHCR + composes d'exemple + runbook

**Files:**
- Create: `.github/workflows/docker-image.yml`, `deploy/compose.refresh.example.yml`, `deploy/compose.campaign.example.yml`, `deploy/compose.discover-ids.example.yml`, `deploy/README.md`

**Interfaces:**
- Consumes: image Task 1 (le workflow builde `deploy/Dockerfile`).
- Produces: image `ghcr.io/jeulinmarc/cb_corpus:latest` (+ tag sha) à chaque push master hors `data/**` ; composes à placeholders `POOL/DATASET/PUID/PGID` ; runbook complet.

- [ ] **Step 1: Écrire le workflow**

`.github/workflows/docker-image.yml` :
```yaml
name: docker-image

on:
  push:
    branches: [master]
    paths-ignore:
      - "data/**"     # les auto-commits d'état du NAS ne doivent PAS rebuilder
      - "docs/**"
      - "**.md"
  pull_request:
    paths:
      - "deploy/**"
      - "cb_corpus/**"
      - "requirements.txt"
      - ".github/workflows/docker-image.yml"

permissions:
  contents: read
  packages: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        if: github.event_name == 'push'
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: deploy/Dockerfile
          platforms: linux/amd64
          push: ${{ github.event_name == 'push' }}
          tags: |
            ghcr.io/jeulinmarc/cb_corpus:latest
            ghcr.io/jeulinmarc/cb_corpus:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Écrire les trois composes d'exemple**

`deploy/compose.refresh.example.yml` :
```yaml
# Stack Dockge « cb-refresh » — refresh 12 h + discover hebdo.
# REMPLACER les placeholders (POOL/DATASET/PUID/PGID) dans Dockge UNIQUEMENT.
# NE JAMAIS committer la version remplie : le repo est public.
services:
  cb-refresh:
    image: ghcr.io/jeulinmarc/cb_corpus:latest
    restart: unless-stopped
    user: "PUID:PGID"          # UID:GID de marc sur TrueNAS (stack discover-ids)
    environment:
      TZ: Europe/Paris
      DISCOVER_ARGS: "--banks us,ecb --types A3 --rounds 1"  # périmètre hebdo, à ajuster
      AUTOCOMMIT: "1"
    volumes:
      - /mnt/POOL/DATASET:/app/data              # chemin host du dataset SMB
      - ./deploy_key:/run/secrets/deploy_key:ro  # clé posée dans le dossier du stack
    # mem_limit: 2g        # décommenter si le NAS est contraint
    # cpus: "2"
```

`deploy/compose.campaign.example.yml` :
```yaml
# Stack Dockge « cb-campaign » — campagne longue à la demande.
# Éditer `command:` (sous-commande cb_corpus complète) puis Deploy.
# Le conteneur attend le lock si un refresh tourne, puis s'arrête à la fin.
services:
  cb-campaign:
    image: ghcr.io/jeulinmarc/cb_corpus:latest
    restart: "no"
    user: "PUID:PGID"
    environment:
      TZ: Europe/Paris
      AUTOCOMMIT: "1"
    command: ["/app/deploy/run-job.sh", "campaign",
              "discover", "--banks", "fr,it", "--types", "A3", "--download", "--rounds", "3"]
    volumes:
      - /mnt/POOL/DATASET:/app/data
      - ./deploy_key:/run/secrets/deploy_key:ro
```

`deploy/compose.discover-ids.example.yml` :
```yaml
# Stack JETABLE : découvre le chemin host du dataset SMB et l'UID/GID de marc.
# Deploy -> lire les logs -> noter les valeurs -> SUPPRIMER le stack.
services:
  probe:
    image: busybox
    restart: "no"
    command: >
      sh -c "echo '=== datasets sous /mnt ==='; ls -lnR /host-mnt 2>/dev/null | head -80;
             echo '=== reperer: le dataset du partage SMB, et uid/gid proprietaire ==='"
    volumes:
      - /mnt:/host-mnt:ro
```

- [ ] **Step 3: Écrire le runbook `deploy/README.md`**

````markdown
# Déploiement NAS (Dockge) — runbook

Spécification : `docs/superpowers/specs/2026-07-12-nas-docker-deploy-design.md`.
Règle absolue : **aucune valeur d'infra réelle** (IP, hostname, chemins /mnt réels,
UID) ne doit être commitée — les valeurs vivent dans Dockge et dans des notes
locales non versionnées (`*.local.md`).

## 0. Prérequis (une fois)

1. **Deploy key** (sur le Mac) :
   `ssh-keygen -t ed25519 -f nas_deploy_key -N "" -C "cb-corpus-nas-state"`
   GitHub → repo → Settings → Deploy keys → « Add deploy key », coller
   `nas_deploy_key.pub`, **cocher "Allow write access"**.
2. **Visibilité GHCR** : après le premier build CI, GitHub → profil → Packages →
   `cb_corpus` → Package settings → Change visibility → **Public**
   (sinon le NAS ne peut pas puller sans authentification).

## 1. Découverte chemins/UID (stack jetable)

Dockge → nouveau stack `cb-probe` → coller `compose.discover-ids.example.yml`
→ Deploy → lire les logs : noter le chemin `/mnt/<pool>/<dataset>` du partage
SMB et l'UID/GID propriétaire (fichiers créés par marc via SMB). Supprimer le stack.

## 2. Seed initial (OBLIGATOIRE avant le premier run)

Sans seed, le premier run re-téléchargerait ~38 000 documents et l'état
Wayback non re-crawlable serait perdu. Depuis le Mac, partage SMB monté :

```bash
# adapter la destination au partage monté dans le Finder
DST="/Volumes/<share>/<chemin_dataset>"
rsync -rt --progress "data/manifest" "$DST/"
rsync -rt --progress "data/wp_dates_index.jsonl" "$DST/"
rsync -rt --progress "data/raw" "$DST/"      # 8,2 GB — plusieurs heures

# vérification d'intégrité (les comptes doivent être identiques)
find data/raw -type f | wc -l
find "$DST/raw" -type f | wc -l
ls data/manifest/*.jsonl | wc -l
ls "$DST/manifest/"*.jsonl | wc -l
```

Après le seed : le Mac **cesse de crawler** ; son `data/` devient une archive
(ne pas supprimer sans décision explicite).

## 3. Stack `cb-refresh`

Dockge → nouveau stack `cb-refresh` → coller `compose.refresh.example.yml` →
remplacer `POOL/DATASET/PUID/PGID` → déposer la clé privée `nas_deploy_key`
dans le dossier du stack sous le nom `deploy_key` (éditeur de fichiers Dockge)
→ Deploy.

## 4. Stack `cb-campaign` (à la demande)

Dockge → stack `cb-campaign` → coller `compose.campaign.example.yml` →
remplacer les placeholders et la ligne `command:` → Deploy. Le conteneur
attend la fin d'un éventuel refresh (lock), exécute, pousse l'état, s'arrête.
Relancer une autre campagne = rééditer `command:` + Deploy.

## 5. Vérifications de bon fonctionnement

- `data/reports/nas_runs.log` et `last_run_status` visibles dans le Finder (SMB).
- Un PDF récent apparaît sous `raw/<bank>/...` dans le Finder.
- Un commit `data: NAS refresh <date>` apparaît sur GitHub après un run utile.
- Les fichiers créés par le conteneur t'appartiennent via SMB (sinon revoir PUID/PGID).

## 6. Mise à jour du code

Push sur master → CI rebuilde `ghcr.io/.../cb_corpus:latest` → dans Dockge :
re-pull de l'image + redéploiement des stacks.
````

- [ ] **Step 4: Valider la syntaxe des composes et du workflow**

Run:
```bash
for f in deploy/compose.*.example.yml; do docker compose -f "$f" config -q && echo "OK $f"; done
python3.13 -c "import yaml" 2>/dev/null && python3.13 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/docker-image.yml')); print('OK workflow')" || echo "(pas de pyyaml — validation workflow via la CI de la PR)"
```
Expected: `OK` pour les trois composes (le workflow sera validé par le run PR).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/docker-image.yml deploy/compose.refresh.example.yml deploy/compose.campaign.example.yml deploy/compose.discover-ids.example.yml deploy/README.md
git commit -m "feat(deploy): CI GHCR, composes d'exemple (placeholders) et runbook NAS"
```

---

### Task 5: PR + CI réelle

**Files:** aucun nouveau — push, PR, vérification du run.

- [ ] **Step 1: Relancer TOUS les tests une dernière fois**

Run: `bash tests/deploy/run_tests.sh && python3.13 -m pytest tests/ -x -q`
Expected: `ALL_DEPLOY_TESTS_OK` + suite pytest existante verte (aucune régression — ce chantier ne touche pas au code du crawler).

- [ ] **Step 2: Push + PR**

```bash
git push -u origin nas-docker-deploy
gh pr create --title "Deploy: cb_corpus sur NAS via Docker/Dockge (GHCR, refresh 12h, campagnes)" \
  --body "Packaging Docker + CI GHCR + runbook Dockge. Spec: docs/superpowers/specs/2026-07-12-nas-docker-deploy-design.md. Aucune valeur d'infra réelle commitée (placeholders + hook pre-commit local). Aucun code crawler touché."
```

- [ ] **Step 3: Vérifier le VRAI run CI (jamais annoncer une CI verte sans l'avoir vue)**

Run: `gh pr checks --watch` puis `gh run list --limit 3`
Expected: le job `docker-image` de la PR passe (build sans push). Ne merger qu'après run vert constaté.

---

### Task 6: Mise en service NAS (manuel, guidé — post-merge)

**Files:** aucun commit — opérations Dockge/SMB, suivant `deploy/README.md`.

- [ ] **Step 1:** Merge de la PR → vérifier que le run master pousse bien `ghcr.io/jeulinmarc/cb_corpus:latest` (`gh run watch`), puis passer le package GHCR en **Public**.
- [ ] **Step 2:** Deploy key : génération, ajout GitHub (write), dépôt dans le dossier du stack.
- [ ] **Step 3:** Stack jetable `cb-probe` → noter chemin host + UID/GID (dans une note locale non versionnée) → supprimer le stack.
- [ ] **Step 4:** Seed 8,2 GB via SMB + vérification des comptes de fichiers (runbook §2).
- [ ] **Step 5:** Stack `cb-refresh` → Deploy → premier run : suivre `nas_runs.log` via le Finder.
- [ ] **Step 6:** Vérifications finales : PDF récent visible en SMB, commit `data: NAS refresh <date>` sur GitHub, fichiers possédés par marc. Ensuite seulement : déclarer le Mac hors du circuit de crawl.
