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
remplacer les placeholders et la ligne `command:` → déposer la clé privée `nas_deploy_key`
dans le dossier du stack sous le nom `deploy_key` → Deploy. Le conteneur
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
