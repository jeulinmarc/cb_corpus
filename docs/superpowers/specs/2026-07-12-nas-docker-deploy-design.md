# Design — Déploiement cb_corpus sur le NAS (TrueNAS + Dockge)

**Date :** 2026-07-12
**Statut :** approuvé (brainstorming du 2026-07-12)
**Objectif :** faire tourner le crawler cb_corpus sur le NAS familial, sans laisser le Mac
allumé, avec les fichiers écrits sur le dataset ZFS servi en SMB — déployé via Dockge,
sans SSH ni accès admin TrueNAS.

## 1. Contexte et contraintes

- NAS TrueNAS familial, accessible en local et via Tailscale ; déploiement via l'UI Dockge.
  Pas d'accès admin ni SSH pour Marc. Les détails d'infra (IP, hostnames, ports, comptes)
  sont volontairement hors de ce document : voir `nas-infra.local.md` (non versionné —
  le repo est public, aucun détail d'infra ne doit jamais être commité).
- Repo public : <https://github.com/jeulinmarc/cb_corpus>. Python 3.13 requis
  (3.14 casse les deps). Chromium requis pour la conversion HTML→PDF
  (`htmlpdf.py` cherche `google-chrome`/`chromium`/`chromium-browser`/`chrome` dans le PATH ;
  le paquet Debian `chromium` convient — aucun patch code nécessaire).
- État existant sur le Mac : `data/` = **8,2 GB, ~64 000 fichiers** (`data/raw/` + manifests).
  Les manifests `data/manifest/*.jsonl` et `data/wp_dates_index.jsonl` sont versionnés dans
  git et **partiellement non reconstructibles par re-crawl** (dates récupérées via Wayback).

## 2. Décisions actées

| Question | Décision |
|---|---|
| Rôle du NAS | Les deux : refresh planifié **et** campagnes longues à la demande (scaling A–F) |
| Writer de l'état | **NAS unique writer** ; le Mac cesse de crawler après le seed |
| Retour de l'état dans git | **Auto-commit** depuis le conteneur (deploy key en écriture) |
| Cadence refresh | **Toutes les 12 h** (`bis-sitemap --download` + `repec --download`) + **discover hebdo** |
| Livraison de l'image | **GHCR via GitHub Actions** (gratuit et illimité, repo public) |
| Campagnes à la demande | **2ᵉ stack Dockge** (`command:` édité dans l'UI, `restart: "no"`) |

Cible « toutes les 5–10 min » : notée comme évolution future, **hors scope**. Elle exigera un
fast-path incrémental (diff de sitemap seul, sans passe RePEc) — chantier code séparé. La
cadence reste un paramètre (crontab éditable), pas une constante.

## 3. Architecture

Une **image unique** GHCR, **deux stacks Dockge**, **un volume partagé**
(`/mnt/<pool>/<dataset>` monté sur `/app/data`) :

### Stack `cb-refresh` (permanent)

- `restart: unless-stopped` ; le process principal est **supercronic** (cron statique
  container-friendly) avec deux entrées :
  - `0 */12 * * *` → refresh : `bis-sitemap --download` puis `repec --download` ;
  - `0 3 * * 0` → discover hebdo : arguments dans l'env var **`DISCOVER_ARGS`**
    (banques/types/rounds), éditable dans Dockge — paramètre configurable, jamais en dur.

### Stack `cb-campaign` (à la demande)

- `restart: "no"` ; Marc édite `command:` dans l'UI Dockge (ex.
  `discover --banks fr,it --types A3 --download --rounds 3`) puis Deploy ; le conteneur
  s'arrête en fin de campagne. Logs dans Dockge.

### Verrou anti-collision

- `flock` global sur `/app/data/.cb.lock` (le volume est du ZFS local côté NAS — flock fiable,
  les deux conteneurs partagent le même kernel).
- Le refresh **saute son tour** si le lock est pris (et le journalise) ; une campagne tient le
  lock toute sa durée. Conséquence assumée : une campagne de plusieurs jours suspend les
  refresh (poli vis-à-vis des hôtes).

## 4. Image (Dockerfile + CI)

- Base `python:3.13-slim` + `chromium` + `git` + `rsync` + binaire `supercronic` ;
  `pip install -r requirements.txt` ; code copié dans `/app`.
- GitHub Action : build + push `ghcr.io/jeulinmarc/cb_corpus:latest` à chaque push sur
  master. Plateforme `linux/amd64` (à confirmer au déploiement que le NAS est x86).
- Mise à jour du code sur le NAS = re-pull dans Dockge.

## 5. Auto-commit de l'état

Après chaque run **réussi** (refresh ou campagne — toujours sous lock, donc jamais
concurrent) :

1. clone shallow du repo (ou pull si déjà présent) ;
2. copie des `data/manifest/*.jsonl` + `data/wp_dates_index.jsonl` du volume vers le
   checkout ;
3. si diff non vide : commit `data: NAS <run-type> <date UTC>` — **sans co-author Claude** ;
4. `git pull --rebase` puis push via **deploy key** (clé SSH dédiée au repo, droits write,
   stockée dans le dossier du stack Dockge ; seul secret sur le NAS, révocable d'un clic).

Le Mac récupère l'état par simple `git pull`. Seuls les JSONL versionnés sont commités.

## 6. Permissions et chemins (découverte sans le frère)

- Conteneurs lancés avec `user: "<UID>:<GID>"` de `marc` sur TrueNAS, pour que les fichiers
  soient lisibles/supprimables via SMB.
- UID/GID et chemin host `/mnt/<pool>/<dataset>` découverts via un **stack Dockge jetable**
  qui monte `/mnt` en lecture seule et exécute `ls -ln` (puis supprimé).

## 7. Seed initial (étape 0, bloquante)

- Copier les 8,2 GB (`data/raw/`, `data/manifest/`, `data/wp_dates_index.jsonl`) du Mac vers
  le dataset via le montage SMB (`rsync` local → montage), avec vérification des comptes de
  fichiers avant/après.
- Sans seed, le premier run re-téléchargerait ~38 000 documents (des semaines, impoli) et
  l'état Wayback non re-crawlable serait perdu du nouvel état.
- Après le seed, le `data/` du Mac devient une archive morte — **pas de suppression sans
  accord explicite** (règle git-workflow).

## 8. Observabilité

- Chaque run écrit une ligne horodatée (début/fin/statut/compteurs) dans
  `data/reports/nas_runs.log` + met à jour `data/reports/last_run_status` — visibles depuis
  le Finder via SMB.
- Logs détaillés dans Dockge. Option future hors scope : ping healthchecks.io.

## 9. Paramètres laissés configurables (non tranchés)

- `parallel_hosts=10` conservé par défaut ; ajustable si le NAS souffre (limites CPU/RAM du
  conteneur posables dans le compose).
- Périmètre initial du `DISCOVER_ARGS` hebdo : à choisir au premier déploiement.

## 10. Tests et validation

1. Build local de l'image si Docker est disponible sur le Mac (sinon via l'Action).
2. Smoke test : `list-banks` dans le conteneur.
3. Refresh d'essai sur un `data/` temporaire (hors volume réel) — vérifier PDF + manifest +
   log de run.
4. Déploiement réel : seed → stack jetable (UID/chemins) → `cb-refresh` → vérifier qu'un
   PDF nouvellement téléchargé apparaît dans le Finder (SMB) et qu'un commit d'état arrive
   sur GitHub.

## 11. Hors scope explicite

- Fast-path incrémental 5–10 min (chantier code futur).
- Alerting externe (healthchecks.io).
- Toute modification du cœur du crawler : ce chantier n'ajoute que du packaging
  (Dockerfile, compose, entrypoints/scripts shell, workflow CI).
