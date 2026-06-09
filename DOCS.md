# Index de la documentation — `cb_corpus`

## Docs du repo (commités)
| Doc | Pour qui | Contenu |
|---|---|---|
| **README.md** | tout dev | **Point d'entrée** : vue d'ensemble, install/test, run, « before a real run », complétude, layout |
| **INGESTION_RAG.md** | repo RAG | **Contrat de sortie / handoff** : schéma du manifest, filtres, citation, caveats. Le store requêtable (SQLite/vecteurs) est côté repo RAG |
| **BANKS.md** | dev | Checklist des 63 banques + colonne adaptateur + statut natif |
| **central_bank_corpus_inventory.md** | planif | Doc de planification d'origine (taxonomie, volumes estimés) — bannière « as-built » |
| **DOCS.md** *(ce fichier)* | tout le monde | Carte de la doc |

## Données (régénérables — **non commitées**)
- **`CORPUS.md`** — inventaire (totaux, par pays/type, schéma du manifest) : `python gen_corpus.py`
- **`PRESENTATION(.en).md`** + **`.pptx`** — présentations FR/EN : `python build_slides*.py`

## Notes de travail locales (**gitignorées, hors repo**)
Analyses/audits gardés en local (hors commits) :
`ARCHITECTURE_REVIEW.md`, `ARCHITECTURE_ALTERNATIVES.md`, `DISCOVERY_AUDIT.md`,
`COMPLETENESS.md`, `REFACTORING.md`, `ECB_DOC_TYPES.md`, `RUNBOOK.md` (son contenu opérationnel
utile a été **fusionné dans `README.md`**).

## Source de vérité (quand les docs divergent)
1. **Le code** + les **tests** (`tests/`, 85 tests : `test_framework.py` + `test_recovery.py`).
2. **`CORPUS.md`** (régénéré) pour les chiffres.
3. **`README.md`** pour l'architecture as-built.
